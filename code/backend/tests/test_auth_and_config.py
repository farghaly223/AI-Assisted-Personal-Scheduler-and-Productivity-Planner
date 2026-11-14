from datetime import datetime
from uuid import UUID, uuid4
import io
import os
import time

from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.auth import AuthenticationError
from app.config import BACKEND_ROOT, ConfigurationError, require_local, validate_database_url
from app.db.models import APPLICATION_TABLES, Base
from app.timezones import resolve_unambiguous_local_time, validate_timezone_name


def test_verified_subject(signer):
    subject = uuid4()
    assert signer.verifier.verify(signer.token(subject)).user_id == subject


@pytest.mark.parametrize("overrides", [
    {"exp": 0}, {"iat": int(time.time()) + 3600}, {"nbf": int(time.time()) + 3600},
    {"iss": "https://attacker.invalid/auth/v1"}, {"aud": "service_role"},
    {"role": "service_role"}, {"sub": "not-a-uuid"}, {"role": None},
    {"sub": str(UUID(int=0))}, {"exp": float("inf")},
    {"iat": True}, {"nbf": True}, {"exp": str(int(time.time()) + 3600)},
    {"exp": 10**400}, {"iat": -(10**400)},
])
def test_invalid_claims_rejected(signer, overrides):
    with pytest.raises(AuthenticationError):
        signer.verifier.verify(signer.token(uuid4(), **overrides))


def test_wrong_signature_missing_claim_and_algorithm_rejected(signer):
    valid = signer.token(uuid4())
    claims = jwt.decode(valid, options={"verify_signature": False})
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tokens = [
        jwt.encode(claims, other_key, algorithm="RS256", headers={"kid": "test-key"}),
        jwt.encode(claims, "test-only-" * 8, algorithm="HS256", headers={"kid": "test-key"}),
        jwt.encode(claims, signer.key, algorithm="RS256", headers={"kid": "unknown"}),
        "not.a.token", "", "x" * 32769,
    ]
    del claims["exp"]
    tokens.append(jwt.encode(claims, signer.key, algorithm="RS256", headers={"kid": "test-key"}))
    for token in tokens:
        with pytest.raises(AuthenticationError):
            signer.verifier.verify(token)


@pytest.mark.parametrize("url", [
    "sqlite:///test.db",
    "postgresql+psycopg://user:secret@db.example:5432/postgres",
    "postgresql+psycopg://user:secret@127.0.0.1:6543/test",
    "postgresql+psycopg://user:secret@127.0.0.1/test?host=remote.example",
    "postgresql+psycopg://user:secret@127.0.0.1/test?service=remote",
])
def test_unsafe_connection_configuration_rejected(url):
    with pytest.raises(ConfigurationError) as error:
        validate_database_url(url)
    assert "secret" not in str(error.value)


def test_local_test_guard_rejects_remote():
    url = validate_database_url("postgresql+psycopg://user:secret@db.example/test?sslmode=verify-full")
    with pytest.raises(ConfigurationError):
        require_local(url)


def test_offline_migration_needs_no_url_and_never_manages_auth(monkeypatch):
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    output = io.StringIO()
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini"), output_buffer=output), "head", sql=True)
    sql_text = output.getvalue()
    assert "REFERENCES auth.users(id)" in sql_text
    for action in ("CREATE TABLE auth.", "ALTER TABLE auth.", "DROP TABLE auth.", "CREATE SCHEMA auth", "ON auth.users"):
        assert action not in sql_text
    assert "auth.users" not in Base.metadata.tables
    assert {f"public.{name}" for name in APPLICATION_TABLES} <= set(Base.metadata.tables)
    assert all(table.schema == "public" for table in Base.metadata.tables.values())


def test_remote_migration_blocked_before_connect(monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql+psycopg://user:secret@db.example/test?sslmode=verify-full")
    monkeypatch.setattr(sa, "create_engine", lambda *a, **k: pytest.fail("Must not connect remotely"))
    with pytest.raises(ConfigurationError, match="Remote migrations disabled"):
        command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.mark.parametrize("local", [datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)])
def test_dst_gap_and_fold_rejected(local):
    with pytest.raises(ValueError, match="ambiguous or nonexistent"):
        resolve_unambiguous_local_time(local, "America/New_York")


def test_timezone_validation_and_normal_endpoint():
    assert validate_timezone_name("Africa/Cairo") == "Africa/Cairo"
    with pytest.raises(ValueError):
        validate_timezone_name("Imaginary/Nowhere")
    assert resolve_unambiguous_local_time(datetime(2026, 1, 1, 9), "UTC").hour == 9


def test_all_44_columns_match_the_approved_proposal():
    report = (BACKEND_ROOT.parents[1] / "plan" / "Sprint_1_Database_Design_Proposal.md").read_text(encoding="utf-8")
    count = 0
    for table in Base.metadata.tables.values():
        if table.name not in APPLICATION_TABLES:
            continue  # Module columns have their own reviewed schema/tests.
        section = report.split(f"**`{table.name}`**", 1)[1]
        rows = section.split("\n\n", 2)[1].splitlines()[2:]
        expected = [row.split("|")[1:-1] for row in rows if row.startswith("|")]
        assert [row[0].strip() for row in expected] == list(table.c.keys())
        for row in expected:
            name, type_, _, nullability, default, _ = [value.strip() for value in row]
            column = table.c[name]
            actual_type = str(column.type.compile(dialect=postgresql.dialect())).lower()
            actual_type = actual_type.replace("timestamp with time zone", "timestamptz")
            assert actual_type == type_
            assert column.nullable == (nullability != "NOT NULL")
            if default.startswith("—"):
                assert column.server_default is None
            else:
                assert str(column.server_default.arg).lower() == default.lower()
            count += 1
    assert count == 44


def test_unknown_key_ids_do_not_amplify_jwks_network_fetches(signer, monkeypatch):
    import json
    from app.auth import SupabaseJWTVerifier
    public = jwt.algorithms.RSAAlgorithm.to_jwk(signer.key.public_key(), as_dict=True)
    public.update(kid="test-key", use="sig", alg="RS256")
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return io.BytesIO(json.dumps({"keys": [public]}).encode())
    monkeypatch.setattr("jwt.jwks_client.urllib.request.build_opener", lambda *args: Opener())
    verifier = SupabaseJWTVerifier("https://scheduler-tests.invalid")
    subject = uuid4()
    assert verifier.verify(signer.token(subject)).user_id == subject
    claims = jwt.decode(signer.token(subject), options={"verify_signature": False})
    for index in range(10):
        token = jwt.encode(claims, signer.key, algorithm="RS256", headers={"kid": "unknown-" + str(index)})
        with pytest.raises(AuthenticationError):
            verifier.verify(token)
    assert len(calls) == 1
