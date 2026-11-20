"""Energy discovery must agree with migrations without changing default APIs."""

import json
import os
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from alembic import command
from alembic.config import Config
from psycopg import sql
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.config import BACKEND_ROOT
from app.core.models import Base
from scripts.provision_runtime import connect_admin
from tests.database_support import staged_database
from tests.test_final_database import FINAL_TABLES


ENERGY_TABLES = (
    "sleep_schedules", "sleep_sessions", "sleep_usage_signals",
    "sleep_quality_ratings", "recovery_strategies", "focus_sessions",
    "recovery_periods", "energy_scores",
)
START = datetime(2026, 11, 20, 23, tzinfo=timezone.utc)


def energy_values(session_id=None):
    return {
        "sleep_schedules": {"bedtime": time(23), "wake_time": time(7), "timezone": "UTC"},
        "sleep_sessions": {
            "sleep_date": date(2026, 11, 21), "start_time": START,
            "end_time": START + timedelta(hours=8), "duration_minutes": 480,
            "timezone": "UTC", "source": "manual", "status": "confirmed", "confidence": 0.8,
        },
        "sleep_usage_signals": {"session_id": session_id, "event_type": "SCREEN_OFF", "event_timestamp": START,
                                "device_id": uuid4(), "client_event_id": uuid4()},
        "sleep_quality_ratings": {"session_id": session_id, "rating": 4},
        "recovery_strategies": {"strategy_type": "POMODORO"},
        "focus_sessions": {"start_time": START},
        "recovery_periods": {
            "start_time": START, "end_time": START + timedelta(minutes=5), "duration_minutes": 5,
        },
        "energy_scores": {"target_date": date(2026, 11, 21), "status": "insufficient_data", "confidence": 0.0,
                          "calculation_key": uuid4(), "calculation_version": "test-fixture-v1", "timezone": "UTC",
                          "input_snapshot": {"missing_inputs": True}, "explanation": "Synthetic insufficient-data fixture"},
    }


def insert_energy(session, table_name, values):
    table = Base.metadata.tables["public." + table_name]
    return session.scalar(table.insert().values(user_id=session.info["user_id"], **values).returning(table.c.id))


def test_energy_migration_discovers_models_without_default_runtime_activation():
    # A fresh interpreter matters: another test may already have imported Energy.
    source = '''
import io, json
from alembic import command
from alembic.config import Config
from app.core.registry import managed_table_names
before = sorted(managed_table_names())
command.upgrade(Config("alembic.ini", output_buffer=io.StringIO()), "head", sql=True)
print(json.dumps({"before": before, "after": sorted(managed_table_names())}))
'''
    environment = os.environ.copy()
    environment["BACKEND_MODULES"] = ""
    result = subprocess.run(
        [sys.executable, "-B", "-c", source], cwd=BACKEND_ROOT,
        env=environment, capture_output=True, text=True, check=True,
    )
    discovery = json.loads(result.stdout)
    assert set(discovery["before"]) == {
        "profiles", "user_preferences", "tasks", "events",
    }
    assert set(discovery["after"]) == FINAL_TABLES


