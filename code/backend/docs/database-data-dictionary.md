# DAYLI database data dictionary

Final contract: `0003_dayli_foundation`, extending deployed `0002_energy`. There are 31 application tables: 12 preserved and 19 new. `auth.users` is an external Supabase-owned FK target; `alembic_version` is administrative tracking.

Generated from the complete SQLAlchemy contract and frozen migration SQL. Types/defaults/keys/checks/indexes match the locally verified schema. Physical column order of extended legacy tables can differ from ORM declaration order.

All application rows have direct profile ownership; `profiles.id` is the identity and every other table has `user_id → profiles.id`. Every table has SELECT/INSERT/UPDATE/DELETE ownership policies and a restrictive ownership fence. Column grants and history guards further restrict operations; see [deployment, ownership and security](database-migrations.md).

Absolute instants use TIMESTAMPTZ. Local weekly clocks use TIME and IANA timezone names. Duration caches are not a second editable source of truth: confirmed sleep/focus/recovery durations are derived from their appropriate endpoints. Missing measurements remain NULL.

## Table inventory

| Module | Tables |
|---|---|
| User | [profiles](#profiles), [user_preferences](#user_preferences), [availability_windows](#availability_windows) |
| Scheduler | [tasks](#tasks), [events](#events), [task_requirements](#task_requirements), [task_outcomes](#task_outcomes), [schedules](#schedules), [schedule_versions](#schedule_versions), [schedule_items](#schedule_items), [unscheduled_tasks](#unscheduled_tasks), [schedule_validation_issues](#schedule_validation_issues) |
| Energy | [sleep_schedules](#sleep_schedules), [sleep_sessions](#sleep_sessions), [sleep_usage_signals](#sleep_usage_signals), [sleep_quality_ratings](#sleep_quality_ratings), [recovery_strategies](#recovery_strategies), [recovery_periods](#recovery_periods), [energy_scores](#energy_scores) |
| Attention | [focus_sessions](#focus_sessions), [usage_sync_batches](#usage_sync_batches), [app_classifications](#app_classifications), [app_usage_summaries](#app_usage_summaries), [attention_assessments](#attention_assessments), [attention_questionnaire_responses](#attention_questionnaire_responses), [distraction_events](#distraction_events), [attention_scores](#attention_scores) |
| Decision Engine | [decision_executions](#decision_executions), [decision_candidates](#decision_candidates) |
| Dynamic Rescheduling | [rescheduling_runs](#rescheduling_runs), [rescheduling_changes](#rescheduling_changes) |

## External and administrative objects

`auth.users(id UUID)` remains owned by Supabase Auth; no Auth DDL is part of this upgrade. `public.alembic_version(version_num VARCHAR(32) PRIMARY KEY)` records the single migration head and has no runtime/client grants. `btree_gist` supplies the UUID/time-range exclusion constraint. Application object ownership belongs to migration credentials, distinct from business-module ownership.

## profiles

**Owner:** User. **State:** Existing; preserved and extended where documented. The single application identity/profile; its ID references Supabase auth.users(id). No passwords or duplicated authentication information.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `—` | PK; FK → auth.users.id |
| `username` | `VARCHAR(100)` | NULL | `—` | — |
| `full_name` | `VARCHAR(200)` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_profiles_full_name_nonblank`: CHECK (full_name IS NULL OR full_name ~ '[^[:space:]]').
- `ck_profiles_username_nonblank`: CHECK (username IS NULL OR (username = btrim(username) AND username ~ '[^[:space:]]')).
- `fk_profiles_auth_user`: FOREIGN KEY (id) → auth.users.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_profiles`: PRIMARY KEY (id).
- `uq_profiles_username`: UNIQUE (username).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
No additional index is necessary beyond those constraints.

**Database guards and timestamps**

- `profiles_updated_at` → `scheduler_set_updated_at()`.
- `profiles_create_preferences` → `scheduler_create_preferences()`.
- `dayli_01_ownership` → `dayli_owner_guard('id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## user_preferences

**Owner:** User. **State:** Existing; preserved and extended where documented. One current preferences row per profile, created by the profile trigger; timezone, notification setting and preferred working clock times.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `preference_id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `timezone` | `VARCHAR(100)` | NOT NULL | `'UTC'` | — |
| `notifications_enabled` | `BOOLEAN` | NOT NULL | `false` | — |
| `preferred_work_start` | `TIME(0) WITHOUT TIME ZONE` | NULL | `—` | — |
| `preferred_work_end` | `TIME(0) WITHOUT TIME ZONE` | NULL | `—` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_user_preferences_timezone_nonblank`: CHECK (timezone = btrim(timezone) AND timezone ~ '[^[:space:]]').
- `ck_user_preferences_work_hours`: CHECK ((preferred_work_start IS NULL AND preferred_work_end IS NULL)             OR (preferred_work_start IS NOT NULL AND preferred_work_end IS NOT NULL                 AND preferred_work_start < TIME '24:00:00'                 AND preferred_work_end < TIME '24:00:00'                 AND preferred_work_start <> preferred_work_end)).
- `fk_user_preferences_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_user_preferences`: PRIMARY KEY (preference_id).
- `uq_user_preferences_user_id`: UNIQUE (user_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
No additional index is necessary beyond those constraints.

**Database guards and timestamps**

- `user_preferences_updated_at` → `scheduler_set_updated_at()`.
- `user_preferences_validate_timezone` → `scheduler_validate_preferences_timezone()`.
- `dayli_01_ownership` → `dayli_owner_guard('preference_id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## availability_windows

**Owner:** User. **State:** New in 0003. Weekly local hard availability windows; overnight intervals wrap to next day.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `weekday` | `SMALLINT` | NOT NULL | `—` | — |
| `start_time` | `TIME(0) WITHOUT TIME ZONE` | NOT NULL | `—` | — |
| `end_time` | `TIME(0) WITHOUT TIME ZONE` | NOT NULL | `—` | — |
| `timezone` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `valid_from` | `DATE` | NULL | `—` | — |
| `valid_until` | `DATE` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_availability_dates`: CHECK ((valid_from IS NULL OR isfinite(valid_from)) AND (valid_until IS NULL OR isfinite(valid_until)) AND (valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from)).
- `ck_availability_times`: CHECK (start_time < TIME '24:00' AND end_time < TIME '24:00' AND start_time <> end_time).
- `ck_availability_timezone`: CHECK (timezone = btrim(timezone) AND timezone ~ '[^[:space:]]').
- `ck_availability_weekday`: CHECK (weekday BETWEEN 0 AND 6).
- `fk_availability_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_availability_windows`: PRIMARY KEY (id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_availability_user_day`: `CREATE INDEX ix_availability_user_day ON public.availability_windows (user_id, weekday, start_time, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id')`.
- `dayli_updated_at` → `scheduler_set_updated_at()`.
- `dayli_05_timezone` → `dayli_timezone_guard('timezone')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## tasks

**Owner:** Scheduler. **State:** Existing; preserved and extended where documented. Canonical fixed/flexible work inputs. Description is context; importance is priority 1–5; legacy status remains UNSCHEDULED.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `task_id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `title` | `VARCHAR(500)` | NOT NULL | `—` | — |
| `task_type` | `VARCHAR(8)` | NOT NULL | `—` | — |
| `deadline` | `TIMESTAMPTZ` | NULL | `—` | — |
| `estimated_duration_minutes` | `INTEGER` | NOT NULL | `—` | — |
| `category` | `VARCHAR(20)` | NOT NULL | `—` | — |
| `priority` | `INTEGER` | NOT NULL | `—` | — |
| `fixed_start_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `fixed_end_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `status` | `VARCHAR(20)` | NOT NULL | `'UNSCHEDULED'` | — |
| `context` | `VARCHAR(500)` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_tasks_category`: CHECK (category IN ('STUDY', 'WORK', 'PERSONAL', 'HEALTH', 'MEETING')).
- `ck_tasks_deadline_finite`: CHECK (deadline IS NULL OR isfinite(deadline)).
- `ck_tasks_duration_positive`: CHECK (estimated_duration_minutes > 0).
- `ck_tasks_fixed_interval`: CHECK ((task_type = 'FLEXIBLE' AND fixed_start_at IS NULL AND fixed_end_at IS NULL)             OR (task_type = 'FIXED' AND fixed_start_at IS NOT NULL AND fixed_end_at IS NOT NULL                 AND isfinite(fixed_start_at) AND isfinite(fixed_end_at)                 AND fixed_end_at > fixed_start_at)).
- `ck_tasks_priority_range`: CHECK (priority BETWEEN 1 AND 5).
- `ck_tasks_sprint1_status`: CHECK (status = 'UNSCHEDULED').
- `ck_tasks_title_nonblank`: CHECK (title ~ '[^[:space:]]').
- `ck_tasks_type`: CHECK (task_type IN ('FIXED', 'FLEXIBLE')).
- `fk_tasks_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_tasks`: PRIMARY KEY (task_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_tasks_user_deadline`: `CREATE INDEX ix_tasks_user_deadline ON public.tasks (user_id, deadline)`.

**Database guards and timestamps**

- `tasks_updated_at` → `scheduler_set_updated_at()`.
- `dayli_01_ownership` → `dayli_owner_guard('task_id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## events

**Owner:** Scheduler. **State:** Existing; preserved and extended where documented. Canonical one-time appointments or weekly local-calendar commitments; no duplicate task representation or occurrence table.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `event_id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `title` | `VARCHAR(500)` | NOT NULL | `—` | — |
| `location` | `VARCHAR(500)` | NULL | `—` | — |
| `category` | `VARCHAR(20)` | NOT NULL | `—` | — |
| `recurrence_type` | `VARCHAR(6)` | NOT NULL | `'ONCE'` | — |
| `start_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `end_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `recurrence_first_date` | `DATE` | NULL | `—` | — |
| `recurrence_until_date` | `DATE` | NULL | `—` | — |
| `recurrence_start_time` | `TIME(0) WITHOUT TIME ZONE` | NULL | `—` | — |
| `recurrence_end_time` | `TIME(0) WITHOUT TIME ZONE` | NULL | `—` | — |
| `recurrence_end_day_offset` | `SMALLINT` | NULL | `—` | — |
| `recurrence_timezone` | `VARCHAR(100)` | NULL | `—` | — |
| `source` | `VARCHAR(20)` | NOT NULL | `'MANUAL'` | — |
| `status` | `VARCHAR(20)` | NOT NULL | `'SAVED'` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_events_category`: CHECK (category IN ('STUDY', 'WORK', 'PERSONAL', 'HEALTH', 'MEETING')).
- `ck_events_recurrence_type`: CHECK (recurrence_type IN ('ONCE', 'WEEKLY')).
- `ck_events_sprint1_source`: CHECK (source = 'MANUAL').
- `ck_events_sprint1_status`: CHECK (status = 'SAVED').
- `ck_events_time_shape`: CHECK ((recurrence_type = 'ONCE'              AND start_at IS NOT NULL AND end_at IS NOT NULL              AND isfinite(start_at) AND isfinite(end_at) AND end_at > start_at              AND recurrence_first_date IS NULL AND recurrence_until_date IS NULL              AND recurrence_start_time IS NULL AND recurrence_end_time IS NULL              AND recurrence_end_day_offset IS NULL AND recurrence_timezone IS NULL)             OR (recurrence_type = 'WEEKLY' AND start_at IS NULL AND end_at IS NULL              AND recurrence_first_date IS NOT NULL AND isfinite(recurrence_first_date)              AND (recurrence_until_date IS NULL OR                   (isfinite(recurrence_until_date) AND recurrence_until_date >= recurrence_first_date))              AND recurrence_start_time IS NOT NULL AND recurrence_end_time IS NOT NULL              AND recurrence_start_time < TIME '24:00:00' AND recurrence_end_time < TIME '24:00:00'              AND recurrence_end_day_offset IS NOT NULL AND recurrence_end_day_offset >= 0              AND (recurrence_end_day_offset > 0 OR recurrence_end_time > recurrence_start_time)              AND recurrence_timezone IS NOT NULL AND recurrence_timezone = btrim(recurrence_timezone)              AND recurrence_timezone ~ '[^[:space:]]')).
- `ck_events_title_nonblank`: CHECK (title ~ '[^[:space:]]').
- `fk_events_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_events`: PRIMARY KEY (event_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_events_once_user_start`: `CREATE INDEX ix_events_once_user_start ON public.events (user_id, start_at) WHERE recurrence_type = 'ONCE'`.
- `ix_events_user_id`: `CREATE INDEX ix_events_user_id ON public.events (user_id)`.
- `ix_events_weekly_user_first`: `CREATE INDEX ix_events_weekly_user_first ON public.events (user_id, recurrence_first_date) WHERE recurrence_type = 'WEEKLY'`.

**Database guards and timestamps**

- `events_updated_at` → `scheduler_set_updated_at()`.
- `events_validate_timezone` → `scheduler_validate_event_timezone()`.
- `dayli_01_ownership` → `dayli_owner_guard('event_id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## task_requirements

**Owner:** Scheduler. **State:** New in 0003. One optional task planning extension; no duplicate task entity or importance.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `task_id` | `UUID` | NOT NULL | `—` | PK; FK → public.tasks.task_id |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `can_split` | `BOOLEAN` | NOT NULL | `false` | — |
| `minimum_session_minutes` | `INTEGER` | NULL | `—` | — |
| `preferred_start_time` | `TIME(0) WITHOUT TIME ZONE` | NULL | `—` | — |
| `preferred_end_time` | `TIME(0) WITHOUT TIME ZONE` | NULL | `—` | — |
| `preferred_timezone` | `VARCHAR(100)` | NULL | `—` | — |
| `required_energy_level` | `VARCHAR(6)` | NULL | `—` | — |
| `required_attention_level` | `VARCHAR(6)` | NULL | `—` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_task_requirements_attention`: CHECK (required_attention_level IS NULL OR required_attention_level IN ('LOW','MEDIUM','HIGH')).
- `ck_task_requirements_energy`: CHECK (required_energy_level IS NULL OR required_energy_level IN ('LOW','MEDIUM','HIGH')).
- `ck_task_requirements_minimum`: CHECK (minimum_session_minutes IS NULL OR (can_split AND minimum_session_minutes > 0)).
- `ck_task_requirements_preference`: CHECK ((preferred_start_time IS NULL AND preferred_end_time IS NULL AND preferred_timezone IS NULL) OR (preferred_start_time IS NOT NULL AND preferred_end_time IS NOT NULL AND preferred_timezone IS NOT NULL AND preferred_start_time < TIME '24:00' AND preferred_end_time < TIME '24:00' AND preferred_start_time <> preferred_end_time)).
- `fk_task_requirements_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_task_requirements_task`: FOREIGN KEY (task_id) → public.tasks.task_id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_task_requirements`: PRIMARY KEY (task_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_task_requirements_user`: `CREATE INDEX ix_task_requirements_user ON public.task_requirements (user_id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('task_id','task_id|tasks|task_id')`.
- `dayli_updated_at` → `scheduler_set_updated_at()`.
- `dayli_05_timezone` → `dayli_timezone_guard('preferred_timezone')`.
- `dayli_10_integrity` → `dayli_task_requirement_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## task_outcomes

**Owner:** Scheduler. **State:** New in 0003. Append-only task completion/skip/reopen feedback; current state is projected.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `task_id` | `UUID` | NULL | `—` | FK → public.tasks.task_id |
| `schedule_item_id` | `UUID` | NULL | `—` | FK → public.schedule_items.id |
| `outcome` | `VARCHAR(9)` | NOT NULL | `—` | — |
| `occurred_at` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `client_key` | `UUID` | NOT NULL | `—` | — |
| `task_snapshot` | `JSONB` | NOT NULL | `'{}'::jsonb` | — |
| `note` | `VARCHAR(2000)` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_task_outcomes_kind`: CHECK (outcome IN ('COMPLETED','SKIPPED','REOPENED')).
- `ck_task_outcomes_snapshot`: CHECK (jsonb_typeof(task_snapshot)='object' AND octet_length(task_snapshot::text)<=65536).
- `ck_task_outcomes_time`: CHECK (isfinite(occurred_at)).
- `fk_task_outcomes_item`: FOREIGN KEY (schedule_item_id) → public.schedule_items.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_task_outcomes_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_task_outcomes_task`: FOREIGN KEY (task_id) → public.tasks.task_id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `pk_task_outcomes`: PRIMARY KEY (id).
- `uq_task_outcomes_client`: UNIQUE (user_id, client_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_task_outcomes_item`: `CREATE INDEX ix_task_outcomes_item ON public.task_outcomes (schedule_item_id)`.
- `ix_task_outcomes_task_time`: `CREATE INDEX ix_task_outcomes_task_time ON public.task_outcomes (task_id, occurred_at, id)`.
- `ix_task_outcomes_user_time`: `CREATE INDEX ix_task_outcomes_user_time ON public.task_outcomes (user_id, occurred_at, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','schedule_item_id|schedule_items|id','task_id|tasks|task_id')`.
- `dayli_10_integrity` → `dayli_task_outcome_guard()`.
- `dayli_20_history` → `dayli_history_guard('task_id|tasks|task_id','schedule_item_id|schedule_items|id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## schedules

**Owner:** Scheduler. **State:** New in 0003. One canonical plan per user; daily/weekly/monthly views share its current version.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `current_version_id` | `UUID` | NULL | `—` | FK → public.schedule_versions.id |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `fk_schedules_current`: FOREIGN KEY (current_version_id) → public.schedule_versions.id; ON DELETE SET NULL; ON UPDATE RESTRICT; DEFERRABLE INITIALLY DEFERRED.
- `fk_schedules_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_schedules`: PRIMARY KEY (id).
- `uq_schedules_user`: UNIQUE (user_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_schedules_current`: `CREATE INDEX ix_schedules_current ON public.schedules (current_version_id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','current_version_id|schedule_versions|id')`.
- `dayli_updated_at` → `scheduler_set_updated_at()`.
- `dayli_10_integrity` → `dayli_schedule_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## schedule_versions

**Owner:** Scheduler. **State:** New in 0003. Immutable published schedule versions with timezone and planning-window snapshots.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `schedule_id` | `UUID` | NOT NULL | `—` | FK → public.schedules.id |
| `version_number` | `INTEGER` | NOT NULL | `—` | — |
| `parent_version_id` | `UUID` | NULL | `—` | FK → public.schedule_versions.id |
| `decision_execution_id` | `UUID` | NULL | `—` | FK → public.decision_executions.id |
| `window_start_at` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `window_end_at` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `timezone` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `origin` | `VARCHAR(11)` | NOT NULL | `'GENERATED'` | — |
| `status` | `VARCHAR(9)` | NOT NULL | `'DRAFT'` | — |
| `input_snapshot` | `JSONB` | NOT NULL | `'{}'::jsonb` | — |
| `explanation` | `VARCHAR(4000)` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `published_at` | `TIMESTAMPTZ` | NULL | `—` | — |

**Constraints and relationships**

- `ck_schedule_versions_number`: CHECK (version_number > 0).
- `ck_schedule_versions_origin`: CHECK (origin IN ('GENERATED','MANUAL','RESCHEDULED')).
- `ck_schedule_versions_snapshot`: CHECK (jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536).
- `ck_schedule_versions_status`: CHECK ((status='DRAFT' AND published_at IS NULL) OR (status='PUBLISHED' AND published_at IS NOT NULL AND isfinite(published_at))).
- `ck_schedule_versions_timezone`: CHECK (timezone=btrim(timezone) AND timezone ~ '[^[:space:]]').
- `ck_schedule_versions_window`: CHECK (isfinite(window_start_at) AND isfinite(window_end_at) AND window_end_at > window_start_at).
- `fk_schedule_versions_decision`: FOREIGN KEY (decision_execution_id) → public.decision_executions.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_schedule_versions_parent`: FOREIGN KEY (parent_version_id) → public.schedule_versions.id; ON DELETE NO ACTION; ON UPDATE RESTRICT; DEFERRABLE INITIALLY DEFERRED.
- `fk_schedule_versions_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_schedule_versions_schedule`: FOREIGN KEY (schedule_id) → public.schedules.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_schedule_versions`: PRIMARY KEY (id).
- `uq_schedule_versions_number`: UNIQUE (schedule_id, version_number).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_schedule_versions_decision`: `CREATE INDEX ix_schedule_versions_decision ON public.schedule_versions (decision_execution_id)`.
- `ix_schedule_versions_parent`: `CREATE INDEX ix_schedule_versions_parent ON public.schedule_versions (parent_version_id)`.
- `ix_schedule_versions_user_created`: `CREATE INDEX ix_schedule_versions_user_created ON public.schedule_versions (user_id, created_at, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','decision_execution_id|decision_executions|id','parent_version_id|schedule_versions|id','schedule_id|schedules|id')`.
- `dayli_05_timezone` → `dayli_timezone_guard('timezone')`.
- `dayli_10_integrity` → `dayli_schedule_version_guard()`.
- `dayli_90_publish` → `dayli_publish_pointer()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## schedule_items

**Owner:** Scheduler. **State:** New in 0003. Concrete versioned task/event/break placements, never a second Task or Event.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `schedule_version_id` | `UUID` | NOT NULL | `—` | FK → public.schedule_versions.id |
| `logical_key` | `UUID` | NOT NULL | `gen_random_uuid()` | — |
| `kind` | `VARCHAR(5)` | NOT NULL | `—` | — |
| `task_id` | `UUID` | NULL | `—` | FK → public.tasks.task_id |
| `event_id` | `UUID` | NULL | `—` | FK → public.events.event_id |
| `event_occurrence_date` | `DATE` | NULL | `—` | — |
| `label` | `VARCHAR(500)` | NULL | `—` | — |
| `start_at` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `end_at` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `is_locked` | `BOOLEAN` | NOT NULL | `false` | — |
| `source_snapshot` | `JSONB` | NOT NULL | `'{}'::jsonb` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_schedule_items_date`: CHECK (event_occurrence_date IS NULL OR isfinite(event_occurrence_date)).
- `ck_schedule_items_interval`: CHECK (isfinite(start_at) AND isfinite(end_at) AND end_at > start_at).
- `ck_schedule_items_kind`: CHECK ((kind='TASK' AND event_id IS NULL AND event_occurrence_date IS NULL AND label IS NULL) OR (kind='EVENT' AND task_id IS NULL AND label IS NULL) OR (kind='BREAK' AND task_id IS NULL AND event_id IS NULL AND event_occurrence_date IS NULL AND label IS NOT NULL AND label ~ '[^[:space:]]')).
- `ck_schedule_items_snapshot`: CHECK (jsonb_typeof(source_snapshot)='object' AND octet_length(source_snapshot::text)<=65536).
- `ex_schedule_items_overlap`: EXCLUDE USING GiST (schedule_version_id WITH =, tstzrange(start_at,end_at,'[)') WITH &&): no overlapping placements in one version, including concurrent writes.
- `fk_schedule_items_event`: FOREIGN KEY (event_id) → public.events.event_id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_schedule_items_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_schedule_items_task`: FOREIGN KEY (task_id) → public.tasks.task_id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_schedule_items_version`: FOREIGN KEY (schedule_version_id) → public.schedule_versions.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_schedule_items`: PRIMARY KEY (id).
- `uq_schedule_items_key`: UNIQUE (schedule_version_id, logical_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_schedule_items_event`: `CREATE INDEX ix_schedule_items_event ON public.schedule_items (event_id)`.
- `ix_schedule_items_task`: `CREATE INDEX ix_schedule_items_task ON public.schedule_items (task_id)`.
- `ix_schedule_items_user_start`: `CREATE INDEX ix_schedule_items_user_start ON public.schedule_items (user_id, start_at, id)`.
- `ix_schedule_items_version_start`: `CREATE INDEX ix_schedule_items_version_start ON public.schedule_items (schedule_version_id, start_at, id)`.
- `uq_schedule_items_once`: `CREATE UNIQUE INDEX uq_schedule_items_once ON public.schedule_items (schedule_version_id, event_id) WHERE kind='EVENT' AND event_occurrence_date IS NULL`.
- `uq_schedule_items_weekly`: `CREATE UNIQUE INDEX uq_schedule_items_weekly ON public.schedule_items (schedule_version_id, event_id, event_occurrence_date) WHERE kind='EVENT' AND event_occurrence_date IS NOT NULL`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','event_id|events|event_id','task_id|tasks|task_id','schedule_version_id|schedule_versions|id')`.
- `dayli_10_integrity` → `dayli_schedule_child_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## unscheduled_tasks

**Owner:** Scheduler. **State:** New in 0003. Version-specific omissions/partial allocation reasons; remaining minutes are derived.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `schedule_version_id` | `UUID` | NOT NULL | `—` | FK → public.schedule_versions.id |
| `task_id` | `UUID` | NULL | `—` | FK → public.tasks.task_id |
| `reason_code` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `explanation` | `VARCHAR(2000)` | NULL | `—` | — |
| `task_snapshot` | `JSONB` | NOT NULL | `'{}'::jsonb` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_unscheduled_tasks_reason`: CHECK (reason_code=btrim(reason_code) AND reason_code ~ '[^[:space:]]').
- `ck_unscheduled_tasks_snapshot`: CHECK (jsonb_typeof(task_snapshot)='object' AND octet_length(task_snapshot::text)<=65536).
- `fk_unscheduled_tasks_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_unscheduled_tasks_task`: FOREIGN KEY (task_id) → public.tasks.task_id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_unscheduled_tasks_version`: FOREIGN KEY (schedule_version_id) → public.schedule_versions.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_unscheduled_tasks`: PRIMARY KEY (id).
- `uq_unscheduled_tasks_task`: UNIQUE (schedule_version_id, task_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_unscheduled_tasks_task`: `CREATE INDEX ix_unscheduled_tasks_task ON public.unscheduled_tasks (task_id)`.
- `ix_unscheduled_tasks_user`: `CREATE INDEX ix_unscheduled_tasks_user ON public.unscheduled_tasks (user_id, created_at, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','task_id|tasks|task_id','schedule_version_id|schedule_versions|id')`.
- `dayli_10_integrity` → `dayli_schedule_child_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## schedule_validation_issues

**Owner:** Scheduler. **State:** New in 0003. Validation/conflict findings with explicit item references and human-readable reasons.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `schedule_version_id` | `UUID` | NOT NULL | `—` | FK → public.schedule_versions.id |
| `item_id` | `UUID` | NULL | `—` | FK → public.schedule_items.id |
| `related_item_id` | `UUID` | NULL | `—` | FK → public.schedule_items.id |
| `severity` | `VARCHAR(7)` | NOT NULL | `—` | — |
| `code` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `message` | `VARCHAR(2000)` | NOT NULL | `—` | — |
| `details` | `JSONB` | NOT NULL | `'{}'::jsonb` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_schedule_issues_details`: CHECK (jsonb_typeof(details)='object' AND octet_length(details::text)<=16384).
- `ck_schedule_issues_message`: CHECK (code ~ '[^[:space:]]' AND message ~ '[^[:space:]]').
- `ck_schedule_issues_severity`: CHECK (severity IN ('INFO','WARNING','ERROR')).
- `fk_schedule_issues_item`: FOREIGN KEY (item_id) → public.schedule_items.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_schedule_issues_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_schedule_issues_related`: FOREIGN KEY (related_item_id) → public.schedule_items.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_schedule_issues_version`: FOREIGN KEY (schedule_version_id) → public.schedule_versions.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_schedule_validation_issues`: PRIMARY KEY (id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_schedule_issues_item`: `CREATE INDEX ix_schedule_issues_item ON public.schedule_validation_issues (item_id)`.
- `ix_schedule_issues_related`: `CREATE INDEX ix_schedule_issues_related ON public.schedule_validation_issues (related_item_id)`.
- `ix_schedule_issues_user`: `CREATE INDEX ix_schedule_issues_user ON public.schedule_validation_issues (user_id)`.
- `ix_schedule_issues_version`: `CREATE INDEX ix_schedule_issues_version ON public.schedule_validation_issues (schedule_version_id, severity, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','item_id|schedule_items|id','related_item_id|schedule_items|id','schedule_version_id|schedule_versions|id')`.
- `dayli_10_integrity` → `dayli_schedule_child_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## sleep_schedules

**Owner:** Energy. **State:** Existing; preserved and extended where documented. One current local bedtime/wake configuration per profile. Android implements reminders and alarms. repeat_days is 0=Monday..6=Sunday, referring to the bedtime day.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `bedtime` | `TIME(0) WITHOUT TIME ZONE` | NOT NULL | `—` | — |
| `wake_time` | `TIME(0) WITHOUT TIME ZONE` | NOT NULL | `—` | — |
| `timezone` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `repeat_days` | `SMALLINT[]` | NOT NULL | `'{0,1,2,3,4,5,6}'::smallint[]` | — |
| `bedtime_reminder_enabled` | `BOOLEAN` | NOT NULL | `true` | — |
| `reminder_minutes_before` | `INTEGER` | NOT NULL | `15` | — |
| `wake_alarm_enabled` | `BOOLEAN` | NOT NULL | `true` | — |
| `is_active` | `BOOLEAN` | NOT NULL | `true` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_sleep_schedules_days_unique`: CHECK (public.dayli_valid_repeat_days(repeat_days)).
- `ck_sleep_schedules_reminder`: CHECK (reminder_minutes_before >= 0).
- `ck_sleep_schedules_repeat_days`: CHECK (repeat_days <@ ARRAY[0,1,2,3,4,5,6]::smallint[] AND array_ndims(repeat_days) = 1 AND cardinality(repeat_days) > 0).
- `ck_sleep_schedules_times`: CHECK (bedtime < TIME '24:00:00' AND wake_time < TIME '24:00:00' AND bedtime <> wake_time).
- `ck_sleep_schedules_timezone_nonblank`: CHECK (timezone = btrim(timezone) AND timezone ~ '[^[:space:]]').
- `fk_sleep_schedules_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_sleep_schedules`: PRIMARY KEY (id).
- `uq_sleep_schedules_user_id`: UNIQUE (user_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_sleep_schedules_user_id`: `CREATE INDEX ix_sleep_schedules_user_id ON public.sleep_schedules (user_id)`.

**Database guards and timestamps**

- `sleep_schedules_validate_timezone` → `energy_validate_timezone()`.
- `sleep_schedules_updated_at` → `scheduler_set_updated_at()`.
- `dayli_01_ownership` → `dayli_owner_guard('id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## sleep_sessions

**Owner:** Energy. **State:** Existing; preserved and extended where documented. Original sleep estimates/manual entries with separate confirmed/corrected endpoints and provenance. Only confirmed/corrected endpoints are eligible sleep inputs.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `sleep_date` | `DATE` | NOT NULL | `—` | — |
| `start_time` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `end_time` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `duration_minutes` | `INTEGER` | NOT NULL | `—` | — |
| `timezone` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `source` | `VARCHAR(20)` | NOT NULL | `'android_usage'` | — |
| `confidence` | `FLOAT` | NULL | `—` | — |
| `confirmed_start_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `confirmed_end_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `confirmed_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `confirmation_method` | `VARCHAR(20)` | NULL | `—` | — |
| `detection_version` | `VARCHAR(100)` | NULL | `—` | — |
| `batch_id` | `UUID` | NULL | `—` | FK → public.usage_sync_batches.id |
| `status` | `VARCHAR(30)` | NOT NULL | `'pending_confirmation'` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_sleep_sessions_confidence`: CHECK (confidence >= 0.0 AND confidence <= 1.0).
- `ck_sleep_sessions_confirmation`: CHECK ((status='pending_confirmation' AND confirmed_start_at IS NULL AND confirmed_end_at IS NULL AND confirmed_at IS NULL) OR (status IN ('confirmed','corrected') AND confirmed_start_at IS NOT NULL AND confirmed_end_at IS NOT NULL AND confirmed_at IS NOT NULL AND isfinite(confirmed_at)) OR status='rejected').
- `ck_sleep_sessions_confirmation_method`: CHECK (confirmation_method IS NULL OR confirmation_method IN ('USER_ACCEPTED','USER_CORRECTED','MANUAL','LEGACY')).
- `ck_sleep_sessions_confirmed_interval`: CHECK ((confirmed_start_at IS NULL AND confirmed_end_at IS NULL) OR (confirmed_start_at IS NOT NULL AND confirmed_end_at IS NOT NULL AND isfinite(confirmed_start_at) AND isfinite(confirmed_end_at) AND confirmed_end_at > confirmed_start_at)).
- `ck_sleep_sessions_date_finite`: CHECK (isfinite(sleep_date)).
- `ck_sleep_sessions_duration_positive`: CHECK (duration_minutes > 0).
- `ck_sleep_sessions_duration_rounded_minutes`: CHECK (duration_minutes = ROUND(EXTRACT(EPOCH FROM (end_time - start_time)) / 60)::integer).
- `ck_sleep_sessions_interval`: CHECK (isfinite(start_time) AND isfinite(end_time) AND end_time > start_time).
- `ck_sleep_sessions_source`: CHECK (source IN ('android_usage', 'manual')).
- `ck_sleep_sessions_status`: CHECK (status IN ('pending_confirmation', 'confirmed', 'corrected', 'rejected')).
- `ck_sleep_sessions_timezone_nonblank`: CHECK (timezone = btrim(timezone) AND timezone ~ '[^[:space:]]').
- `fk_sleep_sessions_batch`: FOREIGN KEY (batch_id) → public.usage_sync_batches.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_sleep_sessions_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_sleep_sessions`: PRIMARY KEY (id).
- `uq_sleep_sessions_user_id_id`: UNIQUE (user_id, id).
- `uq_sleep_sessions_user_interval`: UNIQUE (user_id, start_time, end_time).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_sleep_sessions_batch`: `CREATE INDEX ix_sleep_sessions_batch ON public.sleep_sessions (batch_id)`.
- `ix_sleep_sessions_user_pending`: `CREATE INDEX ix_sleep_sessions_user_pending ON public.sleep_sessions (user_id, status) WHERE status = 'pending_confirmation'`.
- `ix_sleep_sessions_user_start`: `CREATE INDEX ix_sleep_sessions_user_start ON public.sleep_sessions (user_id, start_time)`.
- `uq_sleep_sessions_user_sleep_date`: `CREATE UNIQUE INDEX uq_sleep_sessions_user_sleep_date ON public.sleep_sessions (user_id, sleep_date) WHERE status <> 'rejected'`.

**Database guards and timestamps**

- `sleep_sessions_validate_timezone` → `energy_validate_timezone()`.
- `sleep_sessions_updated_at` → `scheduler_set_updated_at()`.
- `dayli_01_ownership` → `dayli_owner_guard('id','batch_id|usage_sync_batches|id')`.
- `dayli_10_integrity` → `dayli_sleep_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## sleep_usage_signals

**Owner:** Energy. **State:** Existing; preserved and extended where documented. Minimal Android activity event type/timestamp, installation UUID and client event UUID; idempotency and a maximum seven-day retention after ingestion.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `session_id` | `UUID` | NULL | `—` | FK → public.sleep_sessions.id |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; FK → public.sleep_sessions.user_id; verified JWT owner; immutable |
| `event_type` | `VARCHAR(50)` | NOT NULL | `—` | — |
| `event_timestamp` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `device_id` | `UUID` | NOT NULL | `—` | — |
| `client_event_id` | `UUID` | NOT NULL | `—` | — |
| `batch_id` | `UUID` | NULL | `—` | FK → public.usage_sync_batches.id |
| `expires_at` | `TIMESTAMPTZ` | NOT NULL | `now() + INTERVAL '7 days'` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_sleep_usage_signals_expiry`: CHECK (isfinite(expires_at) AND expires_at >= created_at AND expires_at <= created_at + INTERVAL '7 days').
- `ck_sleep_usage_signals_timestamp_finite`: CHECK (isfinite(event_timestamp)).
- `ck_sleep_usage_signals_type_nonblank`: CHECK (event_type = btrim(event_type) AND event_type ~ '[^[:space:]]').
- `fk_sleep_usage_signals_batch`: FOREIGN KEY (batch_id) → public.usage_sync_batches.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_sleep_usage_signals_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_sleep_usage_signals_session`: FOREIGN KEY (user_id, session_id) → public.sleep_sessions.user_id, public.sleep_sessions.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_sleep_usage_signals`: PRIMARY KEY (id).
- `uq_sleep_usage_signals_client`: UNIQUE (user_id, device_id, client_event_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_sleep_usage_signals_batch`: `CREATE INDEX ix_sleep_usage_signals_batch ON public.sleep_usage_signals (batch_id)`.
- `ix_sleep_usage_signals_expiry`: `CREATE INDEX ix_sleep_usage_signals_expiry ON public.sleep_usage_signals (expires_at)`.
- `ix_sleep_usage_signals_session`: `CREATE INDEX ix_sleep_usage_signals_session ON public.sleep_usage_signals (session_id)`.
- `ix_sleep_usage_signals_user_time`: `CREATE INDEX ix_sleep_usage_signals_user_time ON public.sleep_usage_signals (user_id, event_timestamp)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','batch_id|usage_sync_batches|id')`.
- `dayli_10_integrity` → `dayli_sleep_signal_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## sleep_quality_ratings

**Owner:** Energy. **State:** Existing; preserved and extended where documented. One user-entered rating 1–5 per sleep session. An absent row means NULL quality; never zero.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `session_id` | `UUID` | NOT NULL | `—` | FK → public.sleep_sessions.id |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; FK → public.sleep_sessions.user_id; verified JWT owner; immutable |
| `rating` | `SMALLINT` | NOT NULL | `—` | — |
| `notes` | `VARCHAR(500)` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_sleep_quality_ratings_range`: CHECK (rating BETWEEN 1 AND 5).
- `fk_sleep_quality_ratings_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_sleep_quality_ratings_session`: FOREIGN KEY (user_id, session_id) → public.sleep_sessions.user_id, public.sleep_sessions.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_sleep_quality_ratings`: PRIMARY KEY (id).
- `uq_sleep_quality_ratings_session_id`: UNIQUE (session_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_sleep_quality_ratings_user`: `CREATE INDEX ix_sleep_quality_ratings_user ON public.sleep_quality_ratings (user_id, created_at)`.

**Database guards and timestamps**

- `sleep_quality_ratings_updated_at` → `scheduler_set_updated_at()`.
- `dayli_01_ownership` → `dayli_owner_guard('id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## recovery_strategies

**Owner:** Energy. **State:** Existing; preserved and extended where documented. One current Pomodoro/custom strategy per profile; approved defaults are 25/5/20 minutes and a long break after four sessions.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `strategy_type` | `VARCHAR(20)` | NOT NULL | `'POMODORO'` | — |
| `work_duration_minutes` | `INTEGER` | NOT NULL | `25` | — |
| `break_duration_minutes` | `INTEGER` | NOT NULL | `5` | — |
| `long_break_duration_minutes` | `INTEGER` | NOT NULL | `20` | — |
| `long_break_after_sessions` | `INTEGER` | NOT NULL | `4` | — |
| `is_active` | `BOOLEAN` | NOT NULL | `true` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_recovery_strategies_durations`: CHECK (work_duration_minutes > 0 AND break_duration_minutes > 0 AND long_break_duration_minutes > 0 AND long_break_after_sessions > 0).
- `ck_recovery_strategies_type`: CHECK (strategy_type IN ('POMODORO', 'CUSTOM')).
- `fk_recovery_strategies_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_recovery_strategies`: PRIMARY KEY (id).
- `uq_recovery_strategies_user_id`: UNIQUE (user_id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_recovery_strategies_user`: `CREATE INDEX ix_recovery_strategies_user ON public.recovery_strategies (user_id)`.

**Database guards and timestamps**

- `recovery_strategies_updated_at` → `scheduler_set_updated_at()`.
- `dayli_01_ownership` → `dayli_owner_guard('id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## recovery_periods

**Owner:** Energy. **State:** Existing; preserved and extended where documented. Planned versus observed recovery and missed breaks. Legacy non-null interval columns project the observed interval for TAKEN, otherwise the planned interval.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `start_time` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `end_time` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `duration_minutes` | `INTEGER` | NOT NULL | `—` | — |
| `planned_start_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `planned_end_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `observed_start_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `observed_end_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `status` | `VARCHAR(7)` | NOT NULL | `'TAKEN'` | — |
| `focus_session_id` | `UUID` | NULL | `—` | FK → public.focus_sessions.id |
| `strategy_id` | `UUID` | NULL | `—` | FK → public.recovery_strategies.id |
| `schedule_item_id` | `UUID` | NULL | `—` | FK → public.schedule_items.id |
| `client_key` | `UUID` | NULL | `—` | — |
| `source` | `VARCHAR(20)` | NOT NULL | `'FOCUS_TIMER'` | — |
| `recovery_type` | `VARCHAR(20)` | NOT NULL | `'SHORT_BREAK'` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_recovery_periods_duration_positive`: CHECK (duration_minutes > 0).
- `ck_recovery_periods_duration_rounded_minutes`: CHECK (duration_minutes = ROUND(EXTRACT(EPOCH FROM (end_time - start_time)) / 60)::integer).
- `ck_recovery_periods_observed`: CHECK ((observed_start_at IS NULL AND observed_end_at IS NULL) OR (observed_start_at IS NOT NULL AND observed_end_at IS NOT NULL AND isfinite(observed_start_at) AND isfinite(observed_end_at) AND observed_end_at > observed_start_at)).
- `ck_recovery_periods_planned`: CHECK ((planned_start_at IS NULL AND planned_end_at IS NULL) OR (planned_start_at IS NOT NULL AND planned_end_at IS NOT NULL AND isfinite(planned_start_at) AND isfinite(planned_end_at) AND planned_end_at > planned_start_at)).
- `ck_recovery_periods_source`: CHECK (source IN ('FOCUS_TIMER', 'DETECTED', 'MANUAL', 'PLANNED')).
- `ck_recovery_periods_status`: CHECK ((status='TAKEN' AND observed_start_at IS NOT NULL AND observed_end_at IS NOT NULL) OR (status IN ('PLANNED','MISSED') AND observed_start_at IS NULL AND observed_end_at IS NULL AND (planned_start_at IS NOT NULL OR schedule_item_id IS NOT NULL))).
- `ck_recovery_periods_times`: CHECK (isfinite(start_time) AND isfinite(end_time) AND end_time > start_time).
- `ck_recovery_periods_type`: CHECK (recovery_type IN ('SHORT_BREAK', 'LONG_BREAK', 'CUSTOM_BREAK', 'REST')).
- `fk_recovery_periods_focus`: FOREIGN KEY (focus_session_id) → public.focus_sessions.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_recovery_periods_item`: FOREIGN KEY (schedule_item_id) → public.schedule_items.id; ON DELETE NO ACTION; ON UPDATE RESTRICT; DEFERRABLE INITIALLY DEFERRED.
- `fk_recovery_periods_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_recovery_periods_strategy`: FOREIGN KEY (strategy_id) → public.recovery_strategies.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `pk_recovery_periods`: PRIMARY KEY (id).
- `uq_recovery_periods_client`: UNIQUE (user_id, client_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_recovery_periods_focus`: `CREATE INDEX ix_recovery_periods_focus ON public.recovery_periods (focus_session_id)`.
- `ix_recovery_periods_item`: `CREATE INDEX ix_recovery_periods_item ON public.recovery_periods (schedule_item_id)`.
- `ix_recovery_periods_strategy`: `CREATE INDEX ix_recovery_periods_strategy ON public.recovery_periods (strategy_id)`.
- `ix_recovery_periods_user_start`: `CREATE INDEX ix_recovery_periods_user_start ON public.recovery_periods (user_id, start_time)`.

**Database guards and timestamps**

- `recovery_periods_updated_at` → `scheduler_set_updated_at()`.
- `dayli_01_ownership` → `dayli_owner_guard('id','focus_session_id|focus_sessions|id','schedule_item_id|schedule_items|id','strategy_id|recovery_strategies|id')`.
- `dayli_10_integrity` → `dayli_recovery_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## energy_scores

**Owner:** Energy. **State:** Existing; preserved and extended where documented. Append-only versioned daily energy results and necessary input snapshots. Legacy rows remain explicitly legacy_unversioned with unknown historical timezone.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `target_date` | `DATE` | NOT NULL | `—` | — |
| `status` | `VARCHAR(20)` | NOT NULL | `'ok'` | — |
| `score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `energy_level` | `VARCHAR(10)` | NULL | `—` | — |
| `confidence` | `FLOAT` | NULL | `—` | — |
| `sleep_session_id` | `UUID` | NULL | `—` | FK → public.sleep_sessions.id |
| `timezone` | `VARCHAR(100)` | NULL | `—` | — |
| `calculation_key` | `UUID` | NOT NULL | `—` | — |
| `calculation_version` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `input_schema_version` | `SMALLINT` | NOT NULL | `1` | — |
| `input_snapshot` | `JSONB` | NOT NULL | `—` | — |
| `explanation` | `VARCHAR(4000)` | NULL | `—` | — |
| `sleep_duration_minutes` | `INTEGER` | NULL | `—` | — |
| `sleep_quality_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `sleep_quality_rating` | `SMALLINT` | NULL | `—` | — |
| `workload_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `workload_level` | `VARCHAR(10)` | NULL | `—` | — |
| `recovery_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `recovery_level` | `VARCHAR(10)` | NULL | `—` | — |
| `insights` | `JSONB` | NULL | `—` | — |
| `calculated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_energy_scores_confidence`: CHECK (confidence >= 0.0 AND confidence <= 1.0).
- `ck_energy_scores_level`: CHECK (energy_level IS NULL OR energy_level IN ('LOW', 'MEDIUM', 'HIGH')).
- `ck_energy_scores_provenance`: CHECK (calculation_version='legacy_unversioned' OR (timezone IS NOT NULL AND explanation IS NOT NULL AND explanation ~ '[^[:space:]]')).
- `ck_energy_scores_recovery_level`: CHECK (recovery_level IS NULL OR recovery_level IN ('LOW', 'MEDIUM', 'HIGH')).
- `ck_energy_scores_recovery_score`: CHECK (recovery_score IS NULL OR (recovery_score >= 0.0 AND recovery_score <= 100.0)).
- `ck_energy_scores_score_range`: CHECK (score IS NULL OR (score >= 0.0 AND score <= 100.0)).
- `ck_energy_scores_sleep_duration`: CHECK (sleep_duration_minutes IS NULL OR sleep_duration_minutes > 0).
- `ck_energy_scores_sleep_quality_rating`: CHECK (sleep_quality_rating IS NULL OR (sleep_quality_rating BETWEEN 1 AND 5)).
- `ck_energy_scores_sleep_quality_score`: CHECK (sleep_quality_score IS NULL OR (sleep_quality_score >= 0.0 AND sleep_quality_score <= 100.0)).
- `ck_energy_scores_snapshot`: CHECK (jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536).
- `ck_energy_scores_status`: CHECK (status IN ('ok', 'insufficient_data')).
- `ck_energy_scores_status_score_level`: CHECK (((status = 'ok' AND score IS NOT NULL AND energy_level IS NOT NULL) OR (status = 'insufficient_data' AND score IS NULL AND energy_level IS NULL))).
- `ck_energy_scores_version`: CHECK (isfinite(target_date) AND input_schema_version > 0 AND calculation_version ~ '[^[:space:]]').
- `ck_energy_scores_workload_level`: CHECK (workload_level IS NULL OR workload_level IN ('LOW', 'MEDIUM', 'HIGH')).
- `ck_energy_scores_workload_score`: CHECK (workload_score IS NULL OR (workload_score >= 0.0 AND workload_score <= 100.0)).
- `fk_energy_scores_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_energy_scores_sleep`: FOREIGN KEY (sleep_session_id) → public.sleep_sessions.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `pk_energy_scores`: PRIMARY KEY (id).
- `uq_energy_scores_calculation`: UNIQUE (user_id, calculation_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_energy_scores_history`: `CREATE INDEX ix_energy_scores_history ON public.energy_scores (user_id, target_date, calculated_at, id)`.
- `ix_energy_scores_sleep`: `CREATE INDEX ix_energy_scores_sleep ON public.energy_scores (sleep_session_id)`.
- `ix_energy_scores_user_calculated`: `CREATE INDEX ix_energy_scores_user_calculated ON public.energy_scores (user_id, calculated_at)`.
- `ix_energy_scores_user_date`: `CREATE INDEX ix_energy_scores_user_date ON public.energy_scores (user_id, target_date)`.

**Database guards and timestamps**

- `energy_scores_updated_at` → `scheduler_set_updated_at()`.
- `dayli_01_ownership` → `dayli_owner_guard('id','sleep_session_id|sleep_sessions|id')`.
- `dayli_05_timezone` → `dayli_timezone_guard('timezone')`.
- `dayli_10_integrity` → `dayli_score_guard()`.
- `dayli_20_history` → `dayli_history_guard('sleep_session_id|sleep_sessions|id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## focus_sessions

**Owner:** Attention. **State:** Existing; preserved and extended where documented. The single shared focus-session entity; Energy consumes its observations.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `start_time` | `TIMESTAMPTZ` | NULL | `—` | — |
| `end_time` | `TIMESTAMPTZ` | NULL | `—` | — |
| `duration_minutes` | `INTEGER` | NULL | `—` | — |
| `planned_duration_minutes` | `INTEGER` | NULL | `—` | — |
| `task_id` | `UUID` | NULL | `—` | FK → public.tasks.task_id |
| `schedule_item_id` | `UUID` | NULL | `—` | FK → public.schedule_items.id |
| `client_session_key` | `UUID` | NULL | `—` | — |
| `status` | `VARCHAR(20)` | NOT NULL | `'IN_PROGRESS'` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_focus_sessions_duration`: CHECK (duration_minutes IS NULL OR duration_minutes > 0).
- `ck_focus_sessions_planned_duration`: CHECK (planned_duration_minutes IS NULL OR planned_duration_minutes > 0).
- `ck_focus_sessions_status`: CHECK (status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'ABANDONED')).
- `ck_focus_sessions_times`: CHECK ((status = 'PLANNED' AND start_time IS NULL AND end_time IS NULL AND planned_duration_minutes IS NOT NULL) OR (status = 'CANCELLED' AND start_time IS NULL AND end_time IS NULL AND planned_duration_minutes IS NOT NULL) OR (status = 'IN_PROGRESS' AND start_time IS NOT NULL AND isfinite(start_time) AND end_time IS NULL) OR (status IN ('COMPLETED','CANCELLED','ABANDONED') AND start_time IS NOT NULL AND end_time IS NOT NULL AND isfinite(start_time) AND isfinite(end_time) AND end_time > start_time)).
- `fk_focus_sessions_item`: FOREIGN KEY (schedule_item_id) → public.schedule_items.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_focus_sessions_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_focus_sessions_task`: FOREIGN KEY (task_id) → public.tasks.task_id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `pk_focus_sessions`: PRIMARY KEY (id).
- `uq_focus_sessions_client`: UNIQUE (user_id, client_session_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_focus_sessions_item`: `CREATE INDEX ix_focus_sessions_item ON public.focus_sessions (schedule_item_id)`.
- `ix_focus_sessions_task`: `CREATE INDEX ix_focus_sessions_task ON public.focus_sessions (task_id)`.
- `ix_focus_sessions_user_start`: `CREATE INDEX ix_focus_sessions_user_start ON public.focus_sessions (user_id, start_time)`.
- `uq_focus_sessions_user_in_progress`: `CREATE UNIQUE INDEX uq_focus_sessions_user_in_progress ON public.focus_sessions (user_id) WHERE status = 'IN_PROGRESS'`.

**Database guards and timestamps**

- `focus_sessions_updated_at` → `scheduler_set_updated_at()`.
- `dayli_01_ownership` → `dayli_owner_guard('id','schedule_item_id|schedule_items|id','task_id|tasks|task_id')`.
- `dayli_10_integrity` → `dayli_focus_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## usage_sync_batches

**Owner:** Attention. **State:** New in 0003. Idempotent cumulative device/day usage snapshots; aggregate only the latest processed revision.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `device_id` | `UUID` | NOT NULL | `—` | — |
| `client_batch_id` | `UUID` | NOT NULL | `—` | — |
| `local_date` | `DATE` | NOT NULL | `—` | — |
| `timezone` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `revision` | `INTEGER` | NOT NULL | `—` | — |
| `window_start_at` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `window_end_at` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `screen_active_seconds` | `INTEGER` | NULL | `—` | — |
| `app_switch_count` | `INTEGER` | NULL | `—` | — |
| `status` | `VARCHAR(9)` | NOT NULL | `'RECEIVED'` | — |
| `processed_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `expires_at` | `TIMESTAMPTZ` | NOT NULL | `now() + INTERVAL '90 days'` | — |

**Constraints and relationships**

- `ck_usage_batches_expiry`: CHECK (isfinite(expires_at) AND expires_at >= created_at AND expires_at <= created_at + INTERVAL '90 days').
- `ck_usage_batches_revision`: CHECK (revision > 0 AND isfinite(local_date)).
- `ck_usage_batches_screen`: CHECK (screen_active_seconds IS NULL OR screen_active_seconds >= 0).
- `ck_usage_batches_status`: CHECK ((status='PROCESSED' AND processed_at IS NOT NULL AND isfinite(processed_at)) OR (status IN ('RECEIVED','FAILED') AND processed_at IS NULL)).
- `ck_usage_batches_switches`: CHECK (app_switch_count IS NULL OR app_switch_count >= 0).
- `ck_usage_batches_window`: CHECK (isfinite(window_start_at) AND isfinite(window_end_at) AND window_end_at > window_start_at).
- `fk_usage_batches_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_usage_sync_batches`: PRIMARY KEY (id).
- `uq_usage_batches_client`: UNIQUE (user_id, device_id, client_batch_id).
- `uq_usage_batches_revision`: UNIQUE (user_id, device_id, local_date, timezone, revision).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_usage_batches_expiry`: `CREATE INDEX ix_usage_batches_expiry ON public.usage_sync_batches (expires_at)`.
- `ix_usage_batches_user_latest`: `CREATE INDEX ix_usage_batches_user_latest ON public.usage_sync_batches (user_id, device_id, local_date, revision)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id')`.
- `dayli_05_timezone` → `dayli_timezone_guard('timezone')`.
- `dayli_10_integrity` → `dayli_usage_batch_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## app_classifications

**Owner:** Attention. **State:** New in 0003. User-specific app classification overrides; absence uses the module's versioned default.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `app_package` | `VARCHAR(255)` | NOT NULL | `—` | — |
| `classification` | `VARCHAR(11)` | NOT NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | updated_at DB trigger |

**Constraints and relationships**

- `ck_app_classifications_kind`: CHECK (classification IN ('PRODUCTIVE','NEUTRAL','DISTRACTING')).
- `ck_app_classifications_package`: CHECK (app_package=btrim(app_package) AND app_package ~ '^[A-Za-z0-9_.]+$').
- `fk_app_classifications_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_app_classifications`: PRIMARY KEY (id).
- `uq_app_classifications_package`: UNIQUE (user_id, app_package).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
No additional index is necessary beyond those constraints.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id')`.
- `dayli_updated_at` → `scheduler_set_updated_at()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## app_usage_summaries

**Owner:** Attention. **State:** New in 0003. Minimal per-app cumulative summary belonging to one device/day revision; no app content.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `batch_id` | `UUID` | NOT NULL | `—` | FK → public.usage_sync_batches.id |
| `app_package` | `VARCHAR(255)` | NOT NULL | `—` | — |
| `foreground_seconds` | `INTEGER` | NOT NULL | `—` | — |
| `foreground_transitions` | `INTEGER` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_app_usage_summaries_metrics`: CHECK (foreground_seconds >= 0 AND (foreground_transitions IS NULL OR foreground_transitions >= 0)).
- `ck_app_usage_summaries_package`: CHECK (app_package=btrim(app_package) AND app_package ~ '^[A-Za-z0-9_.]+$').
- `fk_app_usage_summaries_batch`: FOREIGN KEY (batch_id) → public.usage_sync_batches.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_app_usage_summaries_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_app_usage_summaries`: PRIMARY KEY (id).
- `uq_app_usage_summaries_package`: UNIQUE (batch_id, app_package).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_app_usage_summaries_user`: `CREATE INDEX ix_app_usage_summaries_user ON public.app_usage_summaries (user_id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','batch_id|usage_sync_batches|id')`.
- `dayli_10_integrity` → `dayli_usage_summary_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## attention_assessments

**Owner:** Attention. **State:** New in 0003. Versioned questionnaire instrument, completion and result snapshot; no invented questions/weights.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `questionnaire_version` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `questionnaire_snapshot` | `JSONB` | NOT NULL | `—` | — |
| `status` | `VARCHAR(9)` | NOT NULL | `'DRAFT'` | — |
| `result_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `result_summary` | `VARCHAR(4000)` | NULL | `—` | — |
| `confidence` | `NUMERIC(5, 4)` | NULL | `—` | — |
| `calculation_version` | `VARCHAR(100)` | NULL | `—` | — |
| `completed_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_attention_assessments_confidence`: CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1).
- `ck_attention_assessments_instrument`: CHECK (jsonb_typeof(questionnaire_snapshot)='object' AND COALESCE(jsonb_typeof(questionnaire_snapshot->'questions')='array' AND jsonb_array_length(questionnaire_snapshot->'questions')>0,false) AND octet_length(questionnaire_snapshot::text)<=65536).
- `ck_attention_assessments_score`: CHECK (result_score IS NULL OR result_score BETWEEN 0 AND 100).
- `ck_attention_assessments_status`: CHECK ((status='DRAFT' AND completed_at IS NULL) OR (status='COMPLETED' AND completed_at IS NOT NULL AND isfinite(completed_at) AND calculation_version IS NOT NULL AND calculation_version ~ '[^[:space:]]')).
- `ck_attention_assessments_version`: CHECK (questionnaire_version ~ '[^[:space:]]').
- `fk_attention_assessments_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_attention_assessments`: PRIMARY KEY (id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_attention_assessments_user_created`: `CREATE INDEX ix_attention_assessments_user_created ON public.attention_assessments (user_id, created_at, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id')`.
- `dayli_10_integrity` → `dayli_assessment_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## attention_questionnaire_responses

**Owner:** Attention. **State:** New in 0003. Canonical answer strings tied to question keys in the captured instrument definition.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `assessment_id` | `UUID` | NOT NULL | `—` | FK → public.attention_assessments.id |
| `question_key` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `answer_value` | `VARCHAR(2000)` | NOT NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_attention_responses_value`: CHECK (question_key ~ '[^[:space:]]' AND answer_value ~ '[^[:space:]]').
- `fk_attention_responses_assessment`: FOREIGN KEY (assessment_id) → public.attention_assessments.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_attention_responses_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_attention_questionnaire_responses`: PRIMARY KEY (id).
- `uq_attention_responses_question`: UNIQUE (assessment_id, question_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_attention_responses_user`: `CREATE INDEX ix_attention_responses_user ON public.attention_questionnaire_responses (user_id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','assessment_id|attention_assessments|id')`.
- `dayli_10_integrity` → `dayli_response_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## distraction_events

**Owner:** Attention. **State:** New in 0003. Inferred focus-context distractions with confidence and explicit user acceptance/rejection.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `focus_session_id` | `UUID` | NOT NULL | `—` | FK → public.focus_sessions.id |
| `start_at` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `end_at` | `TIMESTAMPTZ` | NOT NULL | `—` | — |
| `app_package` | `VARCHAR(255)` | NULL | `—` | — |
| `detection_source` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `detector_version` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `confidence` | `NUMERIC(5, 4)` | NULL | `—` | — |
| `status` | `VARCHAR(9)` | NOT NULL | `'PROBABLE'` | — |
| `reviewed_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `client_key` | `UUID` | NOT NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_distraction_events_confidence`: CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1).
- `ck_distraction_events_interval`: CHECK (isfinite(start_at) AND isfinite(end_at) AND end_at > start_at).
- `ck_distraction_events_source`: CHECK (detection_source ~ '[^[:space:]]' AND detector_version ~ '[^[:space:]]').
- `ck_distraction_events_status`: CHECK ((status='PROBABLE' AND reviewed_at IS NULL) OR (status IN ('CONFIRMED','IGNORED') AND reviewed_at IS NOT NULL AND isfinite(reviewed_at))).
- `fk_distraction_events_focus`: FOREIGN KEY (focus_session_id) → public.focus_sessions.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_distraction_events_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_distraction_events`: PRIMARY KEY (id).
- `uq_distraction_events_client`: UNIQUE (user_id, client_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_distraction_events_focus`: `CREATE INDEX ix_distraction_events_focus ON public.distraction_events (focus_session_id, start_at)`.
- `ix_distraction_events_user`: `CREATE INDEX ix_distraction_events_user ON public.distraction_events (user_id, start_at, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','focus_session_id|focus_sessions|id')`.
- `dayli_10_integrity` → `dayli_distraction_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## attention_scores

**Owner:** Attention. **State:** New in 0003. Immutable versioned score results and historical focus/distraction metrics; NULL is missing.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `target_date` | `DATE` | NOT NULL | `—` | — |
| `timezone` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `status` | `VARCHAR(20)` | NOT NULL | `—` | — |
| `score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `attention_level` | `VARCHAR(6)` | NULL | `—` | — |
| `confidence` | `NUMERIC(5, 4)` | NULL | `—` | — |
| `average_focus_seconds` | `NUMERIC(14, 3)` | NULL | `—` | — |
| `distraction_count` | `INTEGER` | NULL | `—` | — |
| `total_distraction_seconds` | `BIGINT` | NULL | `—` | — |
| `focus_completion_rate` | `NUMERIC(5, 4)` | NULL | `—` | — |
| `trend` | `VARCHAR(9)` | NULL | `—` | — |
| `assessment_id` | `UUID` | NULL | `—` | FK → public.attention_assessments.id |
| `calculation_key` | `UUID` | NOT NULL | `—` | — |
| `calculation_version` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `input_schema_version` | `SMALLINT` | NOT NULL | `1` | — |
| `input_snapshot` | `JSONB` | NOT NULL | `—` | — |
| `explanation` | `VARCHAR(4000)` | NOT NULL | `—` | — |
| `calculated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_attention_scores_confidence`: CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1).
- `ck_attention_scores_date`: CHECK (isfinite(target_date)).
- `ck_attention_scores_level`: CHECK (attention_level IS NULL OR attention_level IN ('LOW','MEDIUM','HIGH')).
- `ck_attention_scores_metrics`: CHECK ((average_focus_seconds IS NULL OR (average_focus_seconds>=0 AND average_focus_seconds<'NaN'::numeric)) AND (distraction_count IS NULL OR distraction_count>=0) AND (total_distraction_seconds IS NULL OR total_distraction_seconds>=0) AND (focus_completion_rate IS NULL OR focus_completion_rate BETWEEN 0 AND 1)).
- `ck_attention_scores_range`: CHECK (score IS NULL OR score BETWEEN 0 AND 100).
- `ck_attention_scores_snapshot`: CHECK (jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536).
- `ck_attention_scores_status`: CHECK ((status='ok' AND score IS NOT NULL AND attention_level IS NOT NULL) OR (status='insufficient_data' AND score IS NULL AND attention_level IS NULL)).
- `ck_attention_scores_trend`: CHECK (trend IS NULL OR trend IN ('IMPROVING','STABLE','DECLINING')).
- `ck_attention_scores_version`: CHECK (calculation_version ~ '[^[:space:]]' AND explanation ~ '[^[:space:]]' AND input_schema_version > 0).
- `fk_attention_scores_assessment`: FOREIGN KEY (assessment_id) → public.attention_assessments.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_attention_scores_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_attention_scores`: PRIMARY KEY (id).
- `uq_attention_scores_calculation`: UNIQUE (user_id, calculation_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_attention_scores_assessment`: `CREATE INDEX ix_attention_scores_assessment ON public.attention_scores (assessment_id)`.
- `ix_attention_scores_user_date`: `CREATE INDEX ix_attention_scores_user_date ON public.attention_scores (user_id, target_date, calculated_at, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','assessment_id|attention_assessments|id')`.
- `dayli_05_timezone` → `dayli_timezone_guard('timezone')`.
- `dayli_10_integrity` → `dayli_score_guard()`.
- `dayli_20_history` → `dayli_history_guard('assessment_id|attention_assessments|id')`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## decision_executions

**Owner:** Decision Engine. **State:** New in 0003. Auditable algorithm runs with exact source-score references and versioned inputs.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `execution_key` | `UUID` | NOT NULL | `—` | — |
| `input_schedule_version_id` | `UUID` | NULL | `—` | FK → public.schedule_versions.id |
| `energy_score_id` | `UUID` | NULL | `—` | FK → public.energy_scores.id |
| `attention_score_id` | `UUID` | NULL | `—` | FK → public.attention_scores.id |
| `algorithm_version` | `VARCHAR(100)` | NOT NULL | `—` | — |
| `input_schema_version` | `SMALLINT` | NOT NULL | `1` | — |
| `input_snapshot` | `JSONB` | NOT NULL | `—` | — |
| `status` | `VARCHAR(9)` | NOT NULL | `'RUNNING'` | — |
| `overall_schedule_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `explanation` | `VARCHAR(4000)` | NULL | `—` | — |
| `failure_code` | `VARCHAR(100)` | NULL | `—` | — |
| `started_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `finished_at` | `TIMESTAMPTZ` | NULL | `—` | — |

**Constraints and relationships**

- `ck_decision_executions_explanation`: CHECK (status <> 'SUCCEEDED' OR (explanation IS NOT NULL AND explanation ~ '[^[:space:]]')).
- `ck_decision_executions_score`: CHECK (overall_schedule_score IS NULL OR overall_schedule_score BETWEEN 0 AND 100).
- `ck_decision_executions_snapshot`: CHECK (jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536).
- `ck_decision_executions_status`: CHECK (isfinite(started_at) AND ((status='RUNNING' AND finished_at IS NULL) OR (status IN ('SUCCEEDED','FAILED') AND finished_at IS NOT NULL AND isfinite(finished_at) AND finished_at >= started_at))).
- `ck_decision_executions_version`: CHECK (algorithm_version ~ '[^[:space:]]' AND input_schema_version > 0).
- `fk_decision_executions_attention`: FOREIGN KEY (attention_score_id) → public.attention_scores.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_decision_executions_energy`: FOREIGN KEY (energy_score_id) → public.energy_scores.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_decision_executions_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_decision_executions_schedule`: FOREIGN KEY (input_schedule_version_id) → public.schedule_versions.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `pk_decision_executions`: PRIMARY KEY (id).
- `uq_decision_executions_key`: UNIQUE (user_id, execution_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_decision_executions_attention`: `CREATE INDEX ix_decision_executions_attention ON public.decision_executions (attention_score_id)`.
- `ix_decision_executions_energy`: `CREATE INDEX ix_decision_executions_energy ON public.decision_executions (energy_score_id)`.
- `ix_decision_executions_schedule`: `CREATE INDEX ix_decision_executions_schedule ON public.decision_executions (input_schedule_version_id)`.
- `ix_decision_executions_user_time`: `CREATE INDEX ix_decision_executions_user_time ON public.decision_executions (user_id, started_at, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','attention_score_id|attention_scores|id','energy_score_id|energy_scores|id','input_schedule_version_id|schedule_versions|id')`.
- `dayli_10_integrity` → `dayli_decision_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## decision_candidates

**Owner:** Decision Engine. **State:** New in 0003. Selected/rejected task-time, break and postponement alternatives with component scores and reasons.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `decision_execution_id` | `UUID` | NOT NULL | `—` | FK → public.decision_executions.id |
| `candidate_key` | `UUID` | NOT NULL | `—` | — |
| `task_id` | `UUID` | NULL | `—` | FK → public.tasks.task_id |
| `kind` | `VARCHAR(9)` | NOT NULL | `—` | — |
| `start_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `end_at` | `TIMESTAMPTZ` | NULL | `—` | — |
| `rank` | `INTEGER` | NULL | `—` | — |
| `selection_status` | `VARCHAR(10)` | NOT NULL | `'CONSIDERED'` | — |
| `priority_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `deadline_urgency_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `energy_match_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `attention_match_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `task_time_score` | `NUMERIC(5, 2)` | NULL | `—` | — |
| `task_snapshot` | `JSONB` | NOT NULL | `'{}'::jsonb` | — |
| `explanation` | `VARCHAR(4000)` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_decision_candidates_components`: CHECK ((priority_score IS NULL OR priority_score BETWEEN 0 AND 100) AND (deadline_urgency_score IS NULL OR deadline_urgency_score BETWEEN 0 AND 100) AND (energy_match_score IS NULL OR energy_match_score BETWEEN 0 AND 100) AND (attention_match_score IS NULL OR attention_match_score BETWEEN 0 AND 100) AND (task_time_score IS NULL OR task_time_score BETWEEN 0 AND 100)).
- `ck_decision_candidates_interval`: CHECK ((start_at IS NULL AND end_at IS NULL) OR (start_at IS NOT NULL AND end_at IS NOT NULL AND isfinite(start_at) AND isfinite(end_at) AND end_at > start_at)).
- `ck_decision_candidates_kind`: CHECK (kind IN ('TASK_SLOT','BREAK','POSTPONE') AND (rank IS NULL OR rank > 0)).
- `ck_decision_candidates_selection`: CHECK (selection_status IN ('CONSIDERED','SELECTED','REJECTED')).
- `ck_decision_candidates_slot`: CHECK (kind <> 'TASK_SLOT' OR start_at IS NOT NULL).
- `ck_decision_candidates_snapshot`: CHECK (jsonb_typeof(task_snapshot)='object' AND octet_length(task_snapshot::text)<=65536).
- `fk_decision_candidates_execution`: FOREIGN KEY (decision_execution_id) → public.decision_executions.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_decision_candidates_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_decision_candidates_task`: FOREIGN KEY (task_id) → public.tasks.task_id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `pk_decision_candidates`: PRIMARY KEY (id).
- `uq_decision_candidates_key`: UNIQUE (decision_execution_id, candidate_key).
- `uq_decision_candidates_rank`: UNIQUE (decision_execution_id, rank).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_decision_candidates_task`: `CREATE INDEX ix_decision_candidates_task ON public.decision_candidates (task_id)`.
- `ix_decision_candidates_user`: `CREATE INDEX ix_decision_candidates_user ON public.decision_candidates (user_id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','decision_execution_id|decision_executions|id','task_id|tasks|task_id')`.
- `dayli_10_integrity` → `dayli_candidate_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## rescheduling_runs

**Owner:** Dynamic Rescheduling. **State:** New in 0003. Previous/proposed/applied version history with reasons, decisions and user acceptance state.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `request_key` | `UUID` | NOT NULL | `—` | — |
| `previous_version_id` | `UUID` | NOT NULL | `—` | FK → public.schedule_versions.id |
| `new_version_id` | `UUID` | NULL | `—` | FK → public.schedule_versions.id |
| `decision_execution_id` | `UUID` | NULL | `—` | FK → public.decision_executions.id |
| `trigger_type` | `VARCHAR(32)` | NOT NULL | `—` | — |
| `reason` | `VARCHAR(4000)` | NOT NULL | `—` | — |
| `status` | `VARCHAR(8)` | NOT NULL | `'PENDING'` | — |
| `requested_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |
| `finished_at` | `TIMESTAMPTZ` | NULL | `—` | — |

**Constraints and relationships**

- `ck_rescheduling_runs_reason`: CHECK (reason ~ '[^[:space:]]').
- `ck_rescheduling_runs_status`: CHECK (isfinite(requested_at) AND ((status='PENDING' AND finished_at IS NULL) OR (status='PROPOSED' AND new_version_id IS NOT NULL AND finished_at IS NULL) OR (status IN ('APPLIED','REJECTED') AND new_version_id IS NOT NULL AND finished_at IS NOT NULL AND isfinite(finished_at) AND finished_at >= requested_at) OR (status='FAILED' AND finished_at IS NOT NULL AND isfinite(finished_at) AND finished_at >= requested_at))).
- `ck_rescheduling_runs_trigger`: CHECK (trigger_type IN ('TASK_MISSED','TASK_DELAYED','AVAILABILITY_CHANGED','ENERGY_CHANGED','ATTENTION_CHANGED','TASK_ADDED','EVENT_ADDED','INPUT_CHANGED','CONFLICT','MANUAL')).
- `ck_rescheduling_runs_versions`: CHECK (new_version_id IS NULL OR new_version_id <> previous_version_id).
- `fk_rescheduling_runs_decision`: FOREIGN KEY (decision_execution_id) → public.decision_executions.id; ON DELETE SET NULL; ON UPDATE RESTRICT.
- `fk_rescheduling_runs_new`: FOREIGN KEY (new_version_id) → public.schedule_versions.id; ON DELETE NO ACTION; ON UPDATE RESTRICT; DEFERRABLE INITIALLY DEFERRED.
- `fk_rescheduling_runs_previous`: FOREIGN KEY (previous_version_id) → public.schedule_versions.id; ON DELETE NO ACTION; ON UPDATE RESTRICT; DEFERRABLE INITIALLY DEFERRED.
- `fk_rescheduling_runs_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_rescheduling_runs`: PRIMARY KEY (id).
- `uq_rescheduling_runs_key`: UNIQUE (user_id, request_key).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_rescheduling_runs_decision`: `CREATE INDEX ix_rescheduling_runs_decision ON public.rescheduling_runs (decision_execution_id)`.
- `ix_rescheduling_runs_new`: `CREATE INDEX ix_rescheduling_runs_new ON public.rescheduling_runs (new_version_id)`.
- `ix_rescheduling_runs_previous`: `CREATE INDEX ix_rescheduling_runs_previous ON public.rescheduling_runs (previous_version_id)`.
- `ix_rescheduling_runs_user_time`: `CREATE INDEX ix_rescheduling_runs_user_time ON public.rescheduling_runs (user_id, requested_at, id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','decision_execution_id|decision_executions|id','new_version_id|schedule_versions|id','previous_version_id|schedule_versions|id')`.
- `dayli_10_integrity` → `dayli_rescheduling_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.

## rescheduling_changes

**Owner:** Dynamic Rescheduling. **State:** New in 0003. Affected old/new item references; immutable schedule versions provide interval history.

| Column | PostgreSQL type | Nullability | Default | Key / relationship |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | PK |
| `user_id` | `UUID` | NOT NULL | `—` | FK → public.profiles.id; verified JWT owner; immutable |
| `rescheduling_run_id` | `UUID` | NOT NULL | `—` | FK → public.rescheduling_runs.id |
| `previous_item_id` | `UUID` | NULL | `—` | FK → public.schedule_items.id |
| `new_item_id` | `UUID` | NULL | `—` | FK → public.schedule_items.id |
| `change_type` | `VARCHAR(7)` | NOT NULL | `—` | — |
| `explanation` | `VARCHAR(2000)` | NULL | `—` | — |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

**Constraints and relationships**

- `ck_rescheduling_changes_shape`: CHECK ((change_type='ADDED' AND previous_item_id IS NULL AND new_item_id IS NOT NULL) OR (change_type='REMOVED' AND previous_item_id IS NOT NULL AND new_item_id IS NULL) OR (change_type='MOVED' AND previous_item_id IS NOT NULL AND new_item_id IS NOT NULL)).
- `fk_rescheduling_changes_new`: FOREIGN KEY (new_item_id) → public.schedule_items.id; ON DELETE NO ACTION; ON UPDATE RESTRICT; DEFERRABLE INITIALLY DEFERRED.
- `fk_rescheduling_changes_previous`: FOREIGN KEY (previous_item_id) → public.schedule_items.id; ON DELETE NO ACTION; ON UPDATE RESTRICT; DEFERRABLE INITIALLY DEFERRED.
- `fk_rescheduling_changes_profile`: FOREIGN KEY (user_id) → public.profiles.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `fk_rescheduling_changes_run`: FOREIGN KEY (rescheduling_run_id) → public.rescheduling_runs.id; ON DELETE CASCADE; ON UPDATE RESTRICT.
- `pk_rescheduling_changes`: PRIMARY KEY (id).

**Indexes**

Primary-key and UNIQUE constraints create their own indexes.
- `ix_rescheduling_changes_new`: `CREATE INDEX ix_rescheduling_changes_new ON public.rescheduling_changes (new_item_id)`.
- `ix_rescheduling_changes_previous`: `CREATE INDEX ix_rescheduling_changes_previous ON public.rescheduling_changes (previous_item_id)`.
- `ix_rescheduling_changes_run`: `CREATE INDEX ix_rescheduling_changes_run ON public.rescheduling_changes (rescheduling_run_id)`.
- `ix_rescheduling_changes_user`: `CREATE INDEX ix_rescheduling_changes_user ON public.rescheduling_changes (user_id)`.

**Database guards and timestamps**

- `dayli_01_ownership` → `dayli_owner_guard('id','new_item_id|schedule_items|id','previous_item_id|schedule_items|id','rescheduling_run_id|rescheduling_runs|id')`.
- `dayli_10_integrity` → `dayli_rescheduling_change_guard()`.
The owning module writes its domain fields; other modules consume references or use its service interface. Optional source FKs can become NULL only through real source deletion where history guards apply; required history references remain protected. Whole-account deletion cascades through the profile.
