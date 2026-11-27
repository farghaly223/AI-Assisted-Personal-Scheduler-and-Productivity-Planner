# Energy Module Integration & Ownership Guide

## Table Ownership

The `app.modules.energy` module is the sole owner of the following `public` tables:

- `sleep_schedules`
- `sleep_sessions`
- `sleep_usage_signals`
- `sleep_quality_ratings`
- `recovery_strategies`
- `focus_sessions`
- `recovery_periods`
- `energy_scores`
- `energy_score_revisions`
- `energy_feedback`

### `focus_sessions` Ownership

Energy is the **single owner** of `focus_sessions`. Other backend modules must **never** import `FocusSession` or query `public.focus_sessions` directly; they must use the read-only internal interface `get_focus_summary` documented below.

## Internal Interfaces (Cross-Module)

All internal interfaces live under `app.modules.energy.services`, run read-only `SELECT` queries, and strictly filter by the verified `user_id`:

1. **`get_focus_summary(session, user_id, date, timezone="UTC") -> dict`**
   - Location: `app.modules.energy.services.recovery`
   - Returns:
     ```python
     {
         "completed_sessions": int,
         "total_focus_minutes": int,
         "in_progress": bool,
     }
     ```
   - Aggregates focus sessions whose `start_time` falls within the DST-safe UTC bounds of the local `date` in `timezone`.

2. **`get_daily_workload(session, user_id, target_date=None, timezone_name="UTC", *, date=None, timezone=None) -> dict`**
   - Location: `app.modules.energy.services.energy`
   - Computes live daily workload metrics using `available_minutes` derived from the user's active `sleep_schedule` (when `target_date.weekday()` is in `repeat_days`, clamped to `600..1200`, fallback `960`).
   - `fixed_event_duration` includes the merged interval union of calendar events and `FIXED` tasks (preventing double counting).
   - `total_task_duration` and `completed_task_duration` cover `FLEXIBLE` tasks only.

3. **`get_current_energy(session, user_id) -> dict | None`**
   - Location: `app.modules.energy.services.energy`
   - Returns the latest stored energy score summary (`score`, `level`, `confidence`, `calculated_at`) or `None` if no score exists. Returns `None` for `score`, `level`, and `confidence` when `status == "insufficient_data"`.

## Scheduler Adapter Coupling

Scheduler coupling is isolated strictly to `app/modules/energy/services/scheduler_adapter.py`:

- It is the **only** file in `energy` permitted to import `Task` and `Event` from `app.modules.scheduler.models`.
- It executes only read-only `SELECT` queries filtered by `user_id` with bounded row limits (`limit + 1`, setting `truncated=True` if exceeded).
- Titles, locations, and personal text are never read into energy objects.
- `FIXED` tasks overlapping the local day are converted to clipped `[day_start, day_end)` intervals and unioned with `Event` intervals so overlapping time is counted once. `FLEXIBLE` tasks with deadlines inside the local day remain `TaskLoad` entries.