def test_activated_energy_resolves_shared_focus_and_complete_foreign_keys():
    source = '''
import json
from app.core.registry import load_module_models, managed_table_names
load_module_models(("app.modules.energy",))
from app.modules.energy.models import FocusSession
list(FocusSession.__mapper__._sorted_tables)
print(json.dumps(sorted(managed_table_names())))
'''
    result = subprocess.run([sys.executable,"-B","-c",source],cwd=BACKEND_ROOT,
                            capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == FINAL_TABLES


@pytest.mark.integration
def test_sql_upgrade_preserves_core_data_and_matches_alembic(database, monkeypatch):
    with staged_database(database) as target:
        monkeypatch.setenv("MIGRATION_DATABASE_URL", target.render_as_string(hide_password=False))
        cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
        command.upgrade(cfg, "0001_sprint1")
        owner = uuid4()
        with connect_admin(target) as connection:
            connection.execute("INSERT INTO auth.users(id) VALUES (%s)", (owner,))
            connection.execute("INSERT INTO public.profiles(id, full_name) VALUES (%s, %s)", (owner, "Existing synthetic profile"))
            connection.execute("INSERT INTO public.tasks(user_id, title, task_type, estimated_duration_minutes, category, priority) VALUES (%s, 'Existing task', 'FLEXIBLE', 30, 'STUDY', 3)", (owner,))
            connection.execute("INSERT INTO public.events(user_id, title, category, start_at, end_at) VALUES (%s, 'Existing event', 'WORK', %s, %s)", (owner, START, START + timedelta(hours=1)))
            before = {name: connection.execute(sql.SQL("SELECT * FROM public.{}").format(sql.Identifier(name))).fetchall()
                      for name in ("profiles", "user_preferences", "tasks", "events")}
            auth_oid = connection.execute("SELECT 'auth.users'::regclass::oid").fetchone()[0]
        # Read plain UTF-8 deliberately: the deployment artifact must be executable as written.
        with connect_admin(target, autocommit=True) as connection:
            connection.execute((BACKEND_ROOT / "review/0002_energy_upgrade.sql").read_text(encoding="utf-8"))
            assert connection.execute("SELECT version_num FROM public.alembic_version").fetchone()[0] == "0002_energy"
            assert connection.execute("SELECT 'auth.users'::regclass::oid").fetchone()[0] == auth_oid
            for name, rows in before.items():
                assert connection.execute(sql.SQL("SELECT * FROM public.{}").format(sql.Identifier(name))).fetchall() == rows
            assert set(connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()) == {
                (name,) for name in (*ENERGY_TABLES, "profiles", "user_preferences", "tasks", "events", "alembic_version")
            }
        command.upgrade(cfg, "head")
        command.check(cfg)
        # Only empty added/extended modules may be downgraded; core rows survive.
        command.downgrade(cfg, "0002_energy")
        with connect_admin(target, autocommit=True) as connection:
            connection.execute((BACKEND_ROOT / "review/0002_energy_downgrade.sql").read_text(encoding="utf-8"))
            assert connection.execute("SELECT version_num FROM public.alembic_version").fetchone()[0] == "0001_sprint1"
            for name, rows in before.items():
                assert connection.execute(sql.SQL("SELECT * FROM public.{}").format(sql.Identifier(name))).fetchall() == rows


@pytest.mark.integration
def test_energy_owner_policies_apply_to_all_new_tables(database, signer, users):
    owner, stranger = users
    created = {}
    updates = {
        "sleep_schedules": {"bedtime": time(22)},
        "sleep_sessions": {"confidence": 0.5},
        "sleep_usage_signals": {"event_type": "SCREEN_ON"},
        "sleep_quality_ratings": {"rating": 5},
        "recovery_strategies": {"work_duration_minutes": 30},
        "focus_sessions": {"duration_minutes": 30},
        "recovery_periods": {"duration_minutes": 5},
        "energy_scores": {"sleep_session_id": None},  # Measurements are append-only; source cleanup is the only writable column.
    }
    with database.runtime.user_session(signer.token(owner), signer.verifier) as session:
        created["sleep_sessions"] = insert_energy(session, "sleep_sessions", energy_values()["sleep_sessions"])
        values = energy_values(created["sleep_sessions"])
        for name in ENERGY_TABLES:
            if name != "sleep_sessions":
                created[name] = insert_energy(session, name, values[name])
    with database.runtime.user_session(signer.token(stranger), signer.verifier) as session:
        for name in ENERGY_TABLES:
            table = Base.metadata.tables["public." + name]
            assert session.execute(sa.select(table)).all() == []
            assert session.execute(table.update().where(table.c.id == created[name]).values(**updates[name])).rowcount == 0
            assert session.execute(table.delete().where(table.c.id == created[name])).rowcount == 0
    for name in ENERGY_TABLES:
        table = Base.metadata.tables["public." + name]
        with pytest.raises(sa.exc.DBAPIError) as error, database.runtime.user_session(signer.token(stranger), signer.verifier) as session:
            session.execute(table.insert().values(user_id=owner, **values[name]))
        assert error.value.orig.sqlstate == "42501"
    with database.runtime.user_session(signer.token(owner), signer.verifier) as session:
        for name in ENERGY_TABLES:
            table = Base.metadata.tables["public." + name]
            assert session.scalar(sa.select(table.c.id).where(table.c.id == created[name])) == created[name]


@pytest.mark.integration
def test_energy_composite_foreign_keys_reject_other_users_sleep(database, signer, users):
    with database.runtime.user_session(signer.token(users[0]), signer.verifier) as session:
        sleep_id = insert_energy(session, "sleep_sessions", energy_values()["sleep_sessions"])
    for name in ("sleep_usage_signals", "sleep_quality_ratings"):
        with pytest.raises(IntegrityError) as error, database.runtime.user_session(signer.token(users[1]), signer.verifier) as session:
            insert_energy(session, name, energy_values(sleep_id)[name])
        assert error.value.orig.sqlstate == "23503"


@pytest.mark.integration
def test_energy_profile_cascade_preserves_other_user(database, signer, users):
    ids = {}
    for user_id in users:
        with database.runtime.user_session(signer.token(user_id), signer.verifier) as session:
            ids[user_id] = insert_energy(session, "sleep_sessions", energy_values()["sleep_sessions"])
            values = energy_values(ids[user_id])
            for name in ENERGY_TABLES:
                if name != "sleep_sessions":
                    insert_energy(session, name, values[name])
    with database.admin.begin() as connection:
        connection.execute(sa.text("DELETE FROM public.profiles WHERE id=:id"), {"id": users[0]})
        for name in ENERGY_TABLES:
            table = Base.metadata.tables["public." + name]
            assert connection.scalar(sa.select(sa.func.count()).select_from(table).where(table.c.user_id == users[0])) == 0
            assert connection.scalar(sa.select(sa.func.count()).select_from(table).where(table.c.user_id == users[1])) == 1
