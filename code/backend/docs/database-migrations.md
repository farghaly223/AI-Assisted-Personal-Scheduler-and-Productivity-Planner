# DAYLI final database: deployment, ownership and security

Current Supabase revision: **0002_energy**, confirmed by the project owner. Final code revision: **0003_dayli_foundation**. There are 31 application tables: 12 preserved and 19 new. One database, one shared SQLAlchemy Base and one Alembic history remain. No Supabase connection or deployment was performed.

## Manual deployment

Run [upgrade_from_0002_energy.sql](../review/upgrade_from_0002_energy.sql) once in Supabase SQL Editor using migration/admin access. This is the only new deployment SQL artifact, following the latest instruction to continue from Sprint 2. The original Sprint 1/Energy migrations and their historical review files are preserved. Do not rerun their creation scripts.

Retain a backup and check `SELECT version_num FROM public.alembic_version;` first; it must return exactly one row, `0002_energy`. The upgrade checks its starting revision and conflicting target objects and runs in one transaction. Any error rolls back all changes; review the named constraint rather than resetting data or stamping past an error.

Afterward, the revision must be `0003_dayli_foundation`. Check `SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname='public' ORDER BY tablename;`: all 31 application tables must have RLS enabled. Unrelated Supabase objects are outside this application inventory.

Export verification is local and does not connect: `python -B -m scripts.export_schema --check`. Omitting `--check` regenerates the same one upgrade file. Fresh disposable installations still follow the existing Alembic chain with `alembic upgrade head`; no separate SQL export or history is needed.

## Required artifacts

- [Complete ERD](../../../digrams/masterclass/dayli_final_database.puml): all tables, UUID PKs, 71 FKs, cardinality and module ownership.
- [Data dictionary](database-data-dictionary.md): all 369 columns, types, nullability, defaults, keys, checks, indexes, triggers and relationships.
- [Incremental migration](../alembic/versions/0003_dayli_foundation.py): frozen SQL, `down_revision = "0002_energy"`, no application model imports.
- [Complete ORM discovery](../app/core/schema_models.py): the existing Base, with models in their owning module folders.
- [Applied Sprint 1](../alembic/versions/0001_sprint1_foundation.py) and [Energy](../alembic/versions/0002_energy_tables.py) migrations remain byte-for-byte unchanged.
- Ownership, security and final integration report are consolidated in this existing guide.

## Ownership and integration

| Owner | Tables | Writers and consumers |
|---|---|---|
| User | `profiles`, `user_preferences`, `availability_windows` | User services write; other modules read relevant preferences/availability through User interfaces. |
| Scheduler | `tasks`, `events`, `task_requirements`, `task_outcomes`, `schedules`, `schedule_versions`, `schedule_items`, `unscheduled_tasks`, `schedule_validation_issues` | Scheduler owns inputs, feedback, validation and publication. Decision reads constraints; Rescheduling requests new drafts/publication through Scheduler. Energy/Attention consume relevant task/placement context. |
| Energy | `sleep_schedules`, `sleep_sessions`, `sleep_usage_signals`, `sleep_quality_ratings`, `recovery_strategies`, `recovery_periods`, `energy_scores` | Energy writes configuration, sleep/recovery observations and energy calculations. Decision/Rescheduling consume exact score versions and confidence. |
| Attention | Shared `focus_sessions`, `usage_sync_batches`, `app_classifications`, `app_usage_summaries`, `attention_assessments`, `attention_questionnaire_responses`, `distraction_events`, `attention_scores` | Attention owns focus, usage, questionnaire and distraction/attention data. Energy consumes Focus and shared batches through Attention interfaces. Decision/Rescheduling consume score versions. |
| Decision Engine | `decision_executions`, `decision_candidates` | Decision writes evaluations, components, alternatives and explanations; Scheduler/Rescheduling consume executions. |
| Dynamic Rescheduling | `rescheduling_runs`, `rescheduling_changes` | Rescheduling owns triggers/results/affected-item history and calls Decision/Scheduler; it does not overwrite their tables. |

Every column belongs to its table's owning module. Pass the existing verified Session between services in one transaction. One shared DB credential cannot distinguish Python packages: RLS enforces users; interfaces/reviews enforce module write ownership. Never create another Base, engine, database, or independent history.

Shared entities stay singular. Supabase `auth.users` authenticates; `profiles.id` references it. Every other table has non-null `user_id → profiles.id`; same-user guards protect additional references and IDs/owners are immutable. Focus is one existing table/class, now owned by Attention and reexported by Energy. `usage_sync_batches` is shared rather than duplicated as a sleep batch table. Tasks and Events are distinct: lectures/shifts are Events; performing fixed-time work is a Fixed Task. Historical snapshots retain necessary editable/deletable inputs; Scheduler has no duplicate Energy/Attention score columns.

## Preserved contracts and schema changes

