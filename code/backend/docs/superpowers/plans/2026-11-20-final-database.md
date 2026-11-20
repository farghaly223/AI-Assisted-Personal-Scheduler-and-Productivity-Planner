# DAYLI Final Database Implementation Plan

> Native implementation in this session, with the brainstorming skill's required
> fresh builder check and a fresh final review. User explicitly requests proceeding
> after the concise schema summary; no additional design-approval gate.

**Goal:** Ship a compatible 31-table foundation above deployed 0002_energy.
**Architecture:** Shared Base/history; 19 new owned tables; additive Energy changes;
frozen 0003; manual SQL exports. No frontend or business algorithms.
**Stack:** Existing SQLAlchemy/Alembic/psycopg/PostgreSQL/pytest/PlantUML.
**Spec:** `docs/superpowers/specs/2026-11-20-final-database-design.md`.

## Constraints

- Keep 0001 SHA55f0831c and 0002 SHAe2325a98 unchanged; preserve all core DTOs/columns.
- No MCP/network/Supabase writes; local disposable checks only.
- NULL missing data; no score weights; no duplicate Focus/Task/Event/Auth entities.
- One writer group, separate restricted LOGIN/admin credentials, transaction identity.
- New migration/export must preserve actual existing records.

## Review focus

- Source deletion/account cascades must not be blocked by history guards.
- Concurrent publication/overlap insertion must fail atomically.
- Legacy unversioned/default-confidence data must not masquerade as validated input.
- Cumulative mobile revisions must not double-count; retention must be explicit.
- Both current-0002 and fresh installations must agree with ORM/security contracts.

## Tasks

1. Model contract and Energy extensions.
   Files: new User/Scheduler planning_models; Attention models/focus; Decision and
   Rescheduling models; shared schema_models; extend Energy models.
   - [x] Write failing contract tests; observe missing new schema.
   - [x] Add 19 models and documented fields/constraints/indexes; reexport Focus.
2. Frozen schema/security migration.
   Files: 0003_dayli_foundation.py, local build helper, database.py, provision_runtime.py.
   - [x] Freeze create/alter SQL, integrity/history/publication/clock guards, RLS/grants.
   - [x] Backfill legacy data explicitly; protect unsafe downgrades.
   - [x] Test metadata/data/RLS/FKs/DST/concurrency and strict runtime roles.
3. Exports and generated documentation.
   Files: scripts/export_schema.py; ONLY upgrade_from_0002_energy.sql;
   digrams/masterclass/dayli_final_database.puml; docs/database-data-dictionary.md.
   - [x] Generate exports through offline Alembic; execute fresh/current paths locally.
   - [x] Generate every column/default/key/check/index and every FK/cardinality.
4. Integration handover and review.
   Files: consolidate ownership, security and final report in docs/database-migrations.md;
   update current README/migration/module guides and affected fixtures safely.
   - [x] Document all table owners/consumers, activation, versions, retention and manual roles.
   - [x] Run targeted then complete tests; fresh review; resolve actual findings.
   - [x] Verify frozen migrations and exact artifact parity; report actual results/limits.

No commit, push, automatic deployment or production rollback.
