from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.auth import AuthenticationError
from app.db.models import APPLICATION_TABLES, Base, Event, Profile, Task, UserPreferences
from app.db.registry import managed_table_names
from app.db.session import Database, UnsafeDatabaseRole
from app.db.migration_scope import explicit_schema_reflection

pytestmark = pytest.mark.integration
START = datetime(2026, 11, 16, 9, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


def task_values(user_id, **changes):
    values = dict(user_id=user_id, title="Synthetic task", task_type="FLEXIBLE",
                  estimated_duration_minutes=30, category="STUDY", priority=3)
    values.update(changes)
    return values


def event_values(user_id, **changes):
    values = dict(user_id=user_id, title="Synthetic commitment", category="WORK", start_at=START, end_at=END)
    values.update(changes)
    return values


def weekly_values(user_id, **changes):
    values = dict(user_id=user_id, title="Synthetic weekly shift", category="WORK", recurrence_type="WEEKLY",
                  recurrence_first_date=date(2026, 11, 16), recurrence_start_time=time(22),
                  recurrence_end_time=time(6), recurrence_end_day_offset=1, recurrence_timezone="Africa/Cairo")
    values.update(changes)
    return values


def insert_admin(database, model, values):
    with database.admin.begin() as connection:
        return connection.execute(sa.insert(model).values(**values).returning(*model.__table__.columns)).mappings().one()


def test_connection_and_migrated_schema_matches_models(database):
    database.runtime.check_connection()
    with database.admin.connect() as connection:
        inspector = sa.inspect(connection)
        assert set(inspector.get_table_names(schema="public")) == set(managed_table_names()) | {"alembic_version"}
        for table in Base.metadata.tables.values():
            if table.name not in APPLICATION_TABLES:
                continue  # Preserve all core definitions; module contracts are separate.
            columns = inspector.get_columns(table.name, schema="public")
            assert [column["name"] for column in columns] == list(table.c.keys())
            assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.c}
            assert {c["name"] for c in inspector.get_check_constraints(table.name, schema="public")} == {c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)}
            assert inspector.get_pk_constraint(table.name, schema="public")["constrained_columns"] == list(table.primary_key.columns.keys())
            fk = inspector.get_foreign_keys(table.name, schema="public")
            assert len(fk) == 1 and fk[0]["options"] == {"onupdate": "RESTRICT", "ondelete": "CASCADE"}
            expected_target = ("auth", "users") if table.name == "profiles" else ("public", "profiles")
            assert (fk[0]["referred_schema"], fk[0]["referred_table"]) == expected_target
        with explicit_schema_reflection(connection):
            context = MigrationContext.configure(connection, opts={
                "include_schemas": True, "compare_server_default": True,
                "include_object": lambda obj, name, kind, reflected, compare: kind != "table" or (obj.schema in (None, "public") and name in managed_table_names()),
            })
            assert compare_metadata(context, Base.metadata) == []


def test_trigger_preferences_defaults_and_uniqueness(database, users):
    with database.admin.connect() as connection:
        preferences = connection.execute(sa.select(UserPreferences.__table__).where(UserPreferences.user_id == users[0])).mappings().one()
        assert preferences["timezone"] == "UTC" and preferences["notifications_enabled"] is False
        assert preferences["preferred_work_start"] is None
    with pytest.raises(IntegrityError):
        insert_admin(database, UserPreferences, {"user_id": users[0]})
    with pytest.raises(IntegrityError):
        insert_admin(database, Profile, {"id": users[0]})


@pytest.mark.parametrize("model,values", [
    (Profile, lambda u: {"id": u}), (UserPreferences, lambda u: {"user_id": u}),
    (Task, task_values), (Event, event_values),
])
def test_foreign_keys_reject_missing_owner(database, model, values):
    with pytest.raises(IntegrityError) as error:
        insert_admin(database, model, values(uuid4()))
    assert error.value.orig.sqlstate == "23503"