All 44 original core columns, core checks/indexes and 21 API operations are preserved. Task description is `context`, priority is 1–5, type and category remain independent, and original status remains `UNSCHEDULED`. Completion/skip/reopen state is projected from append-only `task_outcomes`; placement belongs to a version. Event source/status remain `MANUAL`/`SAVED`. No future module APIs or scoring algorithms were implemented.

| Existing table | Intentional extension/backfill |
|---|---|
| `sleep_schedules` | Add strict unique/non-null 0–6 weekday validation; invalid legacy arrays fail without silent normalization. |
| `sleep_sessions` | Keep original intervals/duration. Add confirmed/corrected endpoints/time/method, detector version and batch FK; add corrected status. Only already-confirmed rows get LEGACY confirmation endpoints, with original updated_at as an explicit legacy time proxy. Pending estimates stay pending. Confidence becomes nullable with no default certainty. |
| `sleep_usage_signals` | Add installation/client event UUIDs, batch FK and expiry. Legacy device=user_id, client event=id, expiry=original created_at+7 days; replace coarse event-time uniqueness with user/device/client-event uniqueness. |
| `focus_sessions` | Preserve table/IDs/imports; add planned duration, task/placement FKs and client key. Planned/cancelled plans can have no actual interval. |
| `recovery_periods` | Add planned/observed fields, status and Focus/strategy/placement/client links. Existing PLANNED sources become planned-only; other rows become observed TAKEN. Old non-null interval/duration fields are trigger-maintained projections. |
| `energy_scores` | Replace one-per-day uniqueness with user/calculation-key uniqueness. Add versions, snapshot, explanation, timezone and sleep FK; old key=id, version=legacy_unversioned, timezone=NULL. Preserve original scores/components/timestamps. New calculations are append-only with explicit provenance; confidence becomes nullable. |

Backfills temporarily disable only owned updated_at triggers within the transaction so original timestamps remain intact. The upgrade deletes no existing entities or observations. `btree_gist` supplies atomic interval exclusion and preserves an existing Supabase extension installation.

## Scheduling and history

One schedule container per profile holds its current version; daily/weekly/monthly are views. Build a DRAFT and populate items, omissions and issues. Publication locks the schedule, requires parent=current, validates source snapshots, anchors/locks, window/deadline/splitting/allocation/availability constraints and ERROR issues, then fills published_at and updates the pointer through its trigger. A linked Decision must have succeeded against the exact parent. Stale drafts/direct pointer changes fail.

Half-open intervals cannot overlap within a version, including concurrent inserts; adjacent intervals are allowed. Invalid proposals can be recorded as Decision candidates or validation details. Published content is immutable. Presented rescheduling proposals freeze their drafts; publish unchanged or reject. Rejected/failed proposal content remains frozen. Manual adjustments use a new version and explanation. Automatic changes preserve unchanged locked/completed placements inside the new window; explicit source edits/deletion and recorded manual changes are documented exceptions.

Rescheduling records exact same-user/schedule previous/new versions and exact old/new items. MOVED preserves logical key, kind, task/event identity and weekly date. Finished runs/changes freeze; failed runs can reference failed Decision executions. Publish and mark APPLIED in one transaction so the resulting version is current. Late/offline completion feedback requires a subsequent rescheduling decision.

Optional source FKs use SET NULL with retained snapshots, preserving existing Task/Event DELETE APIs. Required history links use deferred NO ACTION where account-cascade ordering needs it. Whole-account deletion via Supabase Auth cascades through profiles to all application data. Parent/content locks serialize publication and edits; allocate version numbers under the schedule lock and retry whole transactions after applicable concurrency conflicts. Existing input PATCH concurrency behavior remains unchanged.

## Calendar, sleep and attention assumptions

Weekly Events use their first-date weekday, local clocks, end-day offset, timezone and inclusive until date. Resolve both endpoints separately; never add 168 UTC hours. Python/database guards reject gaps/folds, including non-hour transitions. Keep timezone data aligned; published absolute intervals remain historical when user preferences or timezone rules change.

Availability/sleep days use 0=Monday..6=Sunday and refer to the interval's start day; overnight endpoints wrap forward. Convert the Event API's ISO 1–7 convention explicitly. Sleep date is the local wake-up date; one main non-rejected session/date is preserved. Phone inactivity remains an estimate until explicitly accepted/corrected. Manual entry stays available: correct the pending session or reject it before inserting a replacement main session. User sleep quality is only 1–5; absent rating means NULL.

Energy/Attention/component scores are 0–100; confidence/completion rate are 0–1. Missing values remain NULL. New Energy inputs cannot use unconfirmed/rejected sleep or invented quality; unversioned legacy Energy is not a validated Decision measurement. No scientific weights, thresholds, questionnaire questions or optimization/detection algorithms were invented. Versioned bounded snapshots support future approved algorithms. Completed questionnaire records freeze. Distractions retain PROBABLE/CONFIRMED/IGNORED review state; an app switch alone is not proof.

## Synchronization and retention

Use installation UUIDs, not hardware/advertising identifiers. Preserve client batch/event/focus/feedback/calculation/request UUIDs across retries. A new cumulative revision increases per device/local-day/timezone; processed batches/summaries freeze. Consume the latest processed revision for a consistent scoring window; never sum cumulative or overlapping timezone/device snapshots blindly.

