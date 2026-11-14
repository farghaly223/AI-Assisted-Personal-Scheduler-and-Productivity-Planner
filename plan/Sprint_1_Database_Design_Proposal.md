**Sprint 1 database design proposal — AI-Assisted Personal Scheduler**

Prepared: 14 November 2026. Status: proposed for review; not approved for implementation.

The proposed application schema contains **four tables: `profiles`, `user_preferences`, `tasks`, and `events`**, alongside the existing, externally managed `auth.users`. It supports account/profile foundations, preferences, task inputs, individual fixed commitments, and repeating weekly university/work timetables. It does not create generated schedules or perform optimization.

The user clarified during this review that **weekly recurrence is required** and **task importance uses integers 1–5, with 5 most important**. These are explicit requirements for this proposal. Other proposed defaults and policy choices below are recommendations for approval, not claims that the original documents already specified them.

This document and the [PlantUML proposal](../digrams/masterclass/sprint1_database_proposal.puml) are design artifacts only. No application code, database objects, migrations, or remote Supabase resources have been changed. Future implementation belongs under `code/` and requires the user's approval.

The repository was inspected before preparing the proposal. `code/` was empty; the root contained an environment-test Python file, project chapters, `data/`, `digrams/`, and `plan/`. No `AGENTS.md` was found in the repository or the checked parent directories. The following sources were cross-checked rather than treating the newest ERD as automatically authoritative.

| Reference | Source inspected | Relevant evidence |
|---|---|---|
| ERD | [dbschema.svg](../digrams/masterclass/dbschema.svg), titled “PostgreSQL ERD — Revised Version 3” | Supabase identity mapping; the six candidate tables; task constraint times versus allocated times; ownership and event notes. |
| Master SVG | [master.svg](../digrams/masterclass/master.svg) | User/UserPreferences; separate Event and Task classes; category/type/status vocabularies; Task, Schedule, TimeSlot, and UserTaskTable. |
| Master PNG | [master.png](../digrams/master.png), overview and detailed Scheduler region | Abstract Task with FixedTask/FlexibleTask subclasses; priority/deadline on FlexibleTask; fixed times on FixedTask; actual TimeSlot objects. |
| Ownership | [owner.svg](../digrams/masterclass/owner.svg) and [integration.svg](../digrams/masterclass/integration.svg) | One authoritative User; Event owned by Calendar/Email; Task and Schedule owned by Scheduler; other modules consume references/projections. |
| Decision diagram | [class.png](../digrams/class.png) | Its external Task projection calls the integer input `importance`; Master/ERD call it `priority`. |
| FR | [Functional Requirements(edited).pdf](../data/Functional%20Requirements%28edited%29.pdf), all seven pages | FR1.1, FR1.4, FR1.7–1.8; FR2.1–2.12; later energy, attention, recommendation, and rescheduling scope. |
| Backlog | [AI_Scheduler_Product_Backlog.xlsx](AI_Scheduler_Product_Backlog.xlsx), Product Backlog and Read Me sheets | PB-001/002 account isolation; PB-004/005 task inputs; PB-006 fixed/flexible; PB-007/008/010 later behavior; PB-041 event-to-task integration. |
| Sprint plan | [AI_Scheduler_Sprint_Plan.xlsx](AI_Scheduler_Sprint_Plan.xlsx), Sprint Roadmap, Sprint Backlog, Team Lanes, Read Me | S01 includes PB-001/002/004/005/023/029; S02 includes PB-006/007/008/010; S03 includes generated-schedule interaction. |
| Supporting text | [features.txt](../data/features.txt), [non fun.txt](../data/non%20fun.txt), and [project chapters](../Graduation_Project_Chapters_1-2.docx), sections 1.5–1.8 | Task setup, security/privacy, modular ownership; chapters reproduce the relevant FR wording. Literature descriptions of other products are not requirements for this schema. |

The repository contains multiple diagram versions, but no separate approval/sign-off record was located. Consequently, this proposal identifies the exact files used and does not claim that one conflicting version supersedes another merely because of its filename or revision number.

The following discrepancies affect the design. “Proposed resolution” means a choice presented for review; existing source documents have not been rewritten.