@pytest.mark.parametrize("changes", [
    {"task_type": "OTHER"}, {"task_type": None}, {"priority": 0}, {"priority": 6}, {"priority": None},
    {"estimated_duration_minutes": 0}, {"estimated_duration_minutes": -1}, {"estimated_duration_minutes": None},
    {"title": " \t"}, {"category": "UNKNOWN"}, {"status": "SCHEDULED"}, {"deadline": "infinity"},
    {"fixed_start_at": START}, {"fixed_end_at": END}, {"fixed_start_at": START, "fixed_end_at": END},
    {"task_type": "FIXED"}, {"task_type": "FIXED", "fixed_start_at": START},
    {"task_type": "FIXED", "fixed_end_at": END},
    {"task_type": "FIXED", "fixed_start_at": START, "fixed_end_at": START},
    {"task_type": "FIXED", "fixed_start_at": END, "fixed_end_at": START},
    {"task_type": "FIXED", "fixed_start_at": START, "fixed_end_at": "infinity"},
])
def test_invalid_task_inputs(database, users, changes):
    with pytest.raises(IntegrityError):
        insert_admin(database, Task, task_values(users[0], **changes))


@pytest.mark.parametrize("priority", [1, 2, 3, 4, 5])
def test_valid_task_priorities_and_fixed_input(database, users, priority):
    row = insert_admin(database, Task, task_values(users[0], priority=priority, task_type="FIXED", fixed_start_at=START, fixed_end_at=END, deadline=START - timedelta(days=1)))
    assert row["priority"] == priority and row["status"] == "UNSCHEDULED"


@pytest.mark.parametrize("changes", [
    {"start_at": None}, {"end_at": None}, {"start_at": END}, {"end_at": START - timedelta(hours=1)},
    {"end_at": "infinity"}, {"recurrence_first_date": date(2026, 11, 16)},
    {"recurrence_until_date": date(2026, 12, 16)}, {"recurrence_start_time": time(9)},
    {"recurrence_end_time": time(10)}, {"recurrence_end_day_offset": 0}, {"recurrence_timezone": "UTC"},
    {"recurrence_type": "DAILY"}, {"status": "CANCELLED"}, {"source": "EMAIL"},
    {"title": " "}, {"category": "UNKNOWN"},
])
def test_invalid_once_events(database, users, changes):
    with pytest.raises(IntegrityError):
        insert_admin(database, Event, event_values(users[0], **changes))


@pytest.mark.parametrize("changes", [
    {"start_at": START}, {"end_at": END}, {"recurrence_first_date": None},
    {"recurrence_first_date": "infinity"}, {"recurrence_until_date": "infinity"},
    {"recurrence_until_date": date(2026, 11, 15)}, {"recurrence_start_time": None},
    {"recurrence_end_time": None}, {"recurrence_end_day_offset": None}, {"recurrence_end_day_offset": -1},
    {"recurrence_end_day_offset": 0}, {"recurrence_start_time": "24:00:00"},
    {"recurrence_end_time": "24:00:00"}, {"recurrence_timezone": None},
    {"recurrence_timezone": ""}, {"recurrence_timezone": " UTC "}, {"recurrence_timezone": "Imaginary/Nowhere"},
])
def test_invalid_weekly_events(database, users, changes):
    with pytest.raises(IntegrityError):
        insert_admin(database, Event, weekly_values(users[0], **changes))


def test_once_weekly_overnight_and_explicit_24_hours(database, users):
    once = insert_admin(database, Event, event_values(users[0]))
    assert once["recurrence_type"] == "ONCE" and once["source"] == "MANUAL" and once["status"] == "SAVED"
    weekly = insert_admin(database, Event, weekly_values(users[0], recurrence_until_date=date(2026, 11, 16)))
    assert weekly["start_at"] is None and weekly["end_at"] is None
    insert_admin(database, Event, weekly_values(users[0], recurrence_end_time=time(22)))
    insert_admin(database, Event, weekly_values(users[0], recurrence_start_time=time(9), recurrence_end_time=time(10), recurrence_end_day_offset=0))


