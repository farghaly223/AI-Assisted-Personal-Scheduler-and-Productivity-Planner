"""Restricted SQLAlchemy connections with identity scoped to one transaction."""

import json
from contextlib import contextmanager
from collections.abc import Iterator

import sqlalchemy as sa
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import Session

from app.auth import SupabaseJWTVerifier
from app.config import validate_database_url
from app.db.registry import managed_table_names


class UnsafeDatabaseRole(RuntimeError):
    pass


def create_database_engine(url: str | URL, *, pool_size: int = 5) -> Engine:
    return sa.create_engine(
        validate_database_url(url), pool_pre_ping=True, pool_size=pool_size,
        max_overflow=0, pool_reset_on_return="rollback", hide_parameters=True,
        connect_args={"connect_timeout": 10, "prepare_threshold": None},
    )


def assert_runtime_role(connection: sa.Connection) -> None:
    role = connection.execute(sa.text("""
        SELECT current_user AS role, session_user AS login,
               rolsuper, rolbypassrls, rolcreaterole, rolcreatedb
        FROM pg_catalog.pg_roles WHERE rolname = current_user
    """)).mappings().one()
    if role["role"] != role["login"] or any(role[k] for k in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb")):
        raise UnsafeDatabaseRole("Runtime must be a dedicated restricted LOGIN role, never an admin or impersonated role")
    dangerous_membership = connection.scalar(sa.text("""
        SELECT EXISTS (
          SELECT 1 FROM pg_catalog.pg_roles r
          WHERE pg_catalog.pg_has_role(session_user, r.oid, 'MEMBER')
            AND (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb)
        )
    """))
    if dangerous_membership:
        raise UnsafeDatabaseRole("Runtime has membership in a privileged role")
    if not connection.scalar(sa.text("SELECT pg_catalog.pg_has_role(current_user, 'authenticated', 'USAGE')")):
        raise UnsafeDatabaseRole("Runtime must inherit the existing authenticated role")
    table_names = managed_table_names()
    tables = connection.execute(sa.text("""
        SELECT c.relname, c.relrowsecurity,
               pg_catalog.pg_has_role(session_user, c.relowner, 'MEMBER') AS can_own
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname = ANY(:table_names)
          AND c.relkind='r'
    """), {"table_names": sorted(table_names)}).mappings().all()
    if {r["relname"] for r in tables} != table_names or any(not r["relrowsecurity"] or r["can_own"] for r in tables):
        raise UnsafeDatabaseRole("All registered application tables must have RLS enabled and must not be owned by an accessible runtime role")


class Database:
    def __init__(self, engine: Engine):
        self.engine = engine

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            assert_runtime_role(connection)
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
                assert_runtime_role(connection)
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