| ID | Finding and source | Proposed resolution / approval implication |
|---|---|---|
| C1 | The sprint workbook includes energy-input PB-023 and attention-input PB-029 in S01, while the user's current task excludes them and includes fixed commitments currently placed in S02 as PB-006. | Follow the user's explicitly requested scope. Include fixed/flexible and timetable inputs; exclude energy and attention tables. The workbook needs reconciliation if this scope is adopted. |
| C2 | Master PNG has FixedTask/FlexibleTask subclasses; Master SVG and ERD flatten their fields. PNG places priority/deadline on FlexibleTask; SVG/ERD place them on Task. | Use one `tasks` table with a `task_type` discriminator and conditional fixed-time checks. This can map to SQLAlchemy single-table inheritance without subclass tables. Follow the user's task-attribute scope and SVG/ERD by permitting priority/deadline on either type. That attribute-placement difference remains an explicit diagram reconciliation item. |
| C3 | FR2.11 says classify Fixed/Flexible “based on its category”; no category-to-type mapping is supplied. The current task asks users to define task type. | Persist explicit `task_type` independently of category. Do not assume all STUDY tasks are flexible or all WORK tasks fixed. Approval should accept this interpretation or provide a classification rule; no invented mapping is encoded. |
| C4 | ERD TaskStatus is Pending/InProgress/Completed/Skipped/Cancelled; both Master versions use UNSCHEDULED/SCHEDULED/IN_PROGRESS/COMPLETED/SKIPPED/RESCHEDULED. | Use the Master's `UNSCHEDULED` as the sole Sprint 1 persisted task state. Later status transitions require a later migration and their own approved behavior. Do not introduce Pending or Cancelled as aliases. |
| C5 | Master EventStatus is EXTRACTED/PENDING_REVIEW/SAVED/DELETED/DISCARDED; ERD instead has Scheduled/Cancelled/Completed. Master EventSource only lists GOOGLE_CALENDAR/EMAIL; ERD includes Manual/Google/Microsoft. | A manually entered Sprint 1 event has `status = 'SAVED'` and `source = 'MANUAL'`. SAVED follows the Master. MANUAL is required by the user's manual commitments scope and extends the Master's source vocabulary; this amendment must be acknowledged. Imported-event lifecycles and provider vocabularies are deferred. |
| C6 | Master Event contains date/time and an extraction-related deadline, but no end time. ERD uses start_at/end_at and omits deadline/participants. | One-off commitments need an explicit start/end interval. `end_at` is an event ending, never a renamed extraction deadline. Extraction deadlines and participants are deferred to the integration scope. |
| C7 | Neither Master nor ERD defines weekly recurrence. | The user explicitly requested it during this review. Add the bounded-or-open-ended weekly rule described below to `events`; update the Event design when the proposal is approved. Do not mislabel it as already present in the approved diagrams. |
| C8 | The ERD leaves category_enum as “existing project category definitions.” Priority has no range; class.png says importance while Master/ERD say priority. | Use the five categories explicitly listed in both Master category enums. Store exactly one integer `priority` as user-entered importance, constrained to 1–5 as the user clarified. No second importance field or calculated score is added. |
| C9 | Master User includes email and claims identity ownership; the current stack and ERD delegate credentials to Supabase Auth. FR1.1 specifies Google sign-in. | `profiles.id` is the Supabase user UUID. Email/credentials remain in Auth. A logical User response may obtain email from Auth without storing a copy. Google sign-in can be provided through Supabase Auth; calendar/Gmail authorization is a separate later integration, not an application password/account table. |
| C10 | Ownership diagrams show Event triggering Task creation. The full ERD has tasks.source_event_id and schedule_items.task_id only. | Defer event-to-task conversion to PB-041. A Sprint 1 lecture/shift lives once in events and is not cloned into tasks. Before adding generated schedules, decide how allocations/constraints refer to events without double-booking the same commitment. The full ERD's task-only allocation model does not settle that issue. |
| C11 | Master describes one UserPreferences per User; a UNIQUE foreign key alone only guarantees at most one. | Enforce one preferences row per existing profile using the proposed profile-insert trigger and controlled child deletion described below. Auth users may temporarily have no application profile during onboarding. |
| C12 | Integration diagrams describe isolated services/read projections, while the ERD draws physical foreign keys across modules. | For this requested Supabase foundation, keep one application schema with explicit logical owners and no duplicate business entities. This proposal does not decide the deployment/storage architecture of later modules or implement their cross-service foreign keys. |

The table scope is deliberately limited to persistent Sprint 1 concepts.

