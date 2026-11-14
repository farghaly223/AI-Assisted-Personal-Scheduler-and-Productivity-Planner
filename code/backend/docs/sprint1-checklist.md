# Sprint 1 backend audit and progress

Audit date: 2026-11-14. Scope: backend only. The current user request is the implementation authority; routine design/implementation approval gates in skills are waived by that request.

Handover status: Sprint 1 backend implementation and local automated verification are complete. The final full suite passed **235 tests in 114.20s**, with no failures, skips, or warnings. External-service and tooling verification limits are recorded below.

## Initial read-only audit

- Read the backend source, tests, configuration, migration, README, prior audit, approved schema, and repository AGENTS.md.
- No application entry point, routers, API schemas, or services existed. `dependencies.py` was a future integration helper only.
- The four approved models, 44 columns, constraints, triggers, RLS policies, JWT verification, and transaction-local identity were implemented.
- Actual baseline: `python -B -m pytest -q -p no:cacheprovider`: **114 passed in 69.78s**.
- Actual local checks: connection/role check passed; Alembic current `0001_sprint1 (head)`; `alembic check`: no new upgrade operations.
- Windows blocks git.exe as potentially unwanted software. Git status cannot be verified through Git; preserve existing files and record changes explicitly. Do not disable Windows protection.
- pg_ctl's sandbox status/start commands failed, but actual database connections and all integration tests succeeded. The existing server is reachable; do not initialize another cluster or reset it.
- No Supabase production credentials are needed for local tests. Supabase Auth HTTP transport must be simulated in tests, with real JWT cryptography and real PostgreSQL/RLS.
- User explicitly permits cleanup of only disposable test databases created by this task. Existing databases/data remain protected.
- Schema compatibility: weekday is derived from recurrence_first_date, importance is priority 1–5, task status UNSCHEDULED, event status SAVED/source MANUAL. No schema amendment is required.

## Requirement checklist

| Requirement | Initial state | Final evidence/status |
|---|---|---|
| Shared Base, engine, sessions, Alembic | Implemented | Complete: shared module registry, full-metadata drift checks, and temporary-module test |
| Database schema, relationships, constraints, triggers, indexes | Implemented | Complete: original four tables/44 columns preserved; database tests retain core policy/trigger checks |
| JWT verification and reusable authentication dependency | Implemented | Complete: valid/invalid/expired/signature/issuer/audience/role/subject/claim-boundary tests |
| RLS, ownership and pooled identity cleanup | Implemented | Complete: all four tables tested; identity cleanup after commit/rollback and connection reuse |
| FastAPI startup, /api/v1 routers and health | Missing | Complete: application tests and live Uvicorn liveness/readiness/docs smoke |
| Consistent errors, request validation and HTTP statuses | Missing | Complete: typed schemas, sanitized error handlers and API failure/rollback tests |
| Central settings, safe secrets and separate credentials | Partial | Complete: settings guards, placeholder .env.example, ignored local secrets and unsafe-runtime tests |
| Registration using Supabase Auth | Missing | Complete: delegated signup, confirmation-required behavior, validated metadata and safe error tests |
| Login and token handling | Missing | Complete: delegated login, verified returned identity and refresh rotation; no password persistence |
| Basic profile information and missing-profile handling | Missing API | Complete: own GET/PATCH, idempotent verified onboarding, uniqueness fallback and rollback tests |
| Preferences retrieval/update and automatic defaults | Missing API | Complete: own GET/PATCH, validated timezone/work window and one-to-one/default-trigger tests |
| Task create/read/list/update/delete | Missing API | Complete: real API/DB CRUD and persistence tests |
| FIXED/FLEXIBLE, optional deadline, positive duration | Missing API | Complete: schemas plus database constraints; merged PATCH/type-switch tests |
| Task category, importance 1-5, fixed intervals and status | Missing API | Complete: validated category/priority/intervals; UNSCHEDULED remains server-managed |
| Fixed commitments and university/work schedules | Missing API | Complete: Event CRUD and weekly timetable API tests; separate from Tasks |
| ONCE/WEEKLY dates, weekday, timezone and boundaries | Missing API | Complete: local rule validation, derived weekday, overnight/DST endpoint tests; interpretation limits below |
| Cross-user read/update/delete prevention | Missing API tests | Complete: API tests for Tasks/Events; profile/preferences ownership and direct RLS tests |
| Database persistence, rollback and error cleanup | Partial | Complete: real PostgreSQL CRUD, failed commit, onboarding rollback and pooled reuse tests |
| Collection filtering, pagination and query performance | Partial | Complete: bounded pages, has_more, one data SELECT, constant query counts and preserved indexes |
| Temporary module integration | Missing | Complete: shared Base/session/Auth/router, real Alembic autogeneration/ancestry and RLS in disposable test DB |
| OpenAPI endpoint schemas, examples and errors | Missing | Complete: 19 documented operations; exported JSON matches current application |
| Frontend teammate API reference | Missing | Complete: docs/api-reference.md; backend contracts only |
| Architecture/module/migration/contribution guides | Missing | Complete: guides and exact file/registration/migration/test/PR patterns |
| Reproducible local environment and commands | Partial | Complete for native PostgreSQL/tests/server; optional local Supabase Docker path documented but unexecuted |
| Frontend and remote Supabase untouched | Required | Complete: no frontend edits, remote connections or remote migrations performed |

