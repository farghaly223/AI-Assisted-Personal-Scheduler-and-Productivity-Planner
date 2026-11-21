# Scheduler module: handover

Author: Radwa. Scope: the Scheduler algorithms and API on top of the DAYLI final database (migration `0003_dayli_foundation`).
Nothing here touches migrations, models or the Task/Event API. All code lives in `app/modules/scheduler/` plus two small edits to shared files (listed below).

## Read this first

* **Everything in `algorithms/` is pure Python and is covered by unit tests that pass locally.**
* **None of the endpoints has been run against a real PostgreSQL/Supabase database.** There was no local PostgreSQL and no permission to use the shared database, so every database test in the repo was skipped on the author's machine. The write path (`generate`, `regenerate`, `PATCH/lock/unlock items`) was written by reading the `0003` triggers line by line, but it **must be exercised against a database before it is trusted**. See "How to verify".
* The existing full test run on the author's machine: all new tests pass; the only failure is `test_final_revision_is_additive_and_applied_migrations_are_frozen`, which also fails without these changes. The SHA-256 of `0001_sprint1_foundation.py` is correct once CRLF line endings are normalised to LF (Windows checkout, `core.autocrlf`). It is not caused by this work.

## What was added

### Algorithms (`app/modules/scheduler/algorithms/`)

| File | Responsibility |
|---|---|
| `time_intervals.py` | `Interval`, merge and subtract intervals |
| `availability_calculator.py` | Expand weekly `availability_windows` into concrete intervals (timezone aware, overnight windows, `valid_from/until`) and subtract busy time |
| `busy_calculator.py` | Expand events (`ONCE` and `WEEKLY` rules, overnight via day offset) and FIXED tasks into concrete busy blocks |
| `schedule_validator.py` | Overlap, deadline and availability checks; `is_valid` |
| `task_allocator.py` | Greedy earliest-deadline-first placement of FLEXIBLE tasks (ties: priority, then longer duration); honours `can_split` and `minimum_session_minutes`; all-or-nothing per task, with a reason code when a task cannot be placed |
| `schedule_builder.py` | Task filter, build, analyze (statistics). Never schedules in the past |
| `persistence_planner.py` | Carry-over of locked/completed placements, `logical_key` reuse, omission rows for partial allocations, window widening |
| `schedule_health.py` | Checks placements against real commitments (used by `/validate` and `/conflicts`) |

### Services (`services/`)

`availability_service.py`, `planning_service.py` (shared read + plan pipeline), `persistence_service.py` (draft, items, omissions, issues, publish; manual revisions), `generation_service.py`, `schedule_read_service.py`, `analysis_service.py`.

### Endpoints (all under `/api/v1`, bearer token required)

| Method | Path | Purpose |
|---|---|---|
| GET | `/scheduler/availability?start&end&minimum_minutes` | Free slots plus the busy blocks that were subtracted |
| POST/GET/GET/PATCH/DELETE | `/scheduler/availability-windows[/{id}]` | CRUD of weekly hard availability windows (0 = Monday ... 6 = Sunday). The table is owned by the User module (`owner_module: user`); it is here only because no endpoint existed. Move it if the User module owner prefers |
| POST | `/scheduler/generate/preview` | Build a schedule for a range, save nothing |
| POST | `/scheduler/generate` | Create and publish the first schedule version (409 if one exists) |
| POST | `/scheduler/regenerate` | New version from the current one (409 if none exists). Locked placements and completed tasks are kept |
| GET | `/scheduler/current` | Current version with items and unscheduled tasks |
| GET | `/scheduler/daily?date=` | One local day (user timezone from preferences) |
| GET | `/scheduler/weekly?week_start=` | Seven local days |
| PATCH | `/scheduler/items/{id}` | Move one flexible placement (new MANUAL version, explanation required) |
| POST | `/scheduler/items/{id}/lock`, `/unlock` | Lock/unlock a flexible placement (new MANUAL version) |
| POST | `/scheduler/validate` | Validate a proposed list of placements |
| GET | `/scheduler/conflicts` | Is the published schedule still valid against today's events, tasks, deadlines and availability? Reports `COMMITMENT_CHANGED` and `NEW_COMMITMENT` |
| GET | `/scheduler/statistics` | Statistics of the current version |