| Candidate | Decision | Responsibility and requirement justification |
|---|---|---|
| `profiles` | Include | Application profile attached to Supabase identity; basic user information. PB-001; current user foundation scope. |
| `user_preferences` | Include | The Master's one-to-one preferences object: timezone, notification preference, preferred work hours. Current preferences requirement. Storing a notification preference does not implement notifications. |
| `tasks` | Include | Activities to complete; task CRUD, type, deadline, estimated duration, category, importance, and fixed-task constraints. PB-004/005; FR2.1/2.2; requested fixed/flexible input foundation. |
| `events` | Include | Fixed commitments, including appointments, university lectures and work shifts, either one-off or weekly repeating. Current scope and recurrence clarification; input foundation for FR2.6. |
| `schedules` | Exclude | Master Schedule/ERD schedules represents generated plans with generated_at, validity, and lifecycle. Generation/save behavior belongs to PB-008/009/013 and FR2.3/2.4/2.9, outside this task. A user's university timetable is input, not one of these generated plans. |
| `schedule_items` | Exclude | Actual allocated slots, corresponding to TimeSlot. Nothing is being allocated or optimized in Sprint 1. Event recurrence rules do not require materialized schedule items. |

Also exclude external_accounts/sync_sessions, notifications, sleep/activity/energy/burnout/attention tables, decision scores/recommendations/explanations/history, and rescheduling requests/snapshots/changes. Each belongs to excluded functionality. Exclude a separate users table, login/password/session tables, categories/priorities lookup tables, FixedTask/FlexibleTask tables, and a persisted UserTaskTable/count table. Supabase owns authentication; the five categories and two task types are fixed vocabularies; UserTaskTable is a collection/repository abstraction and its count is derived. No course, employer, semester, participant, or calendar-container entity has been required.

The proposed column definitions below are complete for this scope. All application tables are in `public`. `—` means no explicit database default; nullable fields are NULL when omitted. Unless stated otherwise, a non-key column has no uniqueness constraint. All listed PKs are unique and NOT NULL. Check names referenced in the tables are defined immediately after the corresponding table. Defaults and nullable profile fields are proposed onboarding choices, not source requirements.

Use PostgreSQL `uuid`, `timestamptz`, `date`, `time(0) without time zone`, `boolean`, `integer`, and bounded `varchar` types. Named CHECK constraints on strings are proposed instead of native PostgreSQL ENUM types, so later approved vocabulary changes do not require ENUM surgery. The stored vocabulary is uppercase and matches the Master where applicable.

**`profiles`** stores application identity details only. There is no password, email, provider token, or separate authentication user ID column.

| Column | PostgreSQL type | Key / reference | Nullability | Default | Unique / check |
|---|---|---|---|---|---|
| id | uuid | PK; FK → auth.users(id) | NOT NULL | —; use authenticated Auth UUID | PK; never generate a different UUID |
| username | varchar(100) | — | NULL | — | UNIQUE; P1 when supplied |
| full_name | varchar(200) | — | NULL | — | P2 when supplied |
| created_at | timestamptz | — | NOT NULL | now() | Server-managed |
| updated_at | timestamptz | — | NOT NULL | now() | Server-managed on every update |

P1, `ck_profiles_username_nonblank`: `username IS NULL OR (username = btrim(username) AND username ~ '[^[:space:]]')`.

P2, `ck_profiles_full_name_nonblank`: `full_name IS NULL OR full_name ~ '[^[:space:]]'`.

`uq_profiles_username` is ordinary case-sensitive uniqueness under a suitable deterministic database collation, following the ERD's UNIQUE declaration without inventing a case-insensitive username policy. Multiple NULL usernames are permitted. Username/full_name can be completed after Auth signup; neither is used as an alternative authentication credential. Case-insensitive username identity, if wanted, needs a separate explicit decision before implementation.

**`user_preferences`** preserves the Master's `preferenceId` as well as its unique user association; the surrogate ID is retained for diagram compatibility.

| Column | PostgreSQL type | Key / reference | Nullability | Default | Unique / check |
|---|---|---|---|---|---|
| preference_id | uuid | PK | NOT NULL | gen_random_uuid() | PK |
| user_id | uuid | FK → profiles(id) | NOT NULL | — | UNIQUE: uq_user_preferences_user_id |
| timezone | varchar(100) | — | NOT NULL | 'UTC' | U1; timezone-name validation |
| notifications_enabled | boolean | — | NOT NULL | false | Preference only |
| preferred_work_start | time(0) without time zone | — | NULL | — | U2 |
| preferred_work_end | time(0) without time zone | — | NULL | — | U2 |
| updated_at | timestamptz | — | NOT NULL | now() | Server-managed on every update |

U1, `ck_user_preferences_timezone_nonblank`: `timezone = btrim(timezone) AND timezone ~ '[^[:space:]]'`.

U2, `ck_user_preferences_work_hours`: both work-time fields must be NULL, or both must be non-NULL, each less than `TIME '24:00:00'`, and unequal. If end is earlier than start, the preference crosses midnight into the following day. Equal endpoints do not mean an implicit 24-hour interval. No work-hour default is inferred from the user's location or role.

