"""Prepare a disposable manual-test database and ignored .env; no application DDL.

Uses only the loopback cluster created by scripts.local_postgres. The minimal
Auth fixture was explicitly authorized for local tests, never remote deployment.
"""

import json
from uuid import uuid4
import secrets

from psycopg import sql
from sqlalchemy.engine import URL

from app.core.config import BACKEND_ROOT, require_local
from scripts.provision_runtime import connect_admin, create_restricted_role


def main() -> None:
    env_path = BACKEND_ROOT / ".env"
    if env_path.exists():
        raise SystemExit("backend/.env already exists; refusing to overwrite configuration")
    settings = json.loads((BACKEND_ROOT / ".local" / "postgres.json").read_text())
    server_url = URL.create("postgresql+psycopg", database="postgres", **settings)
    require_local(server_url)
    suffix = uuid4().hex[:12]
    name = "scheduler_test_manual_" + suffix
    role = "scheduler_manual_" + suffix
    password = secrets.token_urlsafe(32)
    admin_url = server_url.set(database=name)
    runtime_url = admin_url.set(username=role, password=password)
    with connect_admin(server_url, autocommit=True) as control:
        control.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with connect_admin(admin_url) as connection:
        assert name.startswith("scheduler_test_manual_") and connection.info.dbname == name
        connection.execute((BACKEND_ROOT / "tests" / "auth_fixture.sql").read_text())
        create_restricted_role(connection, role, password)
    # Fresh file only. These credentials are random, local-only, and gitignored.
    with env_path.open("x", encoding="utf-8") as stream:
        stream.write("# Disposable local fixture; NOT a real Supabase Auth installation.\n")
        stream.write("DATABASE_URL=" + runtime_url.render_as_string(hide_password=False) + "\n")
        stream.write("MIGRATION_DATABASE_URL=" + admin_url.render_as_string(hide_password=False) + "\n")
    print("Created a fresh disposable local database and restricted role; saved credentials in ignored backend/.env.")
    print("No application tables created yet. Next: python -m alembic upgrade head")


if __name__ == "__main__":
    main()
