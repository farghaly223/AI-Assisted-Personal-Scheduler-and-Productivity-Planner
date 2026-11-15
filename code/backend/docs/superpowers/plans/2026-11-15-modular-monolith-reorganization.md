# Modular Monolith Reorganization Implementation Plan

**Goal:** Group existing User and Scheduler code by feature while preserving all behavior.

**Architecture:** Move shared infrastructure into `app/core/`. Split the existing model/schema definitions into their owning modules, retaining one Base and a small import-only model discovery file. Keep the existing User and Scheduler router prefixes and their order under `/api/v1`; configured additional modules retain the existing registry workflow.

**Spec:** The user's Feature-Based Modular Monolith task in this session.

**Execution:** Perform the authorized structural changes directly. No tests, server startup, dependency installation, Alembic execution, environment changes, or database access.

## Move map

- `app/config.py` → `app/core/config.py`; correct only the location-derived backend root depth.
- `app/auth.py` → `app/core/security.py`.
- `app/db/session.py` → `app/core/database.py`.
- `app/dependencies.py`, `errors.py`, `openapi.py`, `timezones.py` → their matching `app/core/` files.
- `app/db/registry.py`, `migration_scope.py`, `check.py` → `app/core/registry.py`, `migration_scope.py`, `database_check.py`.
- Auth/profile routers and services → `app/modules/user/routers/` and `services/`.
- Task/Event routers and input service → `app/modules/scheduler/routers/` and `services/`.
- `app/schemas.py` → shared validation/error contracts in `app/core/schemas.py`, User contracts in `app/modules/user/schemas.py`, Scheduler contracts in `app/modules/scheduler/schemas.py`.
- `app/db/models.py` → shared Base/owner FK in `app/core/base.py`, Profile/UserPreferences in User models, Task/Event in Scheduler models; `app/core/models.py` imports the same classes for discovery.
- Replace the old root router aggregator with two module `router.py` aggregators.

## Steps

- [x] Capture existing source and import/reference map; retain an ignored source snapshot for structural review.
- [x] Move whole files, split definitions without changing their bodies, and create needed package markers/aggregators.
- [x] Update main router wiring, model discovery, Alembic environment imports, utility/test imports, and import paths inside the existing temporary-module fixture.
- [x] Remove only obsolete migrated source files/package markers; preserve unrelated user files and caches.
- [x] Update current documentation paths and ownership explanations; leave historical audit/migration records intact.
- [x] Review imports, definitions, router prefixes/order and metadata discovery statically. Compare moved implementations with the saved source; do not import/run the application or test suite.

## Review focus

- Preserve configuration root and environment precedence.
- Preserve one Base, all model/table/relationship definitions, and eager model registration.
- Keep User model forward relationships and Scheduler's Profile relationship resolvable without circular imports.
- Preserve all schema class names, field expressions, validators, and HTTP decorator arguments.
- Preserve endpoint function names, SQL, JWT checks, transaction scope, errors, and shared migration revision files.

## Completed structural review

- All 17 whole-file moves retain the original implementations with updated imports. The moved configuration file uses `parents[2]` to resolve the same backend root and `.env` as before.
- All 54 definitions split out of the combined schemas/models files retain their original syntax trees, including validators, aliases, model columns, constraints and relationships.
- Existing modified utilities/tests and Alembic environment contain only import/reference changes; `main.py` additionally contains the authorized two-module router wiring.
- Application import targets and imported symbols exist in the new source tree. Static import traversal found no circular imports, and the obsolete source files are absent.
- The original migration revision source is unchanged. No application imports/startup, tests, migrations, database connections, environment edits, frontend changes or remote operations were performed.
- Static review found no unresolved structural blocker. Runtime behavior was not exercised, as requested.
