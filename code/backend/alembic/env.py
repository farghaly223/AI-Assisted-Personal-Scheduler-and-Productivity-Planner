"""Application-only migrations. Remote DDL requires an explicit CLI opt-in."""

from alembic import context
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from app.core.config import LOCAL_HOSTS, ConfigurationError, database_url
from app.core.models import Base
from app.core.registry import configured_modules, load_module_models, managed_table_names
from app.core.migration_scope import explicit_schema_reflection

load_module_models(configured_modules())
APPLICATION_TABLES = managed_table_names()


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in (None, "public")
    if type_ == "table":
        return parent_names.get("schema_name") in (None, "public") and name in APPLICATION_TABLES
    return True


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "table":
        return object_.schema in (None, "public") and name in APPLICATION_TABLES
    return True


options = dict(
    target_metadata=Base.metadata, include_schemas=True,
    include_name=include_name, include_object=include_object,
    compare_type=True, compare_server_default=True,
    version_table="alembic_version", version_table_schema="public",
)

if context.is_offline_mode():
    # Offline review needs no credentials or database access.
    context.configure(dialect_name="postgresql", literal_binds=True, **options)
    with context.begin_transaction():
        context.run_migrations()
else:
    url = database_url("MIGRATION_DATABASE_URL")  # Deliberately no runtime fallback.
    allow_remote = context.get_x_argument(as_dictionary=True).get("allow_remote_ddl") == "true"
    if url.host not in LOCAL_HOSTS and not allow_remote:
        raise ConfigurationError("Remote migrations disabled. After explicit approval only, use -x allow_remote_ddl=true")
    engine = sa.create_engine(url, poolclass=NullPool, hide_parameters=True,
                              connect_args={"connect_timeout": 10, "prepare_threshold": None})
    try:
        with engine.begin() as connection:
            with explicit_schema_reflection(connection):
                context.configure(connection=connection, **options)
                with context.begin_transaction():
                    context.run_migrations()
    finally:
        engine.dispose()
