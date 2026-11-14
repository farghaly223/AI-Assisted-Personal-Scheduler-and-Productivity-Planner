**Phase 3 implementation audit — 14 November 2026**

Authority: the user's Phase 3 implementation request approved `plan/Sprint_1_Database_Design_Proposal.md` and `digrams/masterclass/sprint1_database_proposal.puml`. The user separately authorized a disposable local-only Auth fixture for tests. No remote migration or Auth modification was authorized or performed.

| Approved item | Implementation / evidence |
|---|---|
| Exactly four application tables | Base.metadata contains only public.profiles, user_preferences, tasks, events. Migration adds only these business tables; public.alembic_version is Alembic bookkeeping. |
| Profiles: 5 columns | Auth UUID PK/FK; optional unique username and optional full_name; two nonblank checks; two server timestamps. Direct comparison to proposal passes. |
| Preferences: 7 columns | UUID PK; unique required profile owner; UTC/false defaults; paired work-time validation; update timestamp. Trigger supplies exactly one child per profile; failed onboarding rolls both back. |
| Tasks: 14 columns | Required title/type/duration/category/priority/owner; optional deadline/context; conditional fixed endpoints; UNSCHEDULED-only state; UUID/timestamps. All eight named checks present and invalid inputs tested. |
| Events: 18 columns | Exact ONCE/WEEKLY representation; weekly local first/until dates, local endpoints, day offset, timezone; MANUAL/SAVED constants; no actual allocated slot or extra recurrence table. Six named checks present and invalid shapes tested. |
| Exact columns/types/defaults/nullability | All 44 columns compared automatically against the approved Markdown tables; live PostgreSQL migration compared with ORM metadata. No drift. |
| PK/FK/UNIQUE | Four PKs; four FKs with CASCADE deletion and RESTRICT updates; unique username and preference owner. Missing owners, duplicate keys, and parent-key changes rejected. |
| Checks | 18 named CHECK constraints match metadata and migration. Tests cover range, duration, null branches, ordering, finite dates/instants, categories, statuses, source, blank text, work hours, recurrence bounds, and time(0) endpoints. |
| Indexes | Four explicit indexes: tasks owner/deadline; events owner; partial owner/start for ONCE; partial owner/first-date for WEEKLY. PK/UNIQUE indexes are additional automatic indexes. Alembic reports no index/schema drift. |
| Timezone validation | Two write-time database validators use pg_timezone_names. Python validates supported ZoneInfo names and rejects ambiguous/nonexistent individual local times. No claim that stored rules have been expanded through all future DST transitions. |
| Update timestamps | BEFORE UPDATE triggers on all four application tables; now() defaults. Fetched server defaults are configured in ORM; client timestamp mutation denied by column grants. |
| Preferences lifecycle | AFTER INSERT trigger on public.profiles only. SECURITY DEFINER function uses empty search_path and schema-qualified target; direct execution/child insertion/deletion prohibited. |
| Ownership policies | RLS enabled on all four tables; 13 policies. SELECT/UPDATE/DELETE visibility tests hide other users' rows; forged-owner insertion rejected; ownership-key reassignment prohibited. UPDATE USING and WITH CHECK are both present. |
| Privilege separation | Separate runtime LOGIN inherits authenticated without superuser/BYPASSRLS/admin or owner memberships. Runtime role guard rejects admin connections. Runtime does not read Auth user records. |
| JWT bridge | Real RSA signatures tested; wrong signature/issuer/audience/role/subject, expiry, future times, missing claims, unknown key IDs and unsupported algorithms rejected before acquiring a DB connection. Only verified subject and authenticated role enter transaction-local claim settings. |
| Pool isolation | Single-connection pool reuses the same PostgreSQL backend for two users; identity clears after commit and rollback; unscoped auth.uid() is NULL and unscoped task reads return no rows. |
| Deletion | Profile cascade removes owned inputs/preferences; deleting an application profile does not delete Auth. Auth-to-profile cascade tested only in the explicitly approved local fixture. |
| Managed Auth protection | External FK reference is separate from Base.metadata. Application migration never creates/alters/drops auth objects. Local upgrade/downgrade preserves auth.users OID and auth.uid function definition. |
| Alembic boundaries | Explicit application-table allowlist; no managed-schema autogeneration. Offline generation needs no credentials or connection. Online remote migration is blocked without explicit allow_remote_ddl=true. |
| Future scope excluded | No registration/login or CRUD APIs, Flutter code, schedule/schedule_items tables, Event-to-Task conversion, notifications delivery, recurrence expansion, or scheduling algorithms. |

Validation executed on Windows with Python 3.15.2, PostgreSQL 17.6, and the dependencies captured in `requirements.lock`:

```text
python -m pytest -q
114 passed in 93.10s

python -m alembic upgrade head
Exit code 0 (disposable manual-test database)

python -m app.db.check
Connection OK; restricted role and RLS prerequisites verified; no scoped user identity.
This check alone does not prove cross-user isolation; run the integration tests.

python -m alembic check
No new upgrade operations detected.
```

The final full test run had no failures, skips, or warnings. A prior run found and prompted fixes for PostgreSQL schema-name reflection and an invalid-value test that hit VARCHAR length validation before a CHECK; these were corrected before the passing run. No approved schema field or constraint was changed to make a test pass.

The complete generated SQL for review is [upgrade](0001_sprint1_upgrade.sql) and [downgrade](0001_sprint1_downgrade.sql). The authoritative migration source contains both directions in `alembic/versions/0001_sprint1_foundation.py`; it is independent of mutable application model imports.

Unverified remote prerequisites: actual Supabase admin/runtime role permissions, the project's connection/pooler support for a restricted login, TLS certificates, and asymmetric JWT signing configuration. Legacy HS256 is intentionally unsupported rather than accepted without verification. The tests use real PostgreSQL constraints/RLS and real JWT cryptography, but an explicitly authorized minimal Auth fixture, so they are not evidence that the untouched remote project is already configured correctly.

Original design/diagram files remain unchanged. The local server is stopped after validation; the ignored local cluster and disposable manual-test database remain available for review and restart. Remote deployment requires the user's explicit subsequent approval.