## Decisions

Extend the existing foundation with synchronous FastAPI routes (database and HTTP clients run in worker threads). Public Auth routes use only Supabase publishable/anon credentials, never service-role keys. Profiles are provisioned idempotently after verified authentication; the existing profile trigger creates preferences atomically. PATCH validates the merged stored record; server-managed identity/status/source/timestamp fields cannot be submitted. Lists use bounded limit/offset pagination and deterministic ordering. Optional modules register through trusted configuration and share metadata, dependencies, and one Alembic chain.

Loopback HTTP Auth endpoints are permitted only in explicitly selected development mode for local Supabase; remote Auth always requires HTTPS. Recurrence input validation resolves the first and bounded final occurrence endpoints; future consumers must validate endpoints when interpreting any intervening/future occurrence. No occurrence table, expansion API, or scheduling engine is added.

## Final verification evidence

The baseline and final full-suite results were obtained during this task; the following are actual executions, not expected results.

| Verification | Result |
|---|---|
| Initial read-only full suite | 114 passed in 69.78s |
| Final `python -B -m pytest -q -p no:cacheprovider` | 235 passed in 114.20s; no failures/skips/warnings |
| Follow-up JWT claim regression selection | 15 passed in 0.13s, including signed oversized exp/iat integers |
| `python -m pip check` | No broken requirements found; reconfirmed at handover |
| `python -m app.db.check` | Restricted role/RLS prerequisites and no scoped identity; reconfirmed at handover |
| `python -m alembic current` | 0001_sprint1 (head); reconfirmed at handover |
| `python -m alembic check` | No new upgrade operations; reconfirmed at handover |
| Live Uvicorn smoke | Liveness, restricted DB readiness, Swagger, security headers and 19-operation OpenAPI passed |
| Export/application OpenAPI equality | docs/openapi.json matches the current application; 19 operations |
| Approved design preservation | SHA-256 comparisons confirmed unchanged core models, initial migration, both review SQL files, approved design and PlantUML |

The full suite uses real JWT cryptography and a newly created random local PostgreSQL database with real constraints, triggers, ownership and RLS. Only external Supabase HTTP/JWKS transport is simulated. Task-created disposable databases/roles were cleaned up under the user's explicit permission. Existing databases/data were not reset or deleted. The temporary Uvicorn process was stopped after the smoke test; the existing PostgreSQL server remains available.

Tests providing the evidence:

- `tests/test_auth_and_config.py`: JWT/security/configuration, approved 44-column design, DST and JWKS refresh behavior.
- `tests/test_database.py`: real schema/constraints/indexes, core RLS/grants/triggers, four-table ownership, migrations and pooled transaction identity.
- `tests/test_application.py`: application/health/configuration/errors and OpenAPI examples/security.
- `tests/test_api_schemas.py`: Task/Event/preferences validation, boundary inputs and recurrence/DST rules.
- `tests/test_user_api.py`: Auth delegation/confirmation/refresh, profile/preferences, forged identity, secrets, conflicts and failed commits.
- `tests/test_input_api.py`: Task/Event CRUD/persistence, cross-user isolation, merged PATCH, filters, pagination and query counts.
- `tests/test_module_integration.py`: isolated module router/model/dependency reuse and real shared-history autogeneration/RLS.

