"""Disposable local PostgreSQL; never accepts a remote test endpoint."""

import json
import os
import secrets
from types import SimpleNamespace
from uuid import uuid4
import time

from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import psycopg
from psycopg import sql
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import URL

from app.core.security import SupabaseJWTVerifier
from app.core.config import BACKEND_ROOT, require_local, validate_database_url
from app.modules.user.models import Profile
from app.core.database import Database, create_database_engine
from scripts.provision_runtime import connect_admin, create_restricted_role

from tests.api_support import api


@pytest.fixture(scope="session")
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    public.update(kid="test-key", use="sig", alg="RS256")
    verifier = SupabaseJWTVerifier("https://scheduler-tests.invalid")
    # Replace only JWKS transport; PyJWT still checks actual signatures and claims.
    verifier._jwks.fetch_data = lambda: {"keys": [public]}

    def token(user_id, **overrides):
        claims = dict(sub=str(user_id), role="authenticated", aud="authenticated",
                      iss=verifier.issuer, iat=int(time.time()), exp=int(time.time()) + 300)
        claims.update(overrides)
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})

    return SimpleNamespace(key=key, verifier=verifier, token=token)


@pytest.fixture(scope="session")
def database():
    configured = os.environ.get("TEST_DATABASE_ADMIN_URL")
    local_file = BACKEND_ROOT / ".local" / "postgres.json"
    if configured:
        server_url = validate_database_url(configured)
    elif local_file.is_file():
        settings = json.loads(local_file.read_text(encoding="utf-8"))
        server_url = URL.create("postgresql+psycopg", database="postgres", **settings)
    else:
        pytest.skip("Local PostgreSQL not configured; use scripts.local_postgres or TEST_DATABASE_ADMIN_URL")
    require_local(server_url)
    suffix = uuid4().hex[:12]
    name = "scheduler_test_" + suffix
    role = "scheduler_test_runtime_" + suffix
    runtime_password = secrets.token_urlsafe(32)
    admin_url = server_url.set(database=name)
    runtime_url = admin_url.set(username=role, password=runtime_password)
    admin_engine = runtime_engine = None
    created_role = False
    with connect_admin(server_url, autocommit=True) as control:
        control.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with connect_admin(admin_url) as bootstrap:
                # Guard the explicit exception approved by the user. Never run
                # this fixture in an existing/shared/Supabase database.
                require_local(admin_url)
                assert name.startswith("scheduler_test_") and bootstrap.info.dbname == name
                bootstrap.execute((BACKEND_ROOT / "tests" / "auth_fixture.sql").read_text())

            cfg = Config(str(BACKEND_ROOT / "alembic.ini"))

            def migrate(direction, revision):
                with pytest.MonkeyPatch.context() as patch:
                    patch.setenv("MIGRATION_DATABASE_URL", admin_url.render_as_string(hide_password=False))
                    getattr(command, direction)(cfg, revision)

            migrate("upgrade", "head")
            with connect_admin(admin_url) as bootstrap:
                create_restricted_role(bootstrap, role, runtime_password)
            created_role = True
            admin_engine = create_database_engine(admin_url)
            runtime_engine = create_database_engine(runtime_url, pool_size=1)
            runtime = Database(runtime_engine)
            runtime.check_connection()
            yield SimpleNamespace(admin=admin_engine, runtime=runtime, admin_url=admin_url,
                                  runtime_url=runtime_url, migrate=migrate, role=role)
        finally:
            if runtime_engine:
                runtime_engine.dispose()
            if admin_engine:
                admin_engine.dispose()
            # Only the fresh random test database may be destroyed.
            assert name.startswith("scheduler_test_")
            control.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            if created_role:
                control.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.fixture
def users(database, signer):
    ids = [uuid4(), uuid4()]
    with database.admin.begin() as connection:
        for user_id in ids:
            connection.execute(sa.text("INSERT INTO auth.users(id) VALUES (:id)"), {"id": user_id})
    for user_id in ids:
        with database.runtime.user_session(signer.token(user_id), signer.verifier) as session:
            session.add(Profile(id=user_id, full_name="Synthetic test user"))
    return ids
