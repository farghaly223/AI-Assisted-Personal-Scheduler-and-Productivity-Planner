"""A temporary module shares real models, Auth/RLS, and the existing migration chain."""

import importlib
import shutil
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from app.config import BACKEND_ROOT, Settings
from app.db.models import Base
from app.main import create_app


def test_temporary_module_reuses_shared_base_sessions_auth_and_migrations(database, signer, users, tmp_path, monkeypatch):
    import app.modules
    from app.db.registry import load_module_models

    package = tmp_path / "probe"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "models.py").write_text('''import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from app.db.models import Base, owner_fk
class Probe(Base):
    __tablename__ = "sprint1_module_probe"
    id = sa.Column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id = sa.Column(UUID(as_uuid=True), owner_fk("fk_probe_profile"), nullable=False)
    title = sa.Column(sa.String(100), nullable=False)
''')
    (package / "router.py").write_text('''import sqlalchemy as sa
from fastapi import APIRouter
from app.dependencies import SessionDep
from .models import Probe
router = APIRouter(prefix="/probe")
@router.get("")
def list_probe(session: SessionDep):
    return session.execute(sa.select(Probe.__table__).where(Probe.user_id == session.info["user_id"])).mappings().all()
''')
    monkeypatch.setattr(app.modules, "__path__", [str(tmp_path), *app.modules.__path__])
    monkeypatch.setenv("BACKEND_MODULES", "app.modules.probe")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", database.admin_url.render_as_string(hide_password=False))
    migration_dir = tmp_path / "alembic"
    shutil.copytree(BACKEND_ROOT / "alembic", migration_dir, ignore=shutil.ignore_patterns("__pycache__"))
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(migration_dir))
    try:
        load_module_models(("app.modules.probe",))
        revision = command.revision(cfg, message="temporary module probe", autogenerate=True)
        assert revision.down_revision == "0001_sprint1"
        text = revision.path
        source = open(text, encoding="utf-8").read()
        assert "sprint1_module_probe" in source
        # Application-owned security DDL is reviewed explicitly after autogeneration.
        addition = '''
    op.execute("ALTER TABLE public.sprint1_module_probe ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY probe_own ON public.sprint1_module_probe TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()))")
    op.execute("REVOKE ALL ON public.sprint1_module_probe FROM PUBLIC, anon, authenticated")
    op.execute("GRANT SELECT ON public.sprint1_module_probe TO authenticated")
'''
        marker = "\ndef downgrade()"
        source = source.replace(marker, addition + marker)
        with open(text, "w", encoding="utf-8") as stream:
            stream.write(source)
        importlib.invalidate_caches()
        command.upgrade(cfg, "head")
        assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
        table = Base.metadata.tables["public.sprint1_module_probe"]
        with database.admin.begin() as connection:
            connection.execute(table.insert(), [{"user_id": users[0], "title": "Own"}, {"user_id": users[1], "title": "Other"}])
        application = create_app(settings=Settings(modules=("app.modules.probe",)), database=database.runtime, verifier=signer.verifier)
        with TestClient(application) as client:
            assert client.get("/api/v1/probe").status_code == 401
            own = client.get("/api/v1/probe", headers={"Authorization": "Bearer " + signer.token(users[0])})
            assert own.status_code == 200
            assert [row["title"] for row in own.json()] == ["Own"]
        database.runtime.check_connection()
        command.check(cfg)
        # The existing core tests must still verify their contracts while another
        # registered module is present, rather than assuming it cannot exist.
        from tests.test_auth_and_config import test_all_44_columns_match_the_approved_proposal, test_offline_migration_needs_no_url_and_never_manages_auth
        from tests.test_database import test_connection_and_migrated_schema_matches_models, test_rls_policies_and_provisioning_function_permissions
        test_all_44_columns_match_the_approved_proposal()
        test_connection_and_migrated_schema_matches_models(database)
        test_rls_policies_and_provisioning_function_permissions(database)
        with pytest.MonkeyPatch.context() as local_patch:
            test_offline_migration_needs_no_url_and_never_manages_auth(local_patch)
    finally:
        # Only this task-created test database is changed. Shared source remains intact.
        if "public.sprint1_module_probe" in Base.metadata.tables:
            command.downgrade(cfg, "0001_sprint1")
            Base.metadata.remove(Base.metadata.tables["public.sprint1_module_probe"])
        for module in ("app.modules.probe.models", "app.modules.probe.router", "app.modules.probe"):
            sys.modules.pop(module, None)