`timezone` is an IANA timezone name such as `Africa/Cairo`, or `UTC`. Validate it against supported timezone names in the application and with a database write-time validator using `pg_timezone_names`; a nonblank CHECK alone is insufficient. Do not put a subquery against that view into a CHECK. A table-reading validation function must not be declared IMMUTABLE merely to use it as a CHECK. The default UTC is a fallback until the user supplies their preference, not an assumption that they live in UTC.

**`tasks`** stores work to complete. `priority` is the user's importance input, not a computed decision score. `context` provides the existing Master's optional task context/details without adding a second overlapping description field.

| Column | PostgreSQL type | Key / reference | Nullability | Default | Unique / check |
|---|---|---|---|---|---|
| task_id | uuid | PK | NOT NULL | gen_random_uuid() | PK |
| user_id | uuid | FK → profiles(id) | NOT NULL | — | Owner |
| title | varchar(500) | — | NOT NULL | — | T1 |
| task_type | varchar(8) | — | NOT NULL | — | T2: FIXED or FLEXIBLE |
| deadline | timestamptz | — | NULL | — | T3: finite when supplied |
| estimated_duration_minutes | integer | — | NOT NULL | — | T4: greater than zero |
| category | varchar(20) | — | NOT NULL | — | T5: approved category vocabulary |
| priority | integer | — | NOT NULL | — | T6: BETWEEN 1 AND 5 |
| fixed_start_at | timestamptz | — | NULL; required for FIXED | — | T7 |
| fixed_end_at | timestamptz | — | NULL; required for FIXED | — | T7 |
| status | varchar(20) | — | NOT NULL | 'UNSCHEDULED' | T8: only UNSCHEDULED in Sprint 1 |
| context | varchar(500) | — | NULL | — | No uniqueness |
| created_at | timestamptz | — | NOT NULL | now() | Server-managed |
| updated_at | timestamptz | — | NOT NULL | now() | Server-managed on every update |

The named task checks are:

| Check | Exact expression / rule |
|---|---|
| T1: ck_tasks_title_nonblank | `title ~ '[^[:space:]]'` |
| T2: ck_tasks_type | `task_type IN ('FIXED', 'FLEXIBLE')` |
| T3: ck_tasks_deadline_finite | `deadline IS NULL OR isfinite(deadline)` |
| T4: ck_tasks_duration_positive | `estimated_duration_minutes > 0` |
| T5: ck_tasks_category | `category IN ('STUDY', 'WORK', 'PERSONAL', 'HEALTH', 'MEETING')` |
| T6: ck_tasks_priority_range | `priority BETWEEN 1 AND 5` |
| T7: ck_tasks_fixed_interval | The conditional expression below. |
| T8: ck_tasks_sprint1_status | `status = 'UNSCHEDULED'` |

T7 must explicitly check NULLs; PostgreSQL CHECK accepts an UNKNOWN result, so a simple comparison alone would not enforce the required endpoints:

```sql
(task_type = 'FLEXIBLE'
 AND fixed_start_at IS NULL
 AND fixed_end_at IS NULL)
OR
(task_type = 'FIXED'
 AND fixed_start_at IS NOT NULL
 AND fixed_end_at IS NOT NULL
 AND isfinite(fixed_start_at)
 AND isfinite(fixed_end_at)
 AND fixed_end_at > fixed_start_at)
```

The fixed endpoints are user-supplied constraints, not generated allocations. A fixed task can therefore be UNSCHEDULED while it already has a required interval. A flexible task has no fixed endpoints and may have a deadline without an allocation. Changing task type and its conditional fields must be one atomic update.

Deadline is optional because the sources require the ability to specify it, not a deadline on every task. Do not reject past deadlines or past commitments with a `now()` CHECK: records can become overdue or historical without becoming corrupt. Do not silently force estimated duration to equal a fixed interval, invent a maximum duration, or add deadline-versus-fixed-end feasibility rules not defined by the requirements. If those input rules are desired, approve them separately; their omission does not promise that every input workload is schedulable.

`is_interruptible`, task splitting, completion/skipping timestamps and transitions, recommendation scores, and `source_event_id` are deferred. The status column records the initial Master state without implementing PB-007 or schedule generation. Later scope can expand its named CHECK to approved Master states.

**`events`** stores fixed commitments independently from tasks. One row is either one dated commitment or one weekly recurrence rule. It is not both. A weekly row does not also persist a duplicate first occurrence in start_at/end_at.