Raw sleep signals expire within seven days after ingestion; batches/summaries within 90 days. Restrictive SELECT retention policies hide expired rows even alongside an older broad policy. Physical purge is deliberate administrator maintenance, not an automatic job: delete expired sleep_usage_signals and usage_sync_batches using expires_at<=now() in a reviewed transaction. Batch deletion cascades summaries and nulls optional provenance while confirmed sleep/scores retain snapshots. Keep raw app content, tokens and unnecessary activity logs out of snapshots. Flutter/Android implements reminders/alarms locally.

## Security and runtime setup

| Principal | Access |
|---|---|
| Migration/admin credential | Reviewed DDL/grants/maintenance/account administration; separate from runtime. |
| authenticated | Own-row reads and original column-scoped core CRUD; profile deletion and preference insertion/deletion remain ungranted. |
| dayli_app_writer | NOLOGIN group with named column-scoped feature DML, no Auth-table grants/passwords/schema creation/app ownership; score measurements are not updateable. |
| Dedicated backend LOGIN | INHERIT authenticated+writer; NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE, NOREPLICATION, and no app/schema/function ownership. |
| anon/PUBLIC | No application table privileges or callable privileged trigger functions. |
| postgres/owners/service_role/admin/BYPASSRLS | Never ordinary user CRUD; the existing service key is isolated to private Auth Admin lookup. |
| Team developers | Verified-user production API access; privileged development DDL belongs to disposable local databases, not unrestricted production accounts. |

All 31 tables have SELECT/INSERT/UPDATE/DELETE policies plus a restrictive ALL ownership fence, using `(SELECT auth.uid())`. UPDATE has USING and WITH CHECK and also needs SELECT. Grants reconcile app tables/tracking and creator defaults; ordinary schema CREATE is revoked. Existing Supabase Auth internals remain untouched.

FastAPI verifies JWT signature/issuer/audience/time/subject/role before connection acquisition. user_id comes from that subject. One session.begin() scopes request.jwt.claim.sub/role/claims through set_config(...,true), UTC and row_security=on. Commit/rollback and pool return clear identity; readiness/sessions reject residual identity and accessible admin/owner/DDL roles, even if the old development opt-in is true.

SQL credentials remain trusted backend secrets because their holder can set claims; PostgreSQL does not independently verify a JWT string. Never give runtime/migration passwords or service keys to clients/ordinary team accounts. Authorization does not trust editable user metadata. Supabase Auth owns sessions/account lifecycle.

Provision a restricted production LOGIN through a secured administrative workflow, then grant its existing role membership with `GRANT authenticated, dayli_app_writer TO scheduler_runtime;` (replace the name). No password belongs in deployment SQL/logs. Set DATABASE_URL to that role, keep MIGRATION_DATABASE_URL separate, set the legacy admin opt-in false, restart and run `python -m app.core.database_check`/readiness. The private .env was not rewritten. Direct/verified session-mode 5432 with verified TLS remains supported; transaction pooler 6543 is not. Password-bearing scripts.provision_runtime is local only and fails closed remotely.

## Final integration report and limits

Implemented: required ORM entities, one incremental frozen migration/current SQL export, complete ERD/dictionary, table ownership/service boundaries, RLS/identity/privilege protection, history/concurrency/DST guards and explicit legacy backfills. Existing core APIs are preserved. New tables are exactly those marked new in the ownership inventory/dictionary; Energy adds fields to its existing entities rather than duplicate tables.

Verification is isolated PostgreSQL 17.6 with a test-only Auth stub, never Supabase. It covers complete metadata, all original APIs, seeded 0002 preservation, SQL/Alembic equivalence, frozen applied migrations, all-table RLS/column privileges, cross-user FKs, pooled cleanup, source deletion/account cascades, sleep eligibility, idempotency, questionnaire/usage/proposal immutability, DST and concurrent publication/overlaps. Final full verification: **279 tests passed in 55.67 seconds**, with no warnings. The final targeted deployment/integrity/security run also passed all 27 checks. Applied migration hashes and the single SQL export were verified; the independent final review's history finding was fixed and rechecked.

Remaining owner/module work: manual deployment/restricted credentials; approved instruments/algorithms/input schemas; Android collection/confirmation UI; future module APIs, latest-revision aggregation, recurring generation/conflict handling and retention maintenance. Live schema drift was not remotely inspected, as requested; preflight/local tests protect the expected 0002 contract.

Optional calendar/email/OAuth connections, external mappings/sync history, complex recurrence exceptions/materialized occurrences and older burnout/gym/ML proposals were excluded. Future provider tokens need encryption/secret-manager references. Multiple sleep periods, external Event source values or forecasting inputs require separately approved forward changes. New unknown requirements cannot be promised zero future migrations. 0003 downgrade refuses data-discarding rollback in new/extended modules; use reviewed forward fixes in production.
