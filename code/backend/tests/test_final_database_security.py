"""All-table RLS/privilege coverage, deep reference cleanup and real concurrency."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

from psycopg import sql
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.database import Database, create_database_engine
from app.core.schema_models import Base
from tests.final_support import owned_graph
from tests.test_final_database import FINAL_TABLES
from tests.test_planning_integrity import START, draft, insert, placement, publish, table


pytestmark = pytest.mark.integration


def test_every_table_is_covered_by_four_operations_and_a_restrictive_ownership_fence(database):
    with database.admin.connect() as connection:
        policies = connection.execute(sa.text("SELECT tablename,policyname,permissive,cmd,qual,with_check,roles FROM pg_policies WHERE schemaname='public'")).mappings().all()
        for name in FINAL_TABLES:
            own = {p["cmd"]:p for p in policies if p["tablename"]==name and p["policyname"].endswith("_own")}
            assert set(own)=={"SELECT","INSERT","UPDATE","DELETE"},name
            for operation,p in own.items():
                if operation in {"SELECT","UPDATE","DELETE"}: assert "auth.uid()" in p["qual"]
                if operation in {"INSERT","UPDATE"}: assert "auth.uid()" in p["with_check"]
            fence = [p for p in policies if p["tablename"]==name and p["policyname"]=="dayli_owner_fence"]
            assert len(fence)==1 and fence[0]["permissive"]=="RESTRICTIVE" and fence[0]["cmd"]=="ALL"
            assert "auth.uid()" in fence[0]["qual"] and "auth.uid()" in fence[0]["with_check"]
            assert fence[0]["roles"]==["public"]
        assert not connection.scalar(sa.text("SELECT has_table_privilege(:role,'public.alembic_version','SELECT')"),{"role":database.role})
        assert not connection.scalar(sa.text("SELECT has_schema_privilege(:role,'public','CREATE')"),{"role":database.role})
        assert not connection.scalar(sa.text("SELECT has_table_privilege(:role,'auth.users','SELECT')"),{"role":database.role})
        assert not connection.scalar(sa.text("SELECT has_function_privilege('authenticated','public.dayli_publish_pointer()','EXECUTE')"))


def test_all_31_tables_enforce_user_isolation_with_real_owned_records(database,signer,users):
    updates = {
        "profiles":{"full_name":"bad"}, "user_preferences":{"timezone":"UTC"}, "tasks":{"title":"bad"}, "events":{"title":"bad"},
        "availability_windows":{"weekday":1}, "task_requirements":{"required_energy_level":"LOW"}, "task_outcomes":{"task_id":None},
        "schedules":{"current_version_id":None}, "schedule_versions":{"explanation":"bad"}, "schedule_items":{"is_locked":False},
        "unscheduled_tasks":{"reason_code":"bad"}, "schedule_validation_issues":{"message":"bad"},
        "sleep_schedules":{"bedtime":sa.text("TIME '22:00'")}, "sleep_sessions":{"confidence":0.5}, "sleep_usage_signals":{"session_id":None},
        "sleep_quality_ratings":{"rating":5}, "recovery_strategies":{"work_duration_minutes":30}, "recovery_periods":{"status":"TAKEN"},
        "energy_scores":{"sleep_session_id":None}, "focus_sessions":{"status":"COMPLETED"}, "usage_sync_batches":{"status":"FAILED"},
        "app_classifications":{"classification":"NEUTRAL"}, "app_usage_summaries":{"foreground_seconds":100},
        "attention_assessments":{"result_summary":"bad"}, "attention_questionnaire_responses":{"answer_value":"4"},
        "distraction_events":{"status":"CONFIRMED"}, "attention_scores":{"assessment_id":None},
        "decision_executions":{"explanation":"bad"}, "decision_candidates":{"rank":2}, "rescheduling_runs":{"status":"FAILED"},
        "rescheduling_changes":{"change_type":"MOVED"},
    }
    with database.runtime.user_session(signer.token(users[0]),signer.verifier) as session:
        records = owned_graph(session.connection(),users[0])
        assert set(records)==FINAL_TABLES
        for name,row in records.items():
            target = table(name)
            key = next(iter(target.primary_key.columns))
            assert session.scalar(sa.select(key).where(key==row[key.name]))==row[key.name]
    with database.runtime.user_session(signer.token(users[1]),signer.verifier) as session:
        for name,row in records.items():
            target = table(name)
            key = next(iter(target.primary_key.columns))
            predicate = key==row[key.name]
            assert session.execute(sa.select(target).where(predicate)).all()==[],name
            assert session.execute(target.update().where(predicate).values(**updates[name])).rowcount==0,name
            if name not in {"profiles","user_preferences"}:
                assert session.execute(target.delete().where(predicate)).rowcount==0,name
    with pytest.raises(DBAPIError) as error,database.runtime.user_session(signer.token(users[1]),signer.verifier) as session:
        insert(session.connection(),"availability_windows",users[0],weekday=1,start_time=sa.text("TIME '09:00'"),end_time=sa.text("TIME '17:00'"),timezone="UTC")
    assert error.value.orig.sqlstate=="42501"
    for name,column in (("energy_scores","score"),("attention_scores","score"),("task_outcomes","outcome")):
        with pytest.raises(DBAPIError) as error,database.runtime.user_session(signer.token(users[0]),signer.verifier) as session:
            target = table(name)
            session.execute(target.update().values(**{column:sa.column(column)}))
        assert error.value.orig.sqlstate=="42501"


def test_full_graph_source_deletion_and_whole_account_cascade_preserve_the_other_user(database,signer,users):
    graphs = {}
    for owner in users:
        with database.runtime.user_session(signer.token(owner),signer.verifier) as session:
            graphs[owner] = owned_graph(session.connection(),owner)
    with database.runtime.user_session(signer.token(users[0]),signer.verifier) as session:
        records = graphs[users[0]]
        session.execute(table("tasks").delete().where(table("tasks").c.task_id==records["tasks"]["task_id"]))
        item = session.execute(sa.select(table("schedule_items")).where(table("schedule_items").c.id==records["schedule_items"]["id"])).mappings().one()
        assert item["task_id"] is None and item["source_snapshot"]==records["schedule_items"]["source_snapshot"]
        assert session.scalar(sa.select(table("focus_sessions").c.task_id).where(table("focus_sessions").c.id==records["focus_sessions"]["id"])) is None
        assert session.scalar(sa.select(table("task_outcomes").c.task_id).where(table("task_outcomes").c.id==records["task_outcomes"]["id"])) is None
    with database.admin.begin() as connection:
        connection.execute(sa.text("DELETE FROM auth.users WHERE id=:id"),{"id":users[0]})
    with database.admin.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(table("profiles")).where(table("profiles").c.id==users[0]))==0
        for name in FINAL_TABLES-{"profiles"}:
            target = table(name)
            assert connection.scalar(sa.select(sa.func.count()).select_from(target).where(target.c.user_id==users[0]))==0,name
            assert connection.scalar(sa.select(sa.func.count()).select_from(target).where(target.c.user_id==users[1]))>0,name


def test_an_old_permissive_team_policy_cannot_override_the_ownership_fence(database,signer,users):
    with database.admin.begin() as connection:
        connection.execute(sa.text("CREATE POLICY obsolete_team_allow ON public.tasks TO authenticated USING (true) WITH CHECK (true)"))
        connection.execute(table("tasks").insert().values(user_id=users[1],title="Other user",task_type="FLEXIBLE",estimated_duration_minutes=30,category="STUDY",priority=3))
    try:
        with database.runtime.user_session(signer.token(users[0]),signer.verifier) as session:
            assert session.execute(sa.select(table("tasks"))).all()==[]
        with pytest.raises(DBAPIError),database.runtime.user_session(signer.token(users[0]),signer.verifier) as session:
            session.execute(table("tasks").insert().values(user_id=users[1],title="Forged",task_type="FLEXIBLE",estimated_duration_minutes=30,category="STUDY",priority=3))
    finally:
        with database.admin.begin() as connection:
            connection.execute(sa.text("DROP POLICY obsolete_team_allow ON public.tasks"))


def test_simultaneous_publications_accept_exactly_one_current_parent(database,signer,users):
    owner = users[0]
    with database.admin.begin() as connection:
        first,second = draft(connection,owner),draft(connection,owner,version_number=2)
    runtime = Database(create_database_engine(database.runtime_url,pool_size=2))
    entered,release,attempting = Event(),Event(),Event()
    def first_publish():
        with runtime.user_session(signer.token(owner),signer.verifier) as session:
            publish(session.connection(),first["id"])
            entered.set()
            assert release.wait(10)
        return first["id"]
    def second_publish():
        assert entered.wait(10)
        try:
            with runtime.user_session(signer.token(owner),signer.verifier) as session:
                attempting.set()
                publish(session.connection(),second["id"])
        except IntegrityError as error:
            return error.orig.sqlstate
        return "unexpected_success"
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            a,b = workers.submit(first_publish),workers.submit(second_publish)
            try:
                assert attempting.wait(10)
            finally:
                release.set()
            assert a.result(timeout=15)==first["id"]
            assert b.result(timeout=15)=="23514"
        runtime.check_connection()
        with database.admin.connect() as connection:
            assert connection.scalar(sa.select(table("schedules").c.current_version_id).where(table("schedules").c.user_id==owner))==first["id"]
    finally:
        release.set()
        runtime.dispose()


def test_concurrent_overlapping_insertions_leave_one_valid_placement(database,signer,users):
    owner = users[0]
    with database.admin.begin() as connection:
        version = draft(connection,owner)
    runtime = Database(create_database_engine(database.runtime_url,pool_size=2))
    entered,release,attempting = Event(),Event(),Event()
    def first_insert():
        with runtime.user_session(signer.token(owner),signer.verifier) as session:
            placement(session.connection(),owner,version,kind="BREAK",label="First")
            entered.set()
            assert release.wait(10)
    def second_insert():
        assert entered.wait(10)
        try:
            with runtime.user_session(signer.token(owner),signer.verifier) as session:
                attempting.set()
                placement(session.connection(),owner,version,kind="BREAK",label="Second",start_at=START+timedelta(minutes=10),end_at=START+timedelta(minutes=40))
        except IntegrityError as error:
            return error.orig.sqlstate
        return "unexpected_success"
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            a,b = workers.submit(first_insert),workers.submit(second_insert)
            try:
                assert attempting.wait(10)
            finally:
                release.set()
            a.result(timeout=15)
            assert b.result(timeout=15)=="23P01"
        runtime.check_connection()
        with database.admin.connect() as connection:
            assert connection.scalar(sa.select(sa.func.count()).select_from(table("schedule_items")).where(table("schedule_items").c.schedule_version_id==version["id"]))==1
    finally:
        release.set()
        runtime.dispose()