| Column | PostgreSQL type | Key / reference | Nullability | Default | Unique / check |
|---|---|---|---|---|---|
| event_id | uuid | PK | NOT NULL | gen_random_uuid() | PK |
| user_id | uuid | FK → profiles(id) | NOT NULL | — | Owner of commitment/rule and all derived occurrences |
| title | varchar(500) | — | NOT NULL | — | E1 |
| location | varchar(500) | — | NULL | — | No uniqueness |
| category | varchar(20) | — | NOT NULL | — | E2 |
| recurrence_type | varchar(6) | — | NOT NULL | 'ONCE' | E3: ONCE or WEEKLY |
| start_at | timestamptz | — | NULL; required for ONCE | — | E4 |
| end_at | timestamptz | — | NULL; required for ONCE | — | E4 |
| recurrence_first_date | date | — | NULL; required for WEEKLY | — | E4; actual first occurrence's local start date |
| recurrence_until_date | date | — | NULL | — | E4; inclusive last permitted local start date |
| recurrence_start_time | time(0) without time zone | — | NULL; required for WEEKLY | — | E4 |
| recurrence_end_time | time(0) without time zone | — | NULL; required for WEEKLY | — | E4 |
| recurrence_end_day_offset | smallint | — | NULL; required for WEEKLY | — | E4; nonnegative local calendar days after occurrence start date |
| recurrence_timezone | varchar(100) | — | NULL; required for WEEKLY | — | E4; supported IANA timezone-name validation |
| source | varchar(20) | — | NOT NULL | 'MANUAL' | E5: only MANUAL in Sprint 1 |
| status | varchar(20) | — | NOT NULL | 'SAVED' | E6: only SAVED in Sprint 1 |
| created_at | timestamptz | — | NOT NULL | now() | Server-managed |
| updated_at | timestamptz | — | NOT NULL | now() | Server-managed on every update |

E1, `ck_events_title_nonblank`: `title ~ '[^[:space:]]'`.

E2, `ck_events_category`: `category IN ('STUDY', 'WORK', 'PERSONAL', 'HEALTH', 'MEETING')`.

E3, `ck_events_recurrence_type`: `recurrence_type IN ('ONCE', 'WEEKLY')`.

E5, `ck_events_sprint1_source`: `source = 'MANUAL'`. E6, `ck_events_sprint1_status`: `status = 'SAVED'`.

E4, `ck_events_time_shape`, is the following mutually exclusive rule. All required conditional fields are checked for non-NULL explicitly:

```sql
(
    recurrence_type = 'ONCE'
    AND start_at IS NOT NULL
    AND end_at IS NOT NULL
    AND isfinite(start_at)
    AND isfinite(end_at)
    AND end_at > start_at
    AND recurrence_first_date IS NULL
    AND recurrence_until_date IS NULL
    AND recurrence_start_time IS NULL
    AND recurrence_end_time IS NULL
    AND recurrence_end_day_offset IS NULL
    AND recurrence_timezone IS NULL
)
OR
(
    recurrence_type = 'WEEKLY'
    AND start_at IS NULL
    AND end_at IS NULL
    AND recurrence_first_date IS NOT NULL
    AND isfinite(recurrence_first_date)
    AND (recurrence_until_date IS NULL
         OR (isfinite(recurrence_until_date)
             AND recurrence_until_date >= recurrence_first_date))
    AND recurrence_start_time IS NOT NULL
    AND recurrence_end_time IS NOT NULL
    AND recurrence_start_time < TIME '24:00:00'
    AND recurrence_end_time < TIME '24:00:00'
    AND recurrence_end_day_offset IS NOT NULL
    AND recurrence_end_day_offset >= 0
    AND (recurrence_end_day_offset > 0
         OR recurrence_end_time > recurrence_start_time)
    AND recurrence_timezone IS NOT NULL
    AND recurrence_timezone = btrim(recurrence_timezone)
    AND recurrence_timezone ~ '[^[:space:]]'
)
```

The weekly rule has these precise semantics:

1. Occurrence start dates are `recurrence_first_date + 7 × n` calendar days, for integers n ≥ 0. Stop when the start date exceeds recurrence_until_date, if supplied. The first date is an actual occurrence, so there is no redundant stored weekday column. The UI can derive its weekday.
2. Combine each local start date with recurrence_start_time. Combine the same date plus recurrence_end_day_offset with recurrence_end_time for its ending. Resolve each endpoint in recurrence_timezone. Offset 0 is same-day; offset 1 allows an overnight shift, including an explicit 24-hour local interval. This field avoids guessing whether equal endpoints mean zero or 24 hours.
3. The until date bounds occurrence starts. An overnight occurrence may end after that date. NULL until means the weekly commitment continues until the user changes/removes the rule; occurrence expansion always uses a finite requested date window.
4. A Monday/Wednesday lecture occupies two distinct weekly input rules, one per weekday/time interval. Their matching titles do not make them duplicate records. Grouped courses and bulk editing of a named series across several weekdays are not required by the supplied scope.
5. The event's recurrence timezone is independent from user_preferences.timezone. Changing a display/work preference must not move an established lecture in its institution's timezone. UTC timestamps alone cannot retain a recurring 09:00 local start through daylight-saving changes.
6. Do not obtain the next occurrence by adding 168 hours to a UTC timestamp. Resolve the next local date/time using current timezone rules. This is interpretation of a saved commitment, not task allocation or optimization.
7. Proposed daylight-saving policy for approval: detect ambiguous or nonexistent local endpoints and surface a validation error requiring an unambiguous time/rule, instead of silently selecting an offset or shifting a commitment. Validate that a resolved occurrence ends after it starts. SQL row checks cannot prove this for all future timezone transitions; occurrence resolution must perform this validation. No per-occurrence exception/fold field is claimed to exist in this design.
8. Edits/deletion apply to the whole stored weekly rule. “Only this occurrence,” holiday exceptions, alternating weeks, monthly recurrence, recurrence import/export, and an occurrence cache are not included. They need explicitly scoped exception/recurrence design if requested later.

For example, a weekly work shift beginning on Monday 2026-11-16 at 22:00 in Africa/Cairo and ending at 06:00 the next day has first_date 2026-11-16, start_time 22:00, end_time 06:00, end_day_offset 1, and timezone Africa/Cairo. It produces fixed input commitments on successive Mondays in the selected date window. A task such as “finish assignment” instead belongs in tasks with a duration, importance, and optional deadline. A time-constrained piece of work can be a FIXED task; a lecture attendance commitment is an event. A commitment is never automatically represented in both tables.

No events columns store estimated task duration, task priority, task type, or generated slot placement. Event priority hints, is_important, extraction deadlines/participants, provider IDs/accounts, import deduplication, and cancellation/review workflows are deferred. No uniqueness constraint is added on user/title/time because the current requirements do not prohibit legitimately repeated labels or coincident input commitments.

The relationships and deletion rules are as follows. Every proposed FK uses **ON UPDATE RESTRICT** because identity/owner keys are immutable. All four proposed FKs use **ON DELETE CASCADE** in the parent-to-child direction.

| Parent → child | Cardinality | Foreign key | Meaning / deletion result |
|---|---|---|---|
| auth.users → profiles | 1 → 0..1 physically; one profile after application onboarding | profiles.id → auth.users.id | Existing Auth identity is mandatory for every profile. Deleting the Auth user removes the application profile. Deleting a profile never deletes auth.users. |
| profiles → user_preferences | 1 → 1 after profile provisioning | user_preferences.user_id → profiles.id, UNIQUE | Each profile gets one preferences record; profile deletion removes it. UUID preference_id does not create another owner. |
| profiles → tasks | 1 → 0..many | tasks.user_id → profiles.id | Tasks have exactly one existing profile owner; profile deletion removes them. |
| profiles → events | 1 → 0..many | events.user_id → profiles.id | Each one-off event or weekly rule has exactly one existing profile owner; profile deletion removes it and therefore its derived occurrences. |

There are no task-to-event, task-to-schedule, preferences-to-event, or many-to-many foreign keys in Sprint 1. A recurrence occurrence has no separate persisted row or owner. The owner is inherited from its event rule. No SET NULL deletion is appropriate for ownership, because an ownerless personal record is invalid. Individual task/event deletion is a hard delete; there are no histories or allocations to preserve in this scope. Account deletion is not added as a Sprint 1 feature simply because its eventual cascading behavior is defined.

For one-to-one provisioning, propose an AFTER INSERT trigger **on public.profiles**, which inserts the default user_preferences row in the same transaction using NEW.id. Deny ordinary clients direct insertion/deletion of preferences and updates to preference_id/user_id. An authenticated, idempotent application onboarding operation inserts its own profile after Supabase signup/login. A failed application transaction can be retried without creating another Auth user. Do not add a signup trigger to auth.users: the task expressly forbids changing that managed table. A UNIQUE FK by itself must not be described as enforcing the existence of the child row.

All profile/task/event inserts and updates must preserve the owner, and all child rows must reference that same authoritative profiles record. There are no independent parent references in the proposed tables whose owners could disagree, so composite owner FKs are unnecessary here. If source_event_id or schedule_items is introduced later, use ownership-preserving composite references such as `(user_id, source_event_id)` to a unique `(user_id, event_id)` and equivalent references for schedule/task parents. Independent UUID FKs alone would not prevent a cross-user association. Do not add those columns or supporting unique indexes preemptively.

