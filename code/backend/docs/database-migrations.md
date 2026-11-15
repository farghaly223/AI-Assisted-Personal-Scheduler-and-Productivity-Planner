# Database migrations

One shared environment (`alembic/`) manages all registered application models. Current core head is `0001_sprint1`. This API completion adds **no core table/column migration**. The approved 44 columns, constraints, indexes, triggers, grants and policies are unchanged.

## Local commands

```powershell
& '.\.venv\Scripts\python.exe' -m alembic current
& '.\.venv\Scripts\python.exe' -m alembic heads
& '.\.venv\Scripts\python.exe' -m alembic upgrade head
& '.\.venv\Scripts\python.exe' -m alembic check
& '.\.venv\Scripts\python.exe' -m alembic upgrade head --sql
```

The last command renders review SQL without connecting. Online commands use MIGRATION_DATABASE_URL only. Runtime never inherits it. The initial migration references existing auth.users/auth.uid and authenticated/anon roles; its local minimal Auth fixture is explicitly isolated from migration code. Never execute tests/auth_fixture.sql against an existing Supabase database.

Do not use Base.metadata.create_all/drop_all, Alembic stamp, database reset, or direct schema synchronization. Migrations are reviewed, versioned changes. Downgrades delete objects/data and require appropriate authorization; the automated round-trip test is limited to the fresh test database created during the task.

## Creating a module revision

1. Set BACKEND_MODULES to the module package and import its models through the registry.
2. Create a revision with `python -m alembic revision --autogenerate -m 'add module records'`.
3. Check its down_revision against current shared heads. Review generated SQL and keep migration code independent of mutable model imports.
4. Confirm only intended public tables are changed. Auth/managed schemas must never be created/altered/dropped, even if reflected by PostgreSQL.
5. Add/check owner FKs, indexes, nullability, defaults, validation, immutable columns, RLS/grants and any owned triggers explicitly.
6. Upgrade/check/test on a disposable local database. Coordinate other teammates' revisions before a PR.

Alembic reflection uses an explicit schema search path to preserve public/auth FK qualifications. The registry permits only owned public application tables. Standard autogeneration does not fully inspect RLS, privileges, functions or triggers; review them separately.

## RLS/grant pattern

Example for a module-owned table with `id`, `user_id` and `title` (replace table names/columns with the reviewed module design):

```sql
ALTER TABLE public.your_module_records ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.your_module_records FROM PUBLIC, anon, authenticated;
CREATE POLICY module_select_own ON public.your_module_records
  FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY module_insert_own ON public.your_module_records
  FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY module_update_own ON public.your_module_records
  FOR UPDATE TO authenticated
  USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY module_delete_own ON public.your_module_records
  FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));
GRANT SELECT, DELETE ON public.your_module_records TO authenticated;
GRANT INSERT (user_id, title) ON public.your_module_records TO authenticated;
GRANT UPDATE (title) ON public.your_module_records TO authenticated;
```

No anonymous access. IDs/defaults, owners and server-managed fields are excluded from writable grants. Ordinary queries use the separate restricted LOGIN, never table-owner/postgres/service-role credentials. Security-definer functions, if justified, require a fixed safe search_path, schema-qualified references, and tightly limited execution privileges.

## Supabase deployment boundary

No remote Supabase connection or migration was performed. Remote DDL is blocked by default. Only after explicit approval and review, configure independent trusted migration/runtime URLs and use:

```powershell
# Later only, after explicit approval:
& '.\.venv\Scripts\python.exe' -m alembic -x allow_remote_ddl=true upgrade head
& '.\.venv\Scripts\python.exe' -m scripts.provision_runtime --role scheduler_runtime --allow-remote
& '.\.venv\Scripts\python.exe' -m app.core.database_check
```

Prefer direct PostgreSQL or verified session-mode pooler port 5432, with verify-full TLS and trusted certificates. Transaction-pooler port 6543 is unsupported. Custom runtime-role support, migration/role-admin privileges, TLS and actual JWT signing configuration must be verified on the target project; never substitute a privileged runtime role to get around a failure. A connection check alone does not prove remote cross-user isolation.