@pytest.mark.parametrize("changes", [
    {"timezone": "Imaginary/Nowhere"}, {"timezone": ""}, {"timezone": " UTC "},
    {"preferred_work_start": time(9)}, {"preferred_work_end": time(17)},
    {"preferred_work_start": time(9), "preferred_work_end": time(9)},
    {"preferred_work_start": "24:00:00", "preferred_work_end": time(17)},
])
def test_invalid_preferences(database, users, changes):
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        connection.execute(sa.update(UserPreferences).where(UserPreferences.user_id == users[0]).values(**changes))


def test_profile_checks_and_case_sensitive_uniqueness(database, users):
    for values in ({"username": ""}, {"username": " space "}, {"full_name": "\t "}):
        with pytest.raises(IntegrityError), database.admin.begin() as connection:
            connection.execute(sa.update(Profile).where(Profile.id == users[0]).values(**values))
    name = "User_" + uuid4().hex
    with database.admin.begin() as connection:
        connection.execute(sa.update(Profile).where(Profile.id == users[0]).values(username=name))
        connection.execute(sa.update(Profile).where(Profile.id == users[1]).values(username=name.lower()))
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        connection.execute(sa.update(Profile).where(Profile.id == users[1]).values(username=name))


def test_rls_select_update_delete_and_forged_insert(database, signer, users):
    own_task = insert_admin(database, Task, task_values(users[0]))
    other_task = insert_admin(database, Task, task_values(users[1]))
    own_event = insert_admin(database, Event, weekly_values(users[0]))
    other_event = insert_admin(database, Event, event_values(users[1]))
    with database.runtime.user_session(signer.token(users[0]), signer.verifier) as session:
        assert set(session.scalars(sa.select(Profile.id))) == {users[0]}
        assert set(session.scalars(sa.select(UserPreferences.user_id))) == {users[0]}
        assert set(session.scalars(sa.select(Task.task_id))) == {own_task["task_id"]}
        assert set(session.scalars(sa.select(Event.event_id))) == {own_event["event_id"]}
        assert session.execute(sa.update(Profile).where(Profile.id == users[1]).values(full_name="bad")).rowcount == 0
        assert session.execute(sa.update(UserPreferences).where(UserPreferences.user_id == users[1]).values(timezone="UTC")).rowcount == 0
        assert session.execute(sa.update(Task).where(Task.task_id == other_task["task_id"]).values(title="bad")).rowcount == 0
        assert session.execute(sa.delete(Task).where(Task.task_id == other_task["task_id"])).rowcount == 0
        assert session.execute(sa.update(Event).where(Event.event_id == other_event["event_id"]).values(title="bad")).rowcount == 0
        assert session.execute(sa.delete(Event).where(Event.event_id == other_event["event_id"])).rowcount == 0
        session.execute(sa.update(Task).where(Task.task_id == own_task["task_id"]).values(priority=5))
        session.execute(sa.delete(Event).where(Event.event_id == own_event["event_id"]))
    for model, values in ((Task, task_values(users[1])), (Event, event_values(users[1]))):
        with pytest.raises(DBAPIError) as error, database.runtime.user_session(signer.token(users[0]), signer.verifier) as session:
            session.execute(sa.insert(model).values(**values))
        assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("model,column", [(Task, "user_id"), (Event, "user_id"), (Profile, "id"), (UserPreferences, "user_id")])
def test_owner_keys_cannot_be_reassigned(database, signer, users, model, column):
    with pytest.raises(DBAPIError) as error, database.runtime.user_session(signer.token(users[0]), signer.verifier) as session:
        session.execute(sa.update(model).values(**{column: users[1]}))
    assert error.value.orig.sqlstate == "42501"


