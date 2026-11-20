# DAYLI final database contract

Status: implementation authorized by the detailed request. User confirmed the
live starting revision is **0002_energy**. Deployment is manual through SQL
Editor; no MCP, remote connection, remote DDL, reset, frontend or algorithm work.
This document records implementation assumptions, not independent sign-off of
older diagrams. Existing migrations 0001/0002 are immutable.

## Authority, scope and approach

The current request and matching implementation supersede older conflicting ERDs.
One database, shared Base, shared Alembic history and modular monolith remain.
Use an additive 0003 after 0002. Rebuilding the database or implementing every
older diagram would duplicate entities and include unapproved integrations.
The 244-test baseline passed. Existing core columns and API contracts stay intact.
Review evidence includes all current models/migrations/DTOs, existing architecture
and migration guides, project requirements/backlogs/chapters and the older ERDs.

## Tables: 31 application tables (12 existing, 19 new)

| Owner | Existing | New |
|---|---|---|
| User | profiles, user_preferences | availability_windows |
| Scheduler | tasks, events | task_requirements, task_outcomes, schedules, schedule_versions, schedule_items, unscheduled_tasks, schedule_validation_issues |
| Energy | sleep_schedules, sleep_sessions, sleep_usage_signals, sleep_quality_ratings, recovery_strategies, recovery_periods, energy_scores | none |
| Attention | focus_sessions (move code ownership, preserve table) | usage_sync_batches, app_classifications, app_usage_summaries, attention_assessments, attention_questionnaire_responses, distraction_events, attention_scores |
| Decision | none | decision_executions, decision_candidates |
| Rescheduling | none | rescheduling_runs, rescheduling_changes |

`auth.users` is external; `alembic_version` is administrative tracking, neither an
application entity. Every application table has a UUID PK and direct profile
ownership (`profiles.id` is the identity; others have user_id). Task requirements
use task_id as their one-to-one UUID PK. No duplicate User/Task/Event/Focus table.

## Existing contracts and changes

Preserve all 44 core columns, categories, priority 1-5, Task status UNSCHEDULED,
Event source/status MANUAL/SAVED, weekly local rules, and all 21 API operations.
Description remains tasks.context. Task lifecycle is append-only outcomes
(COMPLETED/SKIPPED/REOPENED), and placement is version-specific; do not invent a
second mutable task status. Active Focus does not itself prove task completion.

Energy changes are incremental: add separate confirmed/corrected sleep endpoints
and provenance, nullable unknown confidence with no default certainty, device/
client IDs and expiry for raw signals, planned-versus-actual recovery fields and
links, and versioned immutable energy calculations. Preserve original columns and
values. Replace energy_scores' one-per-day unique constraint with a calculation
idempotency key; multiple versions per day are required. Add planned duration,
task/placement links and client key to the existing Focus table. Legacy duration
columns are reported/cached measurements; consumers calculate elapsed duration
from endpoints, not a second editable source of truth.

Backfill confirmed intervals only for already-confirmed rows. Mark old scores
`legacy_unversioned` and capture their existing inputs/components; do not pretend
to know old weights or calculation timezone. New calculations must provide a
version, bounded input snapshot and idempotency key. Legacy certainty/defaults
are not scientific evidence; decision runs cannot use unversioned legacy scores
as validated measurements. Keep sleep quality in its existing one-to-one rating
table: absent row means NULL, never zero.

Sleep repeat_days remains 0=Monday..6=Sunday to preserve deployed data. New
availability uses the same convention; convert the existing API's ISO weekday
1..7 explicitly. Equal local endpoints are invalid, end < start wraps to next
local day. Keep a single main non-rejected sleep session per user/date as in 0002;
naps/multiple daily sleep periods remain a future domain decision.

## Scheduling and concurrency

One canonical schedules container per user prevents daily/weekly views becoming
competing duplicate plans. Each version has an ordered unique number, parent,
absolute window and IANA timezone snapshot. Daily/weekly/monthly are views of that
plan, not three copies. Versions start DRAFT, then PUBLISHED. Publication locks the
container, verifies parent=current, validates content, freezes plan fields and
updates the current pointer. Direct pointer changes, stale publications, and
changes/deletion of published history are rejected. Whole-account erasure cascades.

Items have TASK/EVENT/BREAK kind, real optional task/event FKs, absolute interval,
logical key, lock and bounded historical source snapshot. A btree_gist exclusion
constraint prevents interval overlaps atomically, including concurrent inserts;
touching half-open intervals are allowed. This native extension is justified by
the required invariant and retained on downgrade if other objects depend on it.
There is no recurrence-occurrence table: event placements are output snapshots.

Check fixed task/event anchors and keep them locked. Flexible splitting is opt-in,
with positive minimum duration and allocation bounded by the source estimate.
Unscheduled rows store reasons and the task snapshot; remaining duration is
derived. Validation issues store severity/code/message and explicit item FKs.
Draft edits after validation must be rechecked at publication. Source changes
invalidate stale draft snapshots. Published timezone/source snapshots never
change when preferences or source rows change.

Weekly placements use a local occurrence DATE, first-date weekday, inclusive
until date and local clock endpoints. Resolve each endpoint separately; never
add 168 UTC hours. Reject ambiguous/nonexistent local times consistently with the
existing Python resolver, including non-hour offset changes. Database clock
validation is a second boundary, not an occurrence-generation engine.

