# Sprint 1 Backend Implementation Plan

> For agentic workers: execute this plan task-by-task in the current session. User authorizes autonomous implementation after the audit; do not pause for routine approvals. Use the available writing-plans and test-driven-development instructions. Referenced executing-plans skill is absent; this plan is executable directly.

**Goal:** Deliver tested Sprint 1 backend APIs and a reproducible team handover.

**Architecture:** Extend existing PostgreSQL models/security and preserve the initial migration. Add one application factory, shared request dependencies, typed schemas, focused routers/services, and configured module registration.

**Tech stack:** Python 3.12+, FastAPI/Pydantic 2, SQLAlchemy 2, Alembic, psycopg, httpx, Supabase Auth.

**Spec:** ../../sprint1-checklist.md and the user's Sprint 1 backend acceptance criteria; approved schema in plan/Sprint_1_Database_Design_Proposal.md at repository root.

**Execution status:** Complete on 2026-11-14. Final full suite: 235 passed in 114.20s, with no failures, skips, or warnings. Local runtime/RLS, Alembic, dependency and live Uvicorn checks passed. Exported OpenAPI matches all 19 application operations. See the checklist for the exact file inventory, evidence and live Auth/Docker/Git verification limits.

## Global constraints

Backend only; no remote Supabase access; no changes to approved core columns/migration; no existing database deletion/reset; only task-created disposable test database cleanup is authorized. Preserve authentication, authorization, RLS, validation, and existing tests. Runtime credentials cannot be privileged. No scheduling/energy/attention/frontend implementations.

## Review focus

1. Password/token validation errors must never echo secrets.
2. PATCH must validate combined stored/new fields, including explicit nulls and recurrence/type switches.
3. Registration with email confirmation must not create an unverified application profile.
4. Commit errors must be raised before sending successful HTTP responses, with rollback/identity cleanup.
5. Module discovery must permit module tables while excluding Supabase-managed schemas.

## Task 1: API foundation and validation

Files: app/config.py, app/auth.py, app/dependencies.py, app/main.py, app/errors.py, app/schemas.py; tests/test_api_schemas.py, tests/test_application.py; pyproject.toml, .env.example.

- [x] Write failing tests for interval validation, ownership/server-field rejection, startup, liveness/readiness, safe errors, local configuration, and OpenAPI security.
- [x] Run targeted tests and confirm intended failures.
- [x] Implement Settings, create_app, reusable request session, schemas, and error handlers. Dependency cleanup must finish before response delivery.
- [x] Run targeted tests; retain all original tests.

## Task 2: Supabase authentication and profiles

Files: app/services/authentication.py, app/services/profiles.py, app/routers/authentication.py, app/routers/profiles.py; tests/test_user_api.py.

- [x] Write/run failing tests for signup/login/refresh protocol, confirmation-required signup, upstream errors, secret redaction, idempotent profile creation, own profile/preferences, conflict handling, and rollback.
- [x] Implement public-key HTTP Auth delegation and verified profile onboarding. Add GET/PATCH profile/preferences APIs.
- [x] Run targeted real-PostgreSQL/API tests with transport-only Auth simulation.

## Task 3: Scheduling inputs

Files: app/services/inputs.py, app/routers/tasks.py, app/routers/events.py; tests/test_input_api.py.

- [x] Write/run failing CRUD, invalid-input, merged PATCH, forged-owner, cross-user, pagination/filter, persistence, DST, and query-count tests.
- [x] Implement owner-filtered SQL queries, defaults, bounded lists, and all task/event CRUD routes.
- [x] Run targeted tests and verify indexes and constant query counts.

## Task 4: Team extension support

Files: app/modules/__init__.py, app/db/registry.py, app/db/session.py, alembic/env.py; tests/test_module_integration.py.

- [x] Write/run a failing temporary-module test proving router, Base/session/auth reuse, migration discovery, shared revision ancestry, and RLS.
- [x] Add trusted module discovery and extend guards/allowlists to registered public application tables.
- [x] Run the isolated module test and original migration/security tests.

## Task 5: Documentation and final verification

Files: docs/backend-architecture.md, docs/module-integration-guide.md, docs/database-migrations.md, docs/api-reference.md, CONTRIBUTING.md, README.md, requirements.lock, docs/sprint1-checklist.md.

- [x] Document exact setup/run/test commands, local Supabase option, endpoint examples/errors, module file patterns, migration review, and Git PR workflow.
- [x] Run the complete suite, local connection/Alembic checks, OpenAPI export, dependency checks, and targeted security/performance review.
- [x] Record actual results, exact changed files, limitations, and protected-scope confirmation. No Git commit while git.exe is unavailable.