def test_preferences_protected_and_server_columns_not_client_writable(database, signer, users):
    statements = [sa.insert(UserPreferences).values(user_id=users[0]), sa.delete(UserPreferences), sa.delete(Profile),
                  sa.update(Task).values(status="UNSCHEDULED"), sa.update(Event).values(source="MANUAL"),
                  sa.update(Profile).values(created_at=START),
                  sa.insert(Task).values(**task_values(users[0]), task_id=uuid4())]
    for statement in statements:
        with pytest.raises(DBAPIError) as error, database.runtime.user_session(signer.token(users[0]), signer.verifier) as session:
            session.execute(statement)
        assert error.value.orig.sqlstate == "42501"
    with database.runtime.user_session(signer.token(users[0]), signer.verifier) as session:
        session.execute(sa.update(UserPreferences).where(UserPreferences.user_id == users[0]).values(timezone="Africa/Cairo", preferred_work_start=time(22), preferred_work_end=time(6)))


def test_pool_identity_cleared_after_commit_and_rollback(database, signer, users):
    pids = []
    for user_id in users:
        with database.runtime.user_session(signer.token(user_id), signer.verifier) as session:
            pids.append(session.scalar(sa.text("SELECT pg_backend_pid()")))
            assert session.scalar(sa.text("SELECT auth.uid()")) == user_id
        database.runtime.check_connection()
    assert pids[0] == pids[1]  # pool_size=1: genuinely reuse the same connection.
    with pytest.raises(RuntimeError), database.runtime.user_session(signer.token(users[0]), signer.verifier) as session:
        assert session.scalar(sa.text("SELECT auth.uid()")) == users[0]
        raise RuntimeError("Synthetic request rollback")
    database.runtime.check_connection()
    with database.runtime.engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT auth.uid()")) is None
        assert connection.scalar(sa.select(sa.func.count()).select_from(Task)) == 0


def test_invalid_token_never_acquires_a_database_connection(database, signer, monkeypatch):
    monkeypatch.setattr(database.runtime.engine, "connect", lambda: pytest.fail("Unverified token reached database"))
    with pytest.raises(AuthenticationError), database.runtime.user_session("invalid", signer.verifier):
        pass


def test_admin_runtime_is_refused(database, signer, users):
    unsafe = Database(database.admin)
    with pytest.raises(UnsafeDatabaseRole):
        unsafe.check_connection()
    with pytest.raises(UnsafeDatabaseRole), unsafe.user_session(signer.token(users[0]), signer.verifier):
        pass


def test_timestamps_and_profile_cascade(database, users):
    task = insert_admin(database, Task, task_values(users[0]))
    insert_admin(database, Event, weekly_values(users[0]))
    with database.admin.begin() as connection:
        updated = connection.execute(sa.update(Task).where(Task.task_id == task["task_id"]).values(title="changed", updated_at=START).returning(Task.updated_at)).scalar_one()
        assert updated >= task["updated_at"] and updated != START
        connection.execute(sa.delete(Profile).where(Profile.id == users[0]))
        for model in (Task, Event, UserPreferences):
            assert connection.scalar(sa.select(sa.func.count()).select_from(model).where(model.user_id == users[0])) == 0
        assert connection.scalar(sa.text("SELECT count(*) FROM auth.users WHERE id=:id"), {"id": users[0]}) == 1


def test_auth_delete_cascade_in_approved_local_fixture(database, users):
    with database.admin.begin() as connection:
        connection.execute(sa.text("DELETE FROM auth.users WHERE id=:id"), {"id": users[0]})
        assert connection.scalar(sa.select(sa.func.count()).select_from(Profile).where(Profile.id == users[0])) == 0


