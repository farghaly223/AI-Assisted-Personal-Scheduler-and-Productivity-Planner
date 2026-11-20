"""Fresh random loopback databases for deployment-path checks; never shared DBs."""

from contextlib import contextmanager
from uuid import uuid4

from psycopg import sql

from app.core.config import BACKEND_ROOT, require_local
from scripts.provision_runtime import connect_admin


@contextmanager
def staged_database(database):
    server_url = database.admin_url.set(database="postgres")
    require_local(server_url)
    name = "scheduler_test_stage_" + uuid4().hex[:12]
    target = server_url.set(database=name)
    with connect_admin(server_url, autocommit=True) as control:
        control.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with connect_admin(target) as bootstrap:
                assert bootstrap.info.dbname == name
                bootstrap.execute((BACKEND_ROOT / "tests/auth_fixture.sql").read_text())
            yield target
        finally:
            assert name.startswith("scheduler_test_stage_")
            control.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
