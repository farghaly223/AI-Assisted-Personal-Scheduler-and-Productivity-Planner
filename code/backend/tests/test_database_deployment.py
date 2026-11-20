"""The deployed-Energy SQL upgrade matches one Alembic history and preserves data."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from alembic import command
from alembic.config import Config
from psycopg import sql
import psycopg
import pytest

from app.core.config import BACKEND_ROOT
from scripts.provision_runtime import connect_admin
from tests.database_support import staged_database
from tests.test_final_database import FINAL_TABLES


SCRIPTS = {"0002_energy": "upgrade_from_0002_energy.sql"}


def test_manual_sql_exports_exist_without_credentials_or_auth_fixtures():
    for name in SCRIPTS.values():
        path = BACKEND_ROOT / "review" / name
        assert path.is_file(), name
        content = path.read_text(encoding="utf-8")
        assert not content.startswith("\ufeff")
        assert "0003_dayli_foundation" in content and "COMMIT;" in content
        assert "CREATE TABLE auth." not in content and "CREATE SCHEMA auth" not in content
        assert " PASSWORD " not in content and "postgresql+psycopg://" not in content


def fingerprint(connection):
    connection.execute("SET LOCAL search_path='pg_catalog'")
    queries = {
        "columns": """SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attname""",
        "constraints": "SELECT c.relname,con.conname,pg_get_constraintdef(con.oid) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,con.conname",
        "indexes": "SELECT c.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,i.relname",
        "triggers": "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        "policies": "SELECT tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname",
        "rls": "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname",
        "functions": "SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND (p.proname LIKE 'dayli_%' OR p.proname LIKE 'scheduler_%' OR p.proname LIKE 'energy_%') ORDER BY p.proname",
        "table_grants": "SELECT table_name,grantee,privilege_type,is_grantable FROM information_schema.role_table_grants WHERE table_schema='public' ORDER BY table_name,grantee,privilege_type",
        "column_grants": "SELECT table_name,column_name,grantee,privilege_type,is_grantable FROM information_schema.role_column_grants WHERE table_schema='public' ORDER BY table_name,column_name,grantee,privilege_type",
        "function_grants": "SELECT p.proname,COALESCE(r.rolname,'PUBLIC'),a.privilege_type,a.is_grantable FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(p.proacl) a LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE n.nspname='public' AND (p.proname LIKE 'dayli_%' OR p.proname LIKE 'scheduler_%' OR p.proname LIKE 'energy_%') ORDER BY p.proname,r.rolname,a.privilege_type",
    }
    return {name: connection.execute(query).fetchall() for name,query in queries.items()}


@pytest.mark.integration
@pytest.mark.parametrize("start", ["0002_energy"])
def test_manual_installation_matches_alembic_for_every_deployment_path(database, monkeypatch, start):
    with staged_database(database) as target:
        cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
        monkeypatch.setenv("MIGRATION_DATABASE_URL", target.render_as_string(hide_password=False))
        if start != "base":
            command.upgrade(cfg, start)
        with connect_admin(target, autocommit=True) as connection:
            connection.execute((BACKEND_ROOT / "review" / SCRIPTS[start]).read_text(encoding="utf-8"))
            assert connection.execute("SELECT version_num FROM public.alembic_version").fetchone()[0] == "0003_dayli_foundation"
            assert {r[0] for r in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")} == FINAL_TABLES | {"alembic_version"}
        with connect_admin(target) as connection:
            # Use psycopg for both paths so PostgreSQL arrays/types compare identically.
            with connect_admin(database.admin_url) as reference:
                assert fingerprint(connection) == fingerprint(reference)
        command.check(cfg)


@pytest.mark.integration
def test_upgrade_from_deployed_energy_preserves_every_existing_column(database, monkeypatch):
    with staged_database(database) as target:
        monkeypatch.setenv("MIGRATION_DATABASE_URL", target.render_as_string(hide_password=False))
        cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
        command.upgrade(cfg, "0002_energy")
        owner, pending_owner = uuid4(),uuid4()
        start = datetime(2026, 11, 20, 23, tzinfo=timezone.utc)
        with connect_admin(target) as connection:
            for user in (owner,pending_owner):
                connection.execute("INSERT INTO auth.users(id) VALUES (%s)",(user,))
                connection.execute("INSERT INTO public.profiles(id,full_name) VALUES (%s,'Existing test profile')",(user,))
            connection.execute("INSERT INTO public.tasks(user_id,title,task_type,estimated_duration_minutes,category,priority,context) VALUES (%s,'Existing work','FLEXIBLE',30,'STUDY',3,'Preserve description')",(owner,))
            connection.execute("INSERT INTO public.events(user_id,title,category,start_at,end_at) VALUES (%s,'Existing commitment','WORK',%s,%s)",(owner,start,start+timedelta(hours=1)))
            connection.execute("INSERT INTO public.sleep_schedules(user_id,bedtime,wake_time,timezone) VALUES (%s,'23:00','07:00','Africa/Cairo')",(owner,))
            sleep_id = connection.execute("INSERT INTO public.sleep_sessions(user_id,sleep_date,start_time,end_time,duration_minutes,timezone,source,status,confidence) VALUES (%s,'2026-11-21',%s,%s,480,'UTC','manual','confirmed',0.8) RETURNING id",(owner,start,start+timedelta(hours=8))).fetchone()[0]
            pending_id = connection.execute("INSERT INTO public.sleep_sessions(user_id,sleep_date,start_time,end_time,duration_minutes,timezone) VALUES (%s,'2026-11-21',%s,%s,480,'UTC') RETURNING id",(pending_owner,start,start+timedelta(hours=8))).fetchone()[0]
            connection.execute("INSERT INTO public.sleep_usage_signals(user_id,session_id,event_type,event_timestamp) VALUES (%s,%s,'SCREEN_OFF',%s)",(owner,sleep_id,start))
            connection.execute("INSERT INTO public.sleep_quality_ratings(user_id,session_id,rating) VALUES (%s,%s,4)",(owner,sleep_id))
            connection.execute("INSERT INTO public.recovery_strategies(user_id) VALUES (%s)",(owner,))
            connection.execute("INSERT INTO public.focus_sessions(user_id,start_time) VALUES (%s,%s)",(owner,start))
            for source in ("PLANNED","MANUAL"):
                connection.execute("INSERT INTO public.recovery_periods(user_id,start_time,end_time,duration_minutes,source) VALUES (%s,%s,%s,5,%s)",(owner,start,start+timedelta(minutes=5),source))
            score_id = connection.execute("INSERT INTO public.energy_scores(user_id,target_date,score,energy_level,confidence,sleep_duration_minutes,workload_score,insights) VALUES (%s,'2026-11-21',72,'MEDIUM',0.6,480,0,'{\"legacy_note\": \"keep me\"}') RETURNING id",(owner,)).fetchone()[0]
            names = [r[0] for r in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename")]
            original = {}
            for name in names:
                cursor = connection.execute(sql.SQL("SELECT * FROM public.{} ORDER BY 1").format(sql.Identifier(name)))
                original[name] = ([c.name for c in cursor.description],cursor.fetchall())
            auth_definition = connection.execute("SELECT pg_get_functiondef('auth.uid()'::regprocedure)").fetchone()[0]
        with connect_admin(target,autocommit=True) as connection:
            connection.execute((BACKEND_ROOT / "review/upgrade_from_0002_energy.sql").read_text(encoding="utf-8"))
            for name,(columns,rows) in original.items():
                result = connection.execute(sql.SQL("SELECT {} FROM public.{} ORDER BY 1").format(sql.SQL(",").join(map(sql.Identifier,columns)),sql.Identifier(name))).fetchall()
                assert result == rows,name
            confirmed = connection.execute("SELECT confirmation_method,confirmed_start_at,confirmed_end_at FROM public.sleep_sessions WHERE id=%s",(sleep_id,)).fetchone()
            assert confirmed == ("LEGACY",start,start+timedelta(hours=8))
            assert connection.execute("SELECT confirmed_at,confirmed_start_at,confirmed_end_at FROM public.sleep_sessions WHERE id=%s",(pending_id,)).fetchone() == (None,None,None)
            assert connection.execute("SELECT calculation_version,calculation_key,timezone FROM public.energy_scores WHERE id=%s",(score_id,)).fetchone() == ("legacy_unversioned",score_id,None)
            assert connection.execute("SELECT device_id=user_id,client_event_id=id,expires_at=created_at+INTERVAL '7 days' FROM public.sleep_usage_signals").fetchone() == (True,True,True)
            assert connection.execute("SELECT source,status,observed_start_at IS NULL FROM public.recovery_periods ORDER BY source").fetchall() == [("MANUAL","TAKEN",False),("PLANNED","PLANNED",True)]
            assert connection.execute("SELECT pg_get_functiondef('auth.uid()'::regprocedure)").fetchone()[0] == auth_definition
        with pytest.raises(psycopg.errors.CheckViolation),connect_admin(target) as connection:
            connection.execute("INSERT INTO public.decision_executions(user_id,execution_key,energy_score_id,algorithm_version,input_snapshot) VALUES (%s,%s,%s,'test-v1','{}')",(owner,uuid4(),score_id))
        command.check(cfg)
        # A repeat execution fails before DDL, and leaves all committed rows intact.
        with pytest.raises(psycopg.Error),connect_admin(target,autocommit=True) as connection:
            connection.execute((BACKEND_ROOT / "review/upgrade_from_0002_energy.sql").read_text(encoding="utf-8"))
        with connect_admin(target) as connection:
            assert connection.execute("SELECT count(*) FROM public.energy_scores WHERE id=%s",(score_id,)).fetchone()[0] == 1

