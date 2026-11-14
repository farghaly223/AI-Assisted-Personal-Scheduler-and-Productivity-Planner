"""Keep explicit public/auth FK schemas stable during Alembic reflection."""

from contextlib import contextmanager

import sqlalchemy as sa


@contextmanager
def explicit_schema_reflection(connection):
    # PostgreSQL hides schema names found on search_path in pg_get_constraintdef.
    # Reflect with a catalog-only path, preserving public/auth qualifications.
    # This is session configuration, never a change to a managed schema.
    previous_path = connection.scalar(sa.text("SELECT current_setting('search_path')"))
    previous_default = connection.dialect.default_schema_name
    connection.execute(sa.text("SELECT set_config('search_path', 'pg_catalog', true)"))
    connection.dialect.default_schema_name = "pg_catalog"
    try:
        yield
    except BaseException:
        # Leave transaction-local cleanup to rollback; an aborted PostgreSQL
        # transaction cannot execute RESET/SELECT and must retain its first error.
        raise
    else:
        connection.execute(sa.text("SELECT set_config('search_path', :path, true)"), {"path": previous_path})
    finally:
        connection.dialect.default_schema_name = previous_default
