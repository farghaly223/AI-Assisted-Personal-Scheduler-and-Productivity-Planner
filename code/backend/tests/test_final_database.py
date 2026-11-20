"""The final database extends deployed Energy without duplicating its entities."""

import importlib
import importlib.util
import hashlib
from types import SimpleNamespace

from alembic.config import Config
from alembic.script import ScriptDirectory

import pytest
import sqlalchemy as sa

from app.core.models import Base
from app.core.database import Database, UnsafeDatabaseRole
from app.core.config import BACKEND_ROOT


FINAL_TABLES = {
    "profiles", "user_preferences", "tasks", "events",
    "sleep_schedules", "sleep_sessions", "sleep_usage_signals", "sleep_quality_ratings",
    "recovery_strategies", "recovery_periods", "energy_scores", "focus_sessions",
    "availability_windows", "task_requirements", "task_outcomes", "schedules",
    "schedule_versions", "schedule_items", "unscheduled_tasks", "schedule_validation_issues",
    "usage_sync_batches", "app_classifications", "app_usage_summaries", "attention_assessments",
    "attention_questionnaire_responses", "distraction_events", "attention_scores",
    "decision_executions", "decision_candidates", "rescheduling_runs", "rescheduling_changes",
}


def test_final_revision_is_additive_and_applied_migrations_are_frozen():
    scripts = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))
    assert scripts.get_heads() == ["0003_dayli_foundation"]
    assert scripts.get_revision("0003_dayli_foundation").down_revision == "0002_energy"
    expected = {
        "0001_sprint1_foundation.py": "55f0831c70d1a2ab507c7756a3556c5d9bf4de60b498676b31980c57234d7611",
        "0002_energy_tables.py": "e2325a985e69bc7f57776a768ed02275681323daa974241519cc809cf3f3f6e3",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((BACKEND_ROOT / "alembic/versions" / name).read_bytes()).hexdigest() == digest


def test_final_metadata_provides_one_complete_application_contract():
    assert importlib.util.find_spec("app.core.schema_models") is not None
    contract = importlib.import_module("app.core.schema_models")
    assert contract.Base is Base
    assert {table.name for table in Base.metadata.tables.values()} == FINAL_TABLES
    assert "auth.users" not in Base.metadata.tables


def test_focus_session_has_one_attention_owned_class():
    assert importlib.util.find_spec("app.modules.attention") is not None
    from app.modules.attention.focus import FocusSession
    from app.modules.energy.models import FocusSession as energy_focus
    assert energy_focus is FocusSession
    assert FocusSession.__table__.metadata is Base.metadata


@pytest.mark.integration
def test_administrative_runtime_opt_in_cannot_bypass_user_isolation(database):
    administrative = Database(database.admin, allow_admin_runtime=True)
    with pytest.raises(UnsafeDatabaseRole):
        administrative.check_connection()


@pytest.mark.integration
def test_runtime_rejects_ownership_of_installed_but_unloaded_module_tables(database, monkeypatch):
    # Default API discovery still has four models; security covers every installed
    # profile-owned table, including a module that has not been activated yet.
    monkeypatch.setattr("app.core.database.managed_table_names",lambda: frozenset({"profiles","user_preferences","tasks","events"}))
    with database.admin.begin() as connection:
        owner = connection.scalar(sa.text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.sleep_schedules'::regclass"))
        quote = connection.dialect.identifier_preparer.quote
        connection.execute(sa.text("ALTER TABLE public.sleep_schedules OWNER TO " + quote(database.role)))
    try:
        with pytest.raises(UnsafeDatabaseRole):
            database.runtime.check_connection()
    finally:
        with database.admin.begin() as connection:
            connection.execute(sa.text("ALTER TABLE public.sleep_schedules OWNER TO " + quote(owner)))


@pytest.mark.integration
def test_runtime_rejects_schema_creation_privileges_even_in_development(database):
    with database.admin.begin() as connection:
        quote = connection.dialect.identifier_preparer.quote
        connection.execute(sa.text("GRANT CREATE ON SCHEMA public TO " + quote(database.role)))
    try:
        with pytest.raises(UnsafeDatabaseRole):
            Database(database.runtime.engine,allow_admin_runtime=True).check_connection()
    finally:
        with database.admin.begin() as connection:
            connection.execute(sa.text("REVOKE CREATE ON SCHEMA public FROM " + quote(database.role)))


def test_password_bearing_remote_role_ddl_is_rejected_before_execution():
    from scripts.provision_runtime import create_restricted_role
    connection = SimpleNamespace(info=SimpleNamespace(host="db.example.invalid"),execute=lambda *args: pytest.fail("Remote password DDL reached SQL"))
    with pytest.raises(ValueError,match="Remote password-bearing"):
        create_restricted_role(connection,"scheduler_runtime","synthetic-test-password")


@pytest.mark.integration
def test_authenticated_snapshot_does_not_require_task_requirement_write_grants(database, signer, users):
    from app.modules.scheduler.models import Task
    from app.modules.scheduler.planning_models import TaskRequirement
    with database.runtime.user_session(signer.token(users[0]),signer.verifier) as session:
        task = Task(user_id=users[0],title="Owned snapshot",task_type="FLEXIBLE",
                    estimated_duration_minutes=30,category="STUDY",priority=3)
        session.add(task)
        session.flush()
        task_id = task.task_id
        session.add(TaskRequirement(task_id=task_id,user_id=users[0]))
    identity = signer.verifier.verify(signer.token(users[0]))
    with database.admin.begin() as connection:
        # Simulate the Data API's authenticated DB role, with no writer membership.
        connection.execute(sa.text("SET LOCAL ROLE authenticated"))
        connection.execute(sa.text("SELECT set_config('request.jwt.claim.sub',:subject,true)"),{"subject":str(identity.user_id)})
        assert not connection.scalar(sa.text("SELECT has_table_privilege(current_user,'public.task_requirements','UPDATE')"))
        snapshot = connection.scalar(sa.text("SELECT public.dayli_task_snapshot(:id)"),{"id":task_id})
        assert snapshot["task"]["task_id"]==str(task_id)
        assert snapshot["requirements"]["can_split"] is False


@pytest.mark.integration
def test_readiness_rejects_missing_registered_table_read_grants(database):
    with database.admin.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.text("REVOKE SELECT ON public.profiles FROM authenticated,dayli_app_writer"))
            # Validate the restricted login's effective grants in this transaction.
            quote = connection.dialect.identifier_preparer.quote
            connection.execute(sa.text("SET LOCAL SESSION AUTHORIZATION " + quote(database.role)))
            from app.core.database import assert_runtime_role
            with pytest.raises(UnsafeDatabaseRole,match="read privileges"):
                assert_runtime_role(connection)
        finally:
            transaction.rollback()  # Restore grants and session authorization.


@pytest.mark.integration
def test_readiness_rejects_missing_auth_identity_function_grant(database):
    with database.admin.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.text("REVOKE EXECUTE ON FUNCTION auth.uid() FROM authenticated"))
            quote = connection.dialect.identifier_preparer.quote
            connection.execute(sa.text("SET LOCAL SESSION AUTHORIZATION " + quote(database.role)))
            from app.core.database import assert_runtime_role
            with pytest.raises(UnsafeDatabaseRole,match="auth.uid"):
                assert_runtime_role(connection)
        finally:
            transaction.rollback()