Supabase Row Level Security and application authorization are both required. UUID unpredictability and foreign keys do not authorize access. Enable RLS on **all four public tables**, remove unintended broad grants/policies, and use owner policies for authenticated callers. The proposed policy matrix is:

| Table | SELECT condition | INSERT condition | UPDATE conditions | DELETE |
|---|---|---|---|---|
| profiles | `id = (select auth.uid())` | WITH CHECK same expression; only own profile onboarding | USING and WITH CHECK same expression | No ordinary client policy/grant; controlled account lifecycle only |
| user_preferences | `user_id = (select auth.uid())` | Provisioning trigger only | USING and WITH CHECK same owner expression | No ordinary client policy/grant; parent cascade only |
| tasks | `user_id = (select auth.uid())` | WITH CHECK same owner expression | USING and WITH CHECK same owner expression | USING same owner expression |
| events | `user_id = (select auth.uid())` | WITH CHECK same owner expression | USING and WITH CHECK same owner expression | USING same owner expression |

Apply the policies to the authenticated runtime role(s), with no anonymous access. An unauthenticated auth.uid() is NULL and must authorize no rows. Both USING and WITH CHECK are necessary on UPDATE to prevent changing ownership to another user. Column privileges and API schemas must protect IDs, user_id, timestamps, task/event status, and event source from arbitrary client updates. User IDs come from the verified subject, not a trusted request-body owner field. Ordinary profile UPDATE grants cover username/full_name only; preference UPDATE grants cover the preference values only.

Restrict INSERT columns too: profile onboarding supplies id and permitted profile fields; task/event creation supplies the verified owner and permitted input fields. Generated UUIDs, timestamps, status, and source use their database defaults rather than client overrides. Revoke conflicting table-wide INSERT/UPDATE grants before relying on narrower column grants. The profile trigger, not client-supplied IDs/defaults, creates preferences.

Flutter signs in through Supabase Auth. FastAPI verifies the access token's signature, expected issuer/audience, and expiry, and authorizes operations against its verified subject. **A normal SQLAlchemy connection does not automatically carry the JWT into PostgreSQL**. For direct SQLAlchemy access, use a dedicated non-owner, non-superuser role without BYPASSRLS, with the necessary authenticated-role membership/policies and minimum grants. Within each transaction, supply only verified claims through parameterized, transaction-local request-claim settings, so auth.uid() resolves correctly and pooled connections do not retain another user's identity. Keep owner filters in ORM queries as defense in depth. A request with absent/invalid verified identity must fail before private queries run.

Do not perform ordinary user CRUD using a table-owner, postgres, or service-role connection and assume RLS protects it. Those roles can bypass RLS. Separate migration/admin credentials from runtime credentials; never expose privileged credentials in Flutter. The public profile-provisioning function may need tightly limited SECURITY DEFINER rights to insert preferences; schema-qualify references, set a fixed safe search_path, restrict execution, and use NEW.id only. These are implementation requirements for later review, not functions created by this proposal.

Use these indexes for the proposed schema. Primary keys and UNIQUE constraints create their own indexes; PostgreSQL does not automatically index every referencing FK.

| Index / constraint | Columns or predicate | Reason |
|---|---|---|
| Four primary-key indexes | profiles(id); user_preferences(preference_id); tasks(task_id); events(event_id) | Identity lookup and PK enforcement. |
| uq_profiles_username | profiles(username) | ERD username uniqueness; NULL allowed. |
| uq_user_preferences_user_id | user_preferences(user_id) | One preferences record per profile; also covers owner lookup/FK checks. |
| ix_tasks_user_deadline | tasks(user_id, deadline) | Owner-scoped task lists/deadline ordering; leading user_id covers ownership/FK lookup. |
| ix_events_user_id | events(user_id) | Covers every event/rule for ownership checks, listing and cascading deletion. |
| ix_events_once_user_start | events(user_id, start_at) WHERE recurrence_type = 'ONCE' | One-off interval queries: start_at < window_end AND end_at > window_start. end_at is a residual filter. |
| ix_events_weekly_user_first | events(user_id, recurrence_first_date) WHERE recurrence_type = 'WEEKLY' | Find candidate weekly rules for a user's requested date window. Apply date bounds/timezone/overnight semantics when resolving occurrences. |

The two partial event indexes do not replace the full owner index: they omit the other recurrence shape and cannot be assumed to support all FK/cascade queries. For recurring intervals, a query must also consider occurrences beginning before its date window that end inside it, especially overnight commitments. A date index is a candidate filter, not an occurrence engine. Do not add indexes on every enum/boolean or a generated-slot exclusion constraint before relevant queries exist.

