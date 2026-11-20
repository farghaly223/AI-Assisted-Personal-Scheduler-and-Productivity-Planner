"""Restricted SQLAlchemy connections with identity scoped to one transaction."""

import json
from contextlib import contextmanager
from collections.abc import Iterator

import sqlalchemy as sa
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import Session

from app.core.security import SupabaseJWTVerifier
from app.core.config import validate_database_url
from app.core.registry import managed_table_names


class UnsafeDatabaseRole(RuntimeError):
    pass


def create_database_engine(url: str | URL, *, pool_size: int = 5) -> Engine:
    return sa.create_engine(
        validate_database_url(url), pool_pre_ping=True, pool_size=pool_size,
        max_overflow=0, pool_reset_on_return="rollback", hide_parameters=True,
        connect_args={"connect_timeout": 10, "prepare_threshold": None},
    )


def assert_runtime_role(connection: sa.Connection, *, allow_admin_runtime: bool = False) -> None:
    # Keep the old keyword for callers, but it no longer relaxes user isolation.
    role = connection.execute(sa.text("""
        SELECT current_user AS role, session_user AS login,
               rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication,
               EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname='public'
                       AND has_schema_privilege(current_user,n.oid,'USAGE')) AS public_usage,
               EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname='auth'
                       AND has_schema_privilege(current_user,n.oid,'USAGE')) AS auth_usage,
               EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
                       JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                       WHERE n.nspname='auth' AND p.proname='uid' AND p.pronargs=0
                         AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS auth_uid_execute
        FROM pg_catalog.pg_roles WHERE rolname = current_user
    """)).mappings().one()
    if role["role"] != role["login"]:
        raise UnsafeDatabaseRole("Runtime must not impersonate another database role")
    if any(role[k] for k in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "rolreplication")):
        raise UnsafeDatabaseRole("Runtime must be a dedicated restricted LOGIN role, never an admin")
    dangerous_membership = connection.scalar(sa.text("""
        SELECT EXISTS (
          SELECT 1 FROM pg_catalog.pg_roles r
          WHERE pg_catalog.pg_has_role(session_user, r.oid, 'MEMBER')
            AND (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb
                 OR r.rolreplication OR r.rolname IN
                 ('postgres','service_role','supabase_admin','authenticator',
                  'pg_read_all_data','pg_write_all_data','pg_execute_server_program',
                  'pg_read_server_files','pg_write_server_files'))
        )
    """))
    if dangerous_membership:
        raise UnsafeDatabaseRole("Runtime has membership in a privileged role")
    if not all(role[key] for key in ("public_usage","auth_usage","auth_uid_execute")):
        raise UnsafeDatabaseRole("Runtime needs public/auth schema USAGE and auth.uid() EXECUTE privileges")
    if not connection.scalar(sa.text("SELECT pg_catalog.pg_has_role(current_user, 'authenticated', 'USAGE')")):
        raise UnsafeDatabaseRole("Runtime must inherit the existing authenticated role")
    if connection.scalar(sa.text("""
        SELECT has_database_privilege(current_user, current_database(), 'CREATE')
            OR EXISTS (
                SELECT 1 FROM pg_catalog.pg_namespace n
                WHERE n.nspname IN ('public','auth')
                  AND (has_schema_privilege(current_user, n.oid, 'CREATE')
                       OR pg_catalog.pg_has_role(session_user, n.nspowner, 'MEMBER'))
            )
            OR EXISTS (
                SELECT 1 FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public'
                  AND (p.proname LIKE 'dayli_%' OR p.proname LIKE 'scheduler_%' OR p.proname LIKE 'energy_%')
                  AND pg_catalog.pg_has_role(session_user, p.proowner, 'MEMBER')
            )
    """)):
        raise UnsafeDatabaseRole("Runtime must not own or create database schemas or application objects")
    table_names = managed_table_names()
    tables = connection.execute(sa.text("""
        SELECT c.relname, c.relrowsecurity,
               pg_catalog.pg_has_role(session_user, c.relowner, 'MEMBER') AS can_own,
               has_table_privilege(current_user,c.oid,'SELECT') AS can_select
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r'
          AND (c.relname=ANY(:table_names) OR c.relname='alembic_version'
               OR EXISTS (
                   SELECT 1 FROM pg_catalog.pg_constraint f
                   WHERE f.conrelid=c.oid AND f.contype='f'
                     AND f.confrelid=to_regclass('public.profiles')
               ))
    """), {"table_names": sorted(table_names)}).mappings().all()
    installed = {r["relname"] for r in tables}
    if not table_names <= installed or any(not r["relrowsecurity"] for r in tables if r["relname"] != "alembic_version"):
        raise UnsafeDatabaseRole("All registered application tables must exist and have RLS enabled")
    if any(r["can_own"] for r in tables):
        raise UnsafeDatabaseRole("All registered application tables must have RLS enabled and must not be owned by an accessible runtime role")
    if any(not r["can_select"] for r in tables if r["relname"] != "alembic_version"):
        raise UnsafeDatabaseRole("Runtime is missing application table read privileges")
    if "usage_sync_batches" in installed and not connection.scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname='dayli_app_writer'
                      AND pg_catalog.pg_has_role(current_user,r.oid,'USAGE'))
    """)):
        raise UnsafeDatabaseRole("The final schema requires an explicit dayli_app_writer grant to the restricted runtime LOGIN")


class Database:
    def __init__(self, engine: Engine, *, allow_admin_runtime: bool = False):
        self.engine = engine
        self.allow_admin_runtime = allow_admin_runtime

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            assert_runtime_role(connection, allow_admin_runtime=self.allow_admin_runtime)
            if connection.scalar(sa.text("SELECT 1")) != 1:
                raise RuntimeError("Database connection check failed")
            if connection.scalar(sa.text("SELECT auth.uid()")) is not None:
                raise UnsafeDatabaseRole("Unscoped pooled connection contains a user identity")

    @contextmanager
    def user_session(self, token: str, verifier: SupabaseJWTVerifier) -> Iterator[Session]:
        identity = verifier.verify(token)  # Verify BEFORE acquiring a DB connection.
        with Session(self.engine, expire_on_commit=False) as session:
            with session.begin():
                connection = session.connection()
                assert_runtime_role(connection, allow_admin_runtime=self.allow_admin_runtime)
                if connection.scalar(sa.text("SELECT auth.uid()")) is not None:
                    raise UnsafeDatabaseRole("Unscoped pooled connection contains a user identity")
                subject = str(identity.user_id)
                claims = json.dumps({"sub": subject, "role": "authenticated"})
                session.execute(sa.text("""
                    SELECT set_config('request.jwt.claim.sub', :subject, true),
                           set_config('request.jwt.claim.role', 'authenticated', true),
                           set_config('request.jwt.claims', :claims, true),
                           set_config('TimeZone', 'UTC', true),
                           set_config('row_security', 'on', true)
                """), {"subject": subject, "claims": claims})
                session.info["user_id"] = identity.user_id
                yield session
            # begin() commits on success and rolls back on exception. SET LOCAL
            # disappears either way, before the connection returns to the pool.

    def dispose(self) -> None:
        self.engine.dispose()
