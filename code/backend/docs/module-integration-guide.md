# Adding a backend module

Create only your module-owned files and tests. Reuse the shared foundation; never create another Base, engine, authentication system, Alembic environment, or users/tasks/events copy.

## Existing module ownership

- User: `app/modules/user/` owns Auth endpoints/services, profiles, preferences, schemas and models.
- Scheduler: `app/modules/scheduler/` owns Task/Event endpoints, input services, schemas and models.
- Core: `app/core/` owns JWT verification, sessions, Base, configuration, shared contracts and errors.

Within an existing module, add a child router to that module's `router.py`. Keep its current URL prefixes. User and Scheduler are registered explicitly in `main.py`; do not add them to `BACKEND_MODULES`, which remains the extension mechanism for additional modules. A larger module can use `routers/` and `services/` folders like the existing modules; a small new module can keep the simple layout below.

## Exact files

```text
app/modules/<module>/
  __init__.py       # empty package marker
  models.py         # only module-owned models using shared Base
  schemas.py        # Pydantic requests/responses, extra='forbid'
  service.py        # ownership-filtered business queries
  router.py         # APIRouter exported as router
tests/test_<module>.py
alembic/versions/<shared_revision>_<module>.py
```

Do not edit `app/main.py`, `app/core/config.py`, `app/core/security.py`, `app/core/dependencies.py`, `app/core/database.py`, `app/core/base.py`, `app/core/models.py`, `app/core/registry.py`, `alembic.ini`, `alembic/env.py`, or the existing initial migration to register a module. Configure your ignored environment instead:

```dotenv
BACKEND_MODULES=app.modules.your_module
```

Use comma-separated package names for multiple modules. The application imports registered models.py/router.py; Alembic always imports the approved complete schema plus configured extra module models. Keep __init__.py free of engine creation or side effects. If adding a shared requirement is actually necessary, propose it in a separate reviewed foundation change rather than silently modifying shared files.

For a new package, these commands create only absent files; fill them using the patterns below:

```powershell
$taskModulePath = 'app/modules/your_module'
if (Test-Path -LiteralPath $taskModulePath) { throw 'Module already exists; preserve it.' }
New-Item -ItemType Directory -Path $taskModulePath
foreach ($taskModuleFile in @('__init__.py','models.py','schemas.py','service.py','router.py')) {
    New-Item -ItemType File -Path (Join-Path $taskModulePath $taskModuleFile)
}
```

## Model pattern

```python
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from app.core.base import Base, owner_fk

class ModuleRecord(Base):
    __tablename__ = 'your_module_records'
    id = sa.Column(UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text('gen_random_uuid()'))
    user_id = sa.Column(UUID(as_uuid=True),
                        owner_fk('fk_your_module_profile'), nullable=False)
    # Add only your approved module-owned attributes.
```

Public schema is inherited from Base. Every registered module table requires non-null profile ownership. Migration review must add RLS and least-privilege column grants; the runtime guard rejects unmigrated tables, disabled RLS or accessible table ownership. Define useful owner-led indexes for actual queries. Never model auth.users on Base.

## Router/session pattern

```python
from fastapi import APIRouter
from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES

router = APIRouter(prefix='/your-module', tags=['Your module'],
                   responses=ERROR_RESPONSES)

@router.get('/records', response_model=YourResponse, summary='...',
            description='Bearer token required; ...')
def endpoint(session: SessionDep):
    user_id = session.info['user_id']  # verified subject
    return service.list_owned(session, user_id=user_id)
```

Define YourResponse and import your service in your own package. The application adds `/api/v1` automatically. Supply real request/response examples and error descriptions on your router. Do not manually commit, replace the identity, trust body user_id, or open a separate session inside a handler. Flush writes when needed; `SessionDep` handles final commit/rollback and cleanup before the response.

Every SELECT/UPDATE/DELETE includes the verified owner. IDs belonging to another user return the same 404 as missing IDs. Validate allowed fields and combined PATCH state; never permit owner, ID or server timestamp reassignment. Cross-module foreign keys must preserve same-user ownership; design such relationships explicitly and review any needed shared-schema amendment. Do not create duplicate Task/Event/Profile projections as business tables.

## Migration and test workflow

```powershell
$env:BACKEND_MODULES = 'app.modules.your_module'
& '.\.venv\Scripts\python.exe' -m alembic revision --autogenerate -m 'add your module'
# Review generated file; add constraints, indexes, RLS/grants/triggers manually.
& '.\.venv\Scripts\python.exe' -m alembic heads
& '.\.venv\Scripts\python.exe' -m alembic upgrade head
& '.\.venv\Scripts\python.exe' -m alembic check
& '.\.venv\Scripts\python.exe' -m pytest tests/test_your_module.py -q
& '.\.venv\Scripts\python.exe' -m pytest -q
```

Run migrations only against your disposable/local development database. Read database-migrations.md before writing policies. Coordinate parallel revisions; retain one shared chain or a reviewed Alembic merge revision. Do not edit another teammate's applied migration.

Shared pytest fixtures `database`, `signer`, `users`, and `api` are available from conftest.py. Use database.runtime and signed identities for normal operations; admin is fixture setup only. Tests may bootstrap Auth only in their newly created loopback test database. Test valid/invalid input, persistence, cross-user reads/edits/deletes, transaction cleanup, query counts and migration consistency. For API tests, instantiate create_app with the test runtime and verifier.

`tests/test_module_integration.py` demonstrates a temporary package, real migration autogeneration from the shared environment, one revision ancestry, router registration, shared dependencies, and RLS. Temporary model/source/migration changes live in pytest's temporary directory; no teammate business module is installed into production code.


## Final database contract

The shared head is `0003_dayli_foundation`, above the deployed `0002_energy`. Reuse existing entities in [the complete dictionary](database-data-dictionary.md); consult [ownership/security and the one manual upgrade](database-migrations.md) before adding tables. Import `app.core.schema_models` when using cross-module planning models; registry activation resolves the full contract for Energy/Attention/Decision/Rescheduling. Each owning module writes through its service interface. Attention owns the one Focus Session class/table, reexported by Energy. Existing Task/Event API fields remain unchanged. No additional schema history or duplicate Auth/User/Task/Event/Focus entity is permitted.