Sprint 1 accepts conflicting input commitments because real university/work calendars can conflict. FR2.5 restricts overlap in a **generated schedule**; it does not forbid recording conflicting source inputs. Therefore no exclusion constraint across tasks/events/recurrence rules is proposed. A later generated-allocation design may use GiST ranges and btree_gist, but it must first define splitting, event references, and overlap policy. That extension is not required here.

No new PostgreSQL extension is required for this proposal on a modern Supabase PostgreSQL version: `gen_random_uuid()` is built in on PostgreSQL 13+, as are timestamptz/date/time and the required checks/B-tree indexes. Do not enable uuid-ossp, pgcrypto, btree_gist, pg_cron, or recurrence extensions merely because later modules might use them. The remote PostgreSQL version was deliberately not queried and can be verified at implementation time.

Use timezone-aware values for one-off event endpoints, fixed-task endpoints, and deadlines. PostgreSQL timestamptz stores instants, not an original IANA timezone name. Store/read instants consistently with UTC database sessions and convert for presentation. Weekly calendar rules instead need local dates/times plus their own IANA timezone. Validate recurrence_timezone using the same supported-name mechanism as user_preferences.timezone. Work/recurrence `time(0)` values exclude PostgreSQL's special 24:00 value so they map cleanly to Python/SQLAlchemy time objects; an ending at next-day midnight uses time 00:00 with the explicit day offset.

Use `DEFAULT now()` for creation/update timestamps, and a database BEFORE UPDATE trigger on each application table to set updated_at on every write. Created timestamps and identity keys are immutable to ordinary clients. An SQLAlchemy-only onupdate callback would miss writes through other authorized database paths. A column default alone does not refresh a timestamp after UPDATE.

This design maps directly to SQLAlchemy and Alembic after approval: UUID types, DateTime(timezone=True), Date, Time, Boolean, Integer/SmallInteger, String, named CheckConstraint/UniqueConstraint/ForeignKey objects, partial Index objects, and explicit migration SQL for RLS/grants/triggers. A single-table discriminator can map either Master task representation. Align ORM deletion behavior with database cascades, including passive_deletes where appropriate; do not let an ORM relationship null a required owner key.

Alembic must manage application objects only. Treat auth.users as an external FK target and explicitly exclude the auth schema and other Supabase-managed objects from autogeneration/creation/deletion. Resolving that external FK in SQLAlchemy metadata must not result in CREATE TABLE auth.users, ALTER TABLE auth.users, a trigger on it, or application Base.metadata.create_all() managing it. Review generated migrations manually. No migration or executable DDL file is supplied in this design phase.

Future modules can attach to stable profile/task/event UUIDs without placeholder tables now. Later generated allocations must remain separate from fixed constraints and recurrence inputs. Later integration must define event-to-task provenance, same-owner constraints, and duplicate-busy-time handling. Recurrence exceptions and historical snapshots need their own requirements; editing a rule in this Sprint 1 design changes its interpreted occurrences because no historical occurrence store is claimed.

Final design review:

| Required review | Result |
|---|---|
| Every table supports Sprint 1 | Yes: profile, preferences, task inputs, and fixed/weekly commitments are explicitly required. |
| Every relationship is justified | Yes: one existing Auth identity, one application profile, one preferences object, and owned task/event collections. |
| No duplicated business ownership | Yes: User Service owns profile/preferences; Scheduler owns Task; Event remains a distinct authoritative commitment. No task copies, projections, or persisted occurrences are added. |
| No contradiction with approved Class Diagram | **Conditional, not an unconditional pass.** Domain separation and identity references are preserved, but C2–C7 and C9 require the documented mappings/amendments to be accepted. The repository itself contains conflicting Master versions and enums; approval cannot be inferred. |
| SQLAlchemy/Alembic implementation is practical | Yes, subject to implementing the explicit runtime RLS identity bridge, public-table triggers, and exclusion of managed Auth objects. No implementation has been performed. |
| Future modules remain possible without premature implementation | Yes: generated schedules, allocations, analytics, integrations, and optimization remain deferred. |

Documentation validation compared all 44 application columns between the report and PlantUML source and checked source links. Hash comparison confirmed that the 21 original repository files were unchanged and `code/` remained empty. The PlantUML source was inspected but not rendered with a PlantUML compiler in this environment. Database constraints, RLS policies, and migrations have not been executed; runtime verification belongs to implementation after approval.

Review the proposal as one concrete design, particularly the documented diagram reconciliations, optional onboarding fields/defaults, independent task-type selection, weekly whole-rule semantics, and proposed daylight-saving validation policy. The user's weekly-recurrence and 1–5 importance decisions are already incorporated. Implementation must wait for approval, as requested.
