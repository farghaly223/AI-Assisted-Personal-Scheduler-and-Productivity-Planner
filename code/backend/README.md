# AI-Assisted Personal Scheduler — Sprint 1 backend

Backend-only implementation: FastAPI REST APIs, Supabase Auth delegation/JWT verification, PostgreSQL ownership/RLS, SQLAlchemy and one shared Alembic history. No Flutter, frontend integration, teammate business logic or scheduler is implemented.

Start with [API reference](docs/api-reference.md), [architecture](docs/backend-architecture.md), [module guide](docs/module-integration-guide.md), [migrations](docs/database-migrations.md), and [contributing](CONTRIBUTING.md). [Audit/checklist](docs/sprint1-checklist.md) records actual verification and protected scope. The approved [schema](../../plan/Sprint_1_Database_Design_Proposal.md) and [PlantUML](../../digrams/masterclass/sprint1_database_proposal.puml) remain unchanged.

## Environment

Python 3.12+ is required; this workspace is verified with Python 3.15.2 and PostgreSQL 17.6 on Windows. From repository root:

```powershell
Set-Location -LiteralPath 'code/backend'
py -3 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock
# Optional editable installation; dependencies are already in the lock:
& '.\.venv\Scripts\python.exe' -m pip install --no-deps --no-build-isolation -e .
```

Do not recreate an existing environment unless intended. The current workspace already contains its virtual environment and portable PostgreSQL binaries. On a new machine install PostgreSQL or extract the official Windows binaries under `.local/pgsql/` so `.local/pgsql/bin/initdb.exe` exists. Alternatively set PG_BIN to your installed PostgreSQL bin directory. No PostgreSQL Windows service is installed by these scripts.

## Local database and tests without production credentials

For a fresh checkout with no .env/cluster:

```powershell
# Optional when using an installed PostgreSQL distribution:
# $env:PG_BIN = 'C:\Program Files\PostgreSQL\17\bin'
& '.\.venv\Scripts\python.exe' -m scripts.local_postgres init
& '.\.venv\Scripts\python.exe' -m scripts.local_postgres start
& '.\.venv\Scripts\python.exe' -m scripts.local_database
& '.\.venv\Scripts\python.exe' -m alembic upgrade head
& '.\.venv\Scripts\python.exe' -m app.core.database_check
& '.\.venv\Scripts\python.exe' -m alembic check
& '.\.venv\Scripts\python.exe' -m pytest -q
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Initialization and local_database refuse to overwrite existing cluster/config/.env. local_database creates only a fresh random manual-test database and restricted login, stores random local-only credentials in ignored .env, and seeds an explicitly authorized minimal Auth fixture. It does not apply the application migration. Never use its fixture in an existing/shared/Supabase database.

For this existing workspace, reuse the current environment/.env/database. Check connectivity first. Start the existing local server only if it is stopped; do not initialize or reset it. `alembic upgrade head` is a no-op when already at head. Local server management is `python -m scripts.local_postgres status|start|stop`; stopping a server is separate from deleting data.

The server, liveness/readiness, OpenAPI, database checks and full tests run without production Supabase credentials. Authentication tests replace only external HTTP/JWKS transport and use real JWT cryptography, real PostgreSQL constraints/RLS and synthetic users. Integration tests use a newly created random loopback database and remove only that test database/role; the manual database is unaffected. This test cleanup is explicitly authorized for this task. Missing local PostgreSQL configuration produces explicit integration skips; a successful partial/unit run is not evidence that all tests ran.

For interactive signup/login, use a real **local Supabase Auth** stack as below, or a separately configured non-production Supabase project. Missing Auth configuration returns a safe 503 on protected/Auth operations; it never enables a fake production login.

## Local Supabase Auth for interactive API use

Prerequisites: Docker and a current Supabase CLI (for example `npx supabase@latest` with Node installed). This optional Docker path was documented from public CLI source; Docker/Supabase CLI were not available for live execution in this workspace. It does not require production credentials.

From backend root, for a new local Supabase project:

```powershell
npx supabase@latest init
# Generate a fresh ES256 key. Refuse to overwrite existing key material.
$taskLocalKeyPath = Join-Path (Get-Location) 'supabase/signing_keys.json'
if (Test-Path -LiteralPath $taskLocalKeyPath) { throw 'Signing keys already exist; reuse the existing configuration.' }
$taskLocalKey = npx supabase@latest gen signing-key --algorithm ES256 | ConvertFrom-Json
$taskLocalKeyJson = ConvertTo-Json -InputObject @($taskLocalKey) -Depth 20
[IO.File]::WriteAllText($taskLocalKeyPath, $taskLocalKeyJson, [Text.UTF8Encoding]::new($false))
```

Edit the existing `[auth]` section of `supabase/config.toml` (do not create a duplicate section):

```toml
signing_keys_path = './signing_keys.json'
jwt_issuer = 'http://127.0.0.1:54321/auth/v1'
```

The signing key is private and ignored; never commit it. Local asymmetric keys are necessary because the backend intentionally rejects legacy HS256 access tokens. Start the local stack and copy its **local anon/publishable** key (never service-role) into your environment:

```powershell
npx supabase@latest start
npx supabase@latest status
$env:APP_ENV = 'development'
$env:SUPABASE_URL = 'http://127.0.0.1:54321'
$env:SUPABASE_PUBLIC_KEY = 'REPLACE_LOCAL_ANON_OR_PUBLISHABLE_KEY'
$env:MIGRATION_DATABASE_URL = 'postgresql+psycopg://postgres:REPLACE_LOCAL_DB_PASSWORD@127.0.0.1:54322/postgres'
& '.\.venv\Scripts\python.exe' -m alembic upgrade head
& '.\.venv\Scripts\python.exe' -m scripts.provision_runtime --role scheduler_runtime
# Use the new password entered at the hidden provisioning prompt; URL-encode it.
$env:DATABASE_URL = 'postgresql+psycopg://scheduler_runtime:REPLACE_LOCAL_RUNTIME_PASSWORD@127.0.0.1:54322/postgres'
& '.\.venv\Scripts\python.exe' -m app.core.database_check
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