def test_migration_round_trip_preserves_external_auth(database):
    with database.admin.connect() as connection:
        before = connection.scalar(sa.text("SELECT pg_get_functiondef('auth.uid()'::regprocedure)"))
        auth_oid = connection.scalar(sa.text("SELECT 'auth.users'::regclass::oid"))
    database.migrate("downgrade", "base")
    with database.admin.connect() as connection:
        assert sa.inspect(connection).get_table_names(schema="public") == ["alembic_version"]
        assert connection.scalar(sa.text("SELECT 'auth.users'::regclass::oid")) == auth_oid
        assert connection.scalar(sa.text("SELECT pg_get_functiondef('auth.uid()'::regprocedure)")) == before
    database.migrate("upgrade", "head")
    database.runtime.check_connection()


def test_rls_policies_and_provisioning_function_permissions(database):
    with database.admin.connect() as connection:
        policies = connection.execute(sa.text("SELECT tablename, cmd, qual, with_check FROM pg_policies WHERE schemaname='public' AND tablename IN ('profiles','user_preferences','tasks','events')")).mappings().all()
        assert len(policies) == 13
        for policy in policies:
            if policy["cmd"] in ("SELECT", "UPDATE", "DELETE"):
                assert "auth.uid()" in policy["qual"]
            if policy["cmd"] in ("UPDATE", "INSERT"):
                assert "auth.uid()" in policy["with_check"]
        assert not connection.scalar(sa.text("SELECT has_function_privilege('authenticated', 'public.scheduler_create_preferences()', 'EXECUTE')"))
        assert not connection.scalar(sa.text("SELECT has_table_privilege('anon', 'public.tasks', 'SELECT')"))
        definition = connection.execute(sa.text("SELECT prosecdef, proconfig FROM pg_proc WHERE oid='public.scheduler_create_preferences()'::regprocedure")).one()
        assert definition.prosecdef and 'search_path=""' in definition.proconfig
        assert connection.scalar(sa.text("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('profiles','user_preferences','tasks','events') AND NOT t.tgisinternal")) == 7


@pytest.mark.parametrize("model,factory,key", [(Task, task_values, "task_id"), (Event, event_values, "event_id")])
def test_primary_keys_are_unique(database, users, model, factory, key):
    row = insert_admin(database, model, factory(users[0]))
    values = factory(users[0])
    values[key] = row[key]
    with pytest.raises(IntegrityError) as error:
        insert_admin(database, model, values)
    assert error.value.orig.sqlstate == "23505"


def test_runtime_orm_inserts_and_updates_use_server_defaults(database, signer, users):
    with database.runtime.user_session(signer.token(users[0]), signer.verifier) as session:
        task = Task(**task_values(session.info["user_id"]))
        event = Event(**weekly_values(session.info["user_id"]))
        session.add_all([task, event])
        session.flush()
        assert task.task_id and event.event_id and task.status == "UNSCHEDULED" and event.source == "MANUAL"
        task.priority = 5
        session.flush()
        assert task.updated_at is not None


def test_owner_fk_on_update_restrict(database, users):
    new_id = uuid4()
    with database.admin.begin() as connection:
        connection.execute(sa.text("INSERT INTO auth.users(id) VALUES (:id)"), {"id": new_id})
    with pytest.raises(IntegrityError) as error, database.admin.begin() as connection:
        connection.execute(sa.update(Profile).where(Profile.id == users[0]).values(id=new_id))
    assert error.value.orig.sqlstate == "23503"


def test_failed_onboarding_rolls_back_preferences_too(database, signer):
    user_id = uuid4()
    with database.admin.begin() as connection:
        connection.execute(sa.text("INSERT INTO auth.users(id) VALUES (:id)"), {"id": user_id})
    with pytest.raises(RuntimeError), database.runtime.user_session(signer.token(user_id), signer.verifier) as session:
        session.add(Profile(id=user_id))
        session.flush()
        assert session.scalar(sa.select(sa.func.count()).select_from(UserPreferences)) == 1
        raise RuntimeError("Simulate failed onboarding")
    with database.admin.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(Profile).where(Profile.id == user_id)) == 0
        assert connection.scalar(sa.select(sa.func.count()).select_from(UserPreferences).where(UserPreferences.user_id == user_id)) == 0