Rescheduling runs link previous/new versions and a decision run. They retain
pending/proposed/applied/rejected/failed history. Changes link old/new items in
those exact versions and use ADDED/MOVED/REMOVED. Times are read from items, not
copied. New.parent=previous and both belong to the same schedule/user. Applied
must reference the current published version. Automated changes preserve locks
and completed task placements; explicit source edits/deletion or a recorded
manual adjustment are the only intentional exceptions. Rejected proposals never
replace the current pointer.

## Ownership, FKs and module boundaries

Every real FK is indexed. Required history links use deferred NO ACTION when
needed for account-cascade ordering. Optional source links use SET NULL and retain
immutable snapshots so deleting a Task/Event keeps existing DELETE APIs usable.
Published content may accept FK nulling caused by source deletion, not arbitrary
source swaps. A shared SECURITY INVOKER ownership trigger validates every optional
single-column reference and prevents owner changes. Keep existing composite sleep
FKs. Specific guards enforce same-version/same-schedule semantics beyond user_id.
Identifiers in trigger arguments are migration constants, never client SQL.

Owners write through their own services; other modules read references/projections
or call owner interfaces. Attention owns the single Focus entity. Energy reexports
that class for import compatibility and consumes it for recovery. One application
DB credential cannot enforce Python-package permissions; RLS enforces users.

## Attention, scores and privacy

Usage batches are device/day/timezone cumulative snapshots with monotonic revision
and client UUID. App summaries are unique per batch/package. Select the latest
processed revision per device/day; never sum cumulative revisions. Device IDs are
installation UUIDs, not hardware/advertising identifiers. Existing raw sleep
signals keep only event type/time, gain device/client deduplication, batch reference
and a maximum seven-day expiry. Backfill legacy device=user_id and client=id, clearly
marked as synthetic legacy identifiers. Usage summaries expire after 90 days.
Expiry hides raw records; documented admin maintenance physically purges expired
rows. No cron/service or sensitive app-content storage is added.

Questionnaire version and a bounded immutable instrument-definition snapshot
belong to an assessment; responses are unique question-key/canonical-answer rows.
Keys must occur in the captured instrument. Completion freezes definition/answers/
result. The definition is a flexible versioned artifact, not relational user data.
Do not invent questions, clinical claims or scoring weights. App classifications
are per-user productive/neutral/distracting overrides. Distractions retain source,
interval, confidence and PROBABLE/CONFIRMED/IGNORED user-review state; a switch alone
does not imply distraction. Focus uses actual endpoints and optional planned duration.

Energy/attention scores and normalized decision components are 0-100; confidence
and completion rates are 0-1. Missing data remains NULL. Final calculations are
append-only, with algorithm/input-schema versions and bounded object snapshots.
Historical focus/distraction metrics are retained at calculation time because
observations can be corrected or expire. Decision executions reference exact
energy/attention inputs and input schedule; candidates hold component scores,
rank/selection/rejection explanations and real optional task links. No duplicate
score columns in Scheduler and no algorithm implementation.

## Security and staging

Retain verified JWT-before-connection and transaction-local request.jwt identity,
UTC and row_security, with rollback/pooled cleanup. Current administrative runtime
opt-in contradicts the new requirement: ordinary sessions and readiness reject
owner/postgres/service_role/BYPASSRLS/admin/DDL-capable connections in all modes.
Keep migration/admin helpers separate; do not rewrite private .env credentials.
Restricted runtime provisioning and safe handling of previously shared credentials
remain manual deployment steps. Remote password-bearing role DDL fails closed.

All 31 tables have four operation policies plus a restrictive ownership fence,
using (SELECT auth.uid()). Existing profile/preference missing policies do not
grant previously forbidden direct operations. Remove broad migration-tracker
grants and known team/service defaults on application data. User clients read new
tables; a safe NOLOGIN dayli_app_writer group grants server-only column-scoped DML
to a restricted LOGIN inheriting authenticated. No passwords in deployment SQL.
Trusted development DDL is local; production team access is not unrestricted.
Direct backend DB credentials are trusted because a holder can set GUC claims.

Keep runtime core-model discovery independent of planned tables so old core APIs
can operate while manual deployment is pending. Complete migration metadata is
assembled in app/core/schema_models.py on the same Base; new module code must use
that complete discovery after deploying 0003. Focus lives in Attention/focus.py
so Energy import compatibility does not force all future Attention tables active.

## Deployment, exclusions and checks

Freeze 0003 independently of ORM imports. The user's latest instruction supersedes
the earlier three-script request: export ONLY upgrade_from_0002 (0003 only).
Fresh local installations still use the same Alembic chain. No table
IF NOT EXISTS that masks conflicts, no reset, no editing applied migrations.
Preflight verifies expected version and rejects conflicting target objects. Unsafe
legacy data fails validation with actionable constraints; never silently erase it.
Data-bearing downgrades fail closed; test rollback uses a fresh empty disposable DB.

Optional calendar/email connections, mappings and sync history are documented but
not created. Future secrets require envelope encryption/secret-manager references;
no plaintext tokens now. Older burnout, gym/ML and complex recurrence exceptions
are excluded from this requested MVP contract. No schema can promise zero future
migrations for unspecified features.

Deliver ERD, complete column dictionary, consolidated ownership/security/integration
handover in the existing migration guide, models, 0003 and one SQL upgrade.
Verify seeded 0002 preservation, fresh Alembic/current SQL upgrade
SQL equivalence, metadata, all-table RLS, cross-user refs, null FK cleanup, account
erasure, score/sleep eligibility, sync idempotency/expiry, immutable history, DST,
stale publication, concurrent overlaps, and all existing API tests locally only.