Review findings were resolved and covered by tests: claimed optional usernames no longer prevent onboarding; registered module tables coexist with the exact core security/schema assertions; malformed JWT NumericDates, including integer overflow, fail safely. No known unresolved code findings remain.

## APIs delivered

All routes use `/api/v1`. Health and Auth routes are public; profile/preferences/Task/Event routes require verified Supabase bearer tokens.

| Method | Route |
|---|---|
| GET | /health, /health/ready |
| POST | /auth/register, /auth/login, /auth/refresh |
| GET, PATCH | /profiles/me, /preferences/me |
| POST, GET | /tasks, /events |
| GET, PATCH, DELETE | /tasks/{task_id}, /events/{event_id} |

See [API reference](api-reference.md) for every request/response contract, validation rule and error. Swagger is `http://127.0.0.1:8000/docs`; the [OpenAPI export](openapi.json) is ready for the frontend teammate.

## Files created and modified

Paths below are relative to `code/backend/`. The handover continuation changed only this checklist and the implementation plan.

Modified:

```text
README.md
pyproject.toml
requirements.lock
.env.example
.gitignore
app/config.py
app/auth.py
app/dependencies.py
app/db/session.py
alembic/env.py
tests/conftest.py
tests/test_auth_and_config.py
tests/test_database.py
```

Created:

```text
app/main.py
app/schemas.py
app/errors.py
app/openapi.py
app/db/registry.py
app/modules/__init__.py
app/routers/__init__.py
app/routers/authentication.py
app/routers/profiles.py
app/routers/tasks.py
app/routers/events.py
app/services/__init__.py
app/services/authentication.py
app/services/profiles.py
app/services/inputs.py
tests/__init__.py
tests/api_support.py
tests/test_application.py
tests/test_api_schemas.py
tests/test_user_api.py
tests/test_input_api.py
tests/test_module_integration.py
CONTRIBUTING.md
docs/backend-architecture.md
docs/module-integration-guide.md
docs/database-migrations.md
docs/api-reference.md
docs/openapi.json
docs/sprint1-checklist.md
docs/superpowers/plans/2026-11-14-sprint1-backend.md
```

No application schema changes or new production revisions were required. `alembic/env.py` now discovers registered module metadata within the same shared history. The temporary module revision exists only inside the isolated test workspace/database.

## Environment and team handover

The [README](../README.md) contains exact fresh-checkout Python/venv/dependency/environment/native PostgreSQL/migration/test/server commands and the optional local Supabase Auth setup. For the existing configured workspace, reuse its environment and database:

```powershell
Set-Location -LiteralPath 'code/backend'
& '.\.venv\Scripts\python.exe' -m app.db.check
& '.\.venv\Scripts\python.exe' -m alembic current
& '.\.venv\Scripts\python.exe' -m alembic check
& '.\.venv\Scripts\python.exe' -B -m pytest -q -p no:cacheprovider
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Teammates use the [architecture guide](backend-architecture.md), [module integration guide](module-integration-guide.md), [migration guide](database-migrations.md) and [contribution workflow](../CONTRIBUTING.md). Module registration is configuration-driven through BACKEND_MODULES; teammates reuse shared Base/session/Auth/Alembic without editing foundation files.

## Verification limits and remaining blockers

- No known backend implementation blocker remains within Sprint 1 scope.
- Live Supabase Auth was not exercised: current local .env lacks Auth configuration. Tests simulate external transport while verifying real cryptography and PostgreSQL behavior. Interactive signup/login requires a configured local/non-production Supabase Auth service.
- Native PostgreSQL/tests/Uvicorn were verified. The optional Docker/local Supabase setup is documented from public CLI source but was not executed because Docker and Supabase CLI were unavailable.
- Weekly validation checks first and bounded final occurrence endpoints; it does not certify intervening/future DST transitions. Later consumers must resolve each interpreted occurrence endpoint. Recurrence expansion/exception management remain outside Sprint 1.
- Windows blocks git.exe. Git status/diff, commits and PR creation could not be performed; the explicit file inventory and preservation checks support review. Windows protection was not bypassed.
- Remote Supabase was neither accessed nor modified. Frontend/Flutter files were not modified. Energy, Attention, Decision Engine, optimization, rescheduling and other teammates' modules were not implemented.