These process variables override the existing local .env without overwriting it. Use the actual local stack URLs/ports/passwords reported by your CLI if customized. Do not run scripts.local_database against Supabase; Supabase already owns its Auth schema. Local confirmation emails appear in the local mail tool shown by `supabase status` when confirmation is enabled. JWT issuer must match SUPABASE_URL + /auth/v1, and the JWKS endpoint must expose the configured asymmetric key.

## Runtime configuration

.env.example contains placeholders only. DATABASE_URL is the restricted runtime LOGIN; MIGRATION_DATABASE_URL is separate admin/migration configuration and never a fallback. SUPABASE_URL is the trusted origin; SUPABASE_PUBLIC_KEY must be publishable/anon. APP_ENV is production by default; only explicit development permits loopback HTTP Auth. Remote URLs require HTTPS/Auth and verify-full TLS/PostgreSQL. Use direct or verified session-mode PostgreSQL port 5432; transaction pooler 6543 is unsupported. CORS_ORIGINS is an optional comma-separated exact origin list. BACKEND_MODULES registers teammate packages without shared-code changes.

No runtime/admin/password/service-role secrets are committed or sent to clients. API errors omit raw inputs, passwords, tokens, SQL and provider details. RLS claims are verified and transaction-local; ordinary CRUD rejects owner/admin/BYPASSRLS connections in every environment, including when the legacy development opt-in is true. Read docs/backend-architecture.md for the trust boundary and docs/module-integration-guide.md before adding models/routes.

### Manual Swagger testing with the existing Supabase connection

Configure the existing Supabase project with a dedicated restricted runtime LOGIN inheriting `authenticated` and `dayli_app_writer` after the manual 0002→0003 upgrade. Keep migration credentials separate. The old `ALLOW_ADMIN_DATABASE_RUNTIME=true` option no longer permits administrative user CRUD; the private `.env` was not rewritten. Restart after changing configuration.

From `code/backend`, start with:

```powershell
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs`. Liveness is `/api/v1/health`; database readiness is `/api/v1/health/ready`. Readiness checks restricted role/ownership/DDL privileges, installed app-table RLS and absence of residual user identity. A readiness check alone does not prove cross-user isolation.

**Runtime isolation:** ordinary sessions always require a restricted LOGIN. `postgres`, app owners, service-role/admin/BYPASSRLS and DDL-capable roles are rejected even in development. Use administrative credentials only for separately reviewed migrations/maintenance. See [database deployment and security](docs/database-migrations.md).

## APIs and handover

The final database extends deployed `0002_energy` with `0003_dayli_foundation`, preserving 12 tables and adding 19. Manually run only [upgrade_from_0002_energy.sql](review/upgrade_from_0002_energy.sql). The [complete ERD](../../digrams/masterclass/dayli_final_database.puml), [369-column dictionary](docs/database-data-dictionary.md) and [ownership/security handover](docs/database-migrations.md) define the shared contract. Deploy 0003 before activating the extended Energy/shared model contracts; Energy's router/schemas remain placeholders.

There are 21 method/route operations: two health checks; registration/signup OTP verification/resend/login/refresh; own profile/preferences retrieval/update; task CRUD/list; event CRUD/list with ONCE/WEEKLY rules. Open Swagger at `http://127.0.0.1:8000/docs`, ReDoc at `/redoc`, and OpenAPI at `/openapi.json`. All private APIs require Supabase bearer access tokens. Every operation includes schemas, validation/error descriptions and examples. Exported OpenAPI lives in docs/openapi.json. The [DAYLI signup/resend email template](docs/email-templates/dayli-signup-otp.html) and manual dashboard instructions are documented in the [API reference](docs/api-reference.md). Registration rejects existing confirmed or pending emails with 409; its private read-only Auth lookup requires `SUPABASE_SERVICE_ROLE_KEY`. Public Auth operations still use `SUPABASE_PUBLIC_KEY` or `SUPABASE_ANON_KEY`; application CRUD never uses the service-role key.

Task importance is priority 1–5; status is UNSCHEDULED. Events remain commitments, with MANUAL/SAVED constants; weekly weekday derives from first date. No event-to-task copy, materialized occurrence, exception workflow, energy/attention computation or optimization exists. PATCH validates combined records; list pagination is bounded and avoids per-item queries.

The single chain is `0001_sprint1 → 0002_energy → 0003_dayli_foundation`. Applied migrations remain unchanged; the current upgrade is frozen independently of models. All modules share one Base/engine/session/history; `auth.users` is an external FK target. No Supabase operation or automatic deployment was performed.