### Shared files edited (please review)

* `app/modules/scheduler/router.py`: registers the new child routers.
* `app/core/openapi.py`: adds examples for the new operations (the repo test `test_every_api_operation_has_documented_examples` requires them).

### Tests

`tests/test_scheduler_intervals.py`, `_availability.py`, `_busy.py`, `_validator.py`, `_allocator.py`, `_builder.py`, `_builder_extra.py`, `_persistence_planner.py`, `_health.py`. All pure unit tests; no database needed.

## Design decisions to confirm

1. **`tasks.priority` direction.** Assumed 5 = most important. One constant: `HIGHER_PRIORITY_NUMBER_IS_MORE_IMPORTANT` in `task_allocator.py`.
2. **Weekday convention** of `availability_windows.weekday` is 0 = Monday (confirmed in `docs/database-migrations.md`). Constant `MONDAY_IS_ZERO` in `availability_calculator.py`.
3. **Completed/skipped tasks** come from the latest `task_outcomes` row per task (`COMPLETED`, `SKIPPED` are not planned again; `REOPENED` makes a task schedulable). `tasks.status` is always `UNSCHEDULED`. FIXED tasks stay in the schedule even when completed, because the database requires their placement to be preserved.
4. **Published versions are immutable** (database rule), so every manual change creates a new `MANUAL` version and item ids change with each version.
5. **Version window** is widened to contain every item (events and carried placements must fit inside it). The requested range only limits where flexible tasks may be placed.
6. **Partial allocation:** the allocator is all-or-nothing per task, but carried-over locked or completed sessions can add up to less than the task estimate. The database then requires an omission row, so one with reason `PARTIAL_ALLOCATION` is created.
7. **Not implemented on purpose:** Decision Engine scoring, energy/attention inputs, `preferred_start_time/preferred_end_time` from `task_requirements`, BREAK items, buffers between tasks, `decision_execution_id`, and the Module 5 `rescheduling_*` tables. The allocator is deliberately simple (as agreed in the Scheduler design) so Decision Engine can replace the ordering later.

## Known risks (please test these on a database)

* **Adjacent availability windows** (for example 09:00-12:00 and 12:00-17:00): the code merges touching windows, but the publish trigger checks each flexible placement against a single window row. A task crossing the boundary would be rejected with a 409.
* **DST gaps/folds** for weekly events: the placement trigger recomputes the expected occurrence with `dayli_resolve_local_time`; Python uses `zoneinfo`. They may disagree on a nonexistent or ambiguous local time.
* **Window widening** loops at most 4 times; very long overnight chains could return 409 "window could not be stabilised".
* Database errors during saving are returned as 409 with the trigger's primary message. Check that the message is useful and does not leak anything.
* Flexible tasks whose source changed after the previous version are not carried over (deadline check only). Other snapshot differences are left to the database.
* `ScheduleVersion` rows inserted as drafts are published in the same request; there is no endpoint that leaves a draft open.

## How to verify (suggested order, against a disposable database)

1. Run the repo with PostgreSQL configured so the skipped database tests execute.
2. Create a profile, then `POST /scheduler/availability-windows` (for example Monday 09:00-17:00, `Africa/Cairo`).
3. Create one `ONCE` event and one weekly event, two FLEXIBLE tasks (one with a `task_requirements` row, `can_split = true`), and one FIXED task.
4. `GET /scheduler/availability`, then `POST /scheduler/generate/preview` for a Monday, and compare with expectations.
5. `POST /scheduler/generate`, then `GET /scheduler/current`, `/daily`, `/weekly`, `/statistics`.
6. Lock a placement, move another, then `POST /scheduler/regenerate` and confirm the locked one is unchanged.
7. Insert a `task_outcomes` row (`COMPLETED`) and regenerate: the completed placement must be kept.
8. Edit an event so it overlaps a placed task and call `GET /scheduler/conflicts`.
9. Log in as a second user and confirm none of the above is visible (RLS and owner filters).

## Questions for the author of the User and Decision Engine modules

* Who owns the availability-windows endpoints?
* Will Decision Engine call `compute_plan` / the allocator, or write `decision_executions` itself? `planning_service.compute_plan` is the natural integration point.
