"""Meaningful database invariants, exercised on disposable local PostgreSQL."""

from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.models import Base
from app.modules.scheduler.models import Task, Event


pytestmark = pytest.mark.integration
START = datetime(2026, 11, 20, 9, tzinfo=timezone.utc)


def table(name):
    from app.core import schema_models
    return schema_models.Base.metadata.tables["public." + name]


def insert(connection, name, owner, **values):
    target = table(name)
    return connection.execute(target.insert().values(user_id=owner, **values).returning(*target.c)).mappings().one()


def task(connection, owner, **changes):
    values = dict(title="Test work", task_type="FLEXIBLE", estimated_duration_minutes=30,
                  category="STUDY", priority=3)
    values.update(changes)
    return connection.execute(sa.insert(Task).values(user_id=owner, **values).returning(*Task.__table__.c)).mappings().one()


def draft(connection, owner, **changes):
    schedule_id = connection.scalar(sa.select(table("schedules").c.id).where(table("schedules").c.user_id == owner))
    if schedule_id is None:
        schedule_id = insert(connection, "schedules", owner)["id"]
    values = dict(schedule_id=schedule_id, version_number=1, window_start_at=START,
                  window_end_at=START + timedelta(days=2), timezone="UTC")
    values.update(changes)
    return insert(connection, "schedule_versions", owner, **values)


def publish(connection, version_id):
    versions = table("schedule_versions")
    connection.execute(versions.update().where(versions.c.id == version_id).values(status="PUBLISHED"))


def placement(connection, owner, version, work=None, **changes):
    values = dict(schedule_version_id=version["id"], kind="TASK", task_id=work["task_id"] if work else None,
                  start_at=START, end_at=START + timedelta(minutes=30))
    values.update(changes)
    return insert(connection, "schedule_items", owner, **values)


def test_publication_freezes_history_but_source_deletion_and_account_erasure_work(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        work = task(connection, owner)
        version = draft(connection, owner)
        item = placement(connection, owner, version, work)
        assert item["source_snapshot"]["task"]["task_id"] == str(work["task_id"])
        publish(connection, version["id"])
        assert connection.scalar(sa.select(table("schedules").c.current_version_id).where(table("schedules").c.user_id == owner)) == version["id"]
    for statement in (
        table("schedule_items").update().where(table("schedule_items").c.id == item["id"]).values(end_at=START + timedelta(minutes=20)),
        table("schedule_items").delete().where(table("schedule_items").c.id == item["id"]),
        table("schedule_versions").delete().where(table("schedule_versions").c.id == version["id"]),
        table("schedules").update().where(table("schedules").c.user_id == owner).values(current_version_id=None),
    ):
        with pytest.raises(IntegrityError), database.admin.begin() as connection:
            connection.execute(statement)
    with database.admin.begin() as connection:
        connection.execute(sa.delete(Task).where(Task.task_id == work["task_id"]))
        kept = connection.execute(sa.select(table("schedule_items")).where(table("schedule_items").c.id == item["id"])).mappings().one()
        assert kept["task_id"] is None and kept["source_snapshot"] == item["source_snapshot"]
        connection.execute(sa.text("DELETE FROM public.profiles WHERE id=:id"), {"id": owner})
        for target in Base.metadata.tables.values():
            if "user_id" in target.c:
                assert connection.scalar(sa.select(sa.func.count()).select_from(target).where(target.c.user_id == owner)) == 0


def test_stale_drafts_and_stale_source_snapshots_cannot_publish(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        work = task(connection, owner)
        first = draft(connection, owner)
        placement(connection, owner, first, work)
        stale = draft(connection, owner, version_number=2)
        placement(connection, owner, stale, work)
        publish(connection, first["id"])
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        publish(connection, stale["id"])
    with database.admin.begin() as connection:
        next_version = draft(connection, owner, version_number=3, parent_version_id=first["id"])
        placement(connection, owner, next_version, work)
        connection.execute(sa.update(Task).where(Task.task_id == work["task_id"]).values(estimated_duration_minutes=45))
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        publish(connection, next_version["id"])


def test_overlaps_are_atomic_and_adjacent_intervals_are_allowed(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        version = draft(connection, owner)
        placement(connection, owner, version, kind="BREAK", label="Rest")
        placement(connection, owner, version, kind="BREAK", label="Adjacent", start_at=START + timedelta(minutes=30), end_at=START + timedelta(minutes=40))
    with pytest.raises(IntegrityError) as error, database.admin.begin() as connection:
        placement(connection, owner, version, kind="BREAK", label="Overlap", start_at=START + timedelta(minutes=25))
    assert error.value.orig.sqlstate == "23P01"


def test_optional_foreign_keys_still_reject_cross_user_references(database, users):
    with database.admin.begin() as connection:
        work = task(connection, users[1])
        version = draft(connection, users[0])
    with pytest.raises(IntegrityError) as error, database.admin.begin() as connection:
        placement(connection, users[0], version, work)
    assert error.value.orig.sqlstate == "23503"


@pytest.mark.parametrize("local,zone", [
    (datetime(2026, 3, 8, 2, 30), "America/New_York"),
    (datetime(2026, 11, 1, 1, 30), "America/New_York"),
    (datetime(2026, 4, 5, 1, 45), "Australia/Lord_Howe"),
    (datetime(2026, 10, 4, 2, 15), "Australia/Lord_Howe"),
])
def test_database_rejects_local_gaps_and_folds_including_half_hour_transitions(database, local, zone):
    with pytest.raises(IntegrityError) as error, database.admin.begin() as connection:
        connection.scalar(sa.text("SELECT public.dayli_resolve_local_time(:local,:zone)"), {"local": local, "zone": zone})
    assert error.value.orig.sqlstate == "23514"


def test_weekly_events_keep_the_same_local_clock_across_dst(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        event = connection.execute(sa.insert(Event).values(
            user_id=owner, title="Weekly appointment", category="MEETING", recurrence_type="WEEKLY",
            recurrence_first_date=date(2026, 3, 1), recurrence_start_time=time(9), recurrence_end_time=time(10),
            recurrence_end_day_offset=0, recurrence_timezone="America/New_York",
        ).returning(Event.event_id)).scalar_one()
        version = draft(connection, owner, window_start_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                        window_end_at=datetime(2026, 3, 9, tzinfo=timezone.utc))
        for day, hour in ((date(2026, 3, 1), 14), (date(2026, 3, 8), 13)):
            begin = datetime.combine(day, time(hour), timezone.utc)
            placement(connection, owner, version, kind="EVENT", event_id=event, task_id=None,
                      event_occurrence_date=day, start_at=begin, end_at=begin + timedelta(hours=1), is_locked=True)
        publish(connection, version["id"])


def test_unconfirmed_sleep_cannot_supply_confirmed_energy_measurements(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        session = insert(connection, "sleep_sessions", owner, sleep_date=date(2026, 11, 20),
                         start_time=START - timedelta(hours=9), end_time=START - timedelta(hours=1),
                         duration_minutes=480, timezone="UTC", detection_version="test-detector-v1")
        assert session["confirmed_start_at"] is None and session["confidence"] is None
    values = dict(target_date=date(2026, 11, 20), status="ok", score=70, energy_level="MEDIUM",
                  sleep_session_id=session["id"], sleep_duration_minutes=480,
                  calculation_key=uuid4(), calculation_version="test-fixture-v1", timezone="UTC",
                  input_snapshot={"sleep": "test input"}, explanation="Synthetic fixture, no scientific weights")
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        insert(connection, "energy_scores", owner, **values)
    with database.admin.begin() as connection:
        sleeps = table("sleep_sessions")
        connection.execute(sleeps.update().where(sleeps.c.id == session["id"]).values(
            status="corrected", confirmed_start_at=START - timedelta(hours=8),
            confirmed_end_at=START - timedelta(hours=1), confirmed_at=START,
            confirmation_method="USER_CORRECTED",
        ))
        values["sleep_duration_minutes"] = 420
        score = insert(connection, "energy_scores", owner, **values)
        assert score["sleep_quality_rating"] is None and score["confidence"] is None
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        connection.execute(table("energy_scores").update().where(table("energy_scores").c.id == score["id"]).values(score=90))


def test_usage_batch_processing_freezes_content_and_revision_is_monotonic(database, users):
    owner, device = users[0], uuid4()
    values = dict(device_id=device, local_date=date(2026, 11, 20), timezone="UTC", revision=2,
                  client_batch_id=uuid4(), window_start_at=START, window_end_at=START + timedelta(hours=1))
    with database.admin.begin() as connection:
        batch = insert(connection, "usage_sync_batches", owner, **values)
        insert(connection, "app_usage_summaries", owner, batch_id=batch["id"], app_package="org.test.app", foreground_seconds=120)
        batches = table("usage_sync_batches")
        connection.execute(batches.update().where(batches.c.id == batch["id"]).values(status="PROCESSED", processed_at=START))
    for changes in ({"screen_active_seconds": 100}, {"revision": 3}):
        with pytest.raises(IntegrityError), database.admin.begin() as connection:
            connection.execute(batches.update().where(batches.c.id == batch["id"]).values(**changes))
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        insert(connection, "usage_sync_batches", owner, **{**values, "client_batch_id": uuid4(), "revision": 1})
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        insert(connection, "app_usage_summaries", owner, batch_id=batch["id"], app_package="org.test.second", foreground_seconds=10)


def test_questionnaire_answers_must_belong_to_the_instrument_and_completion_freezes_them(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        assessment = insert(connection, "attention_assessments", owner, questionnaire_version="fixture-v1",
                            questionnaire_snapshot={"questions": [{"key": "focus", "required": True}]})
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        insert(connection, "attention_questionnaire_responses", owner, assessment_id=assessment["id"], question_key="unknown", answer_value="3")
    with database.admin.begin() as connection:
        answer = insert(connection, "attention_questionnaire_responses", owner, assessment_id=assessment["id"], question_key="focus", answer_value="3")
        assessments = table("attention_assessments")
        connection.execute(assessments.update().where(assessments.c.id == assessment["id"]).values(
            status="COMPLETED", completed_at=START, calculation_version="fixture-v1", result_summary="Synthetic fixture",
        ))
    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        responses = table("attention_questionnaire_responses")
        connection.execute(responses.update().where(responses.c.id == answer["id"]).values(answer_value="4"))


def test_publication_rejects_unfinished_decision_executions(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        first = draft(connection,owner)
        publish(connection,first["id"])
        execution = insert(connection,"decision_executions",owner,execution_key=uuid4(),
                           input_schedule_version_id=first["id"],algorithm_version="test-v1",input_snapshot={})
        next_version = draft(connection,owner,version_number=2,parent_version_id=first["id"],decision_execution_id=execution["id"])
    with pytest.raises(IntegrityError),database.admin.begin() as connection:
        publish(connection,next_version["id"])


def test_automatic_revisions_preserve_locks_and_manual_adjustments_record_a_reason(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        first = draft(connection,owner)
        locked = placement(connection,owner,first,kind="BREAK",label="Locked recovery",is_locked=True)
        publish(connection,first["id"])
        automatic = draft(connection,owner,version_number=2,parent_version_id=first["id"],origin="RESCHEDULED")
        placement(connection,owner,automatic,kind="BREAK",label="Moved recovery",is_locked=True,
                  logical_key=locked["logical_key"],start_at=START+timedelta(hours=1),end_at=START+timedelta(hours=1,minutes=30))
    with pytest.raises(IntegrityError),database.admin.begin() as connection:
        publish(connection,automatic["id"])
    with database.admin.begin() as connection:
        manual = draft(connection,owner,version_number=3,parent_version_id=first["id"],origin="MANUAL",explanation="User intentionally moved recovery")
        placement(connection,owner,manual,kind="BREAK",label="Manual recovery",is_locked=True,
                  logical_key=locked["logical_key"],start_at=START+timedelta(hours=1),end_at=START+timedelta(hours=1,minutes=30))
        publish(connection,manual["id"])


def test_same_day_energy_recalculations_use_distinct_idempotency_keys(database, users):
    owner = users[0]
    values = dict(target_date=START.date(),status="insufficient_data",calculation_key=uuid4(),
                  calculation_version="test-v1",timezone="UTC",input_snapshot={},explanation="No confirmed inputs")
    with database.admin.begin() as connection:
        first = insert(connection,"energy_scores",owner,**values)
        second = insert(connection,"energy_scores",owner,**{**values,"calculation_key":uuid4(),"calculation_version":"test-v2"})
        assert first["id"]!=second["id"] and first["score"] is None and first["confidence"] is None
    with pytest.raises(IntegrityError) as error,database.admin.begin() as connection:
        insert(connection,"energy_scores",owner,**values)
    assert error.value.orig.sqlstate=="23505"


def test_rejected_proposals_freeze_their_draft_metadata_and_items(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        first = draft(connection,owner)
        publish(connection,first["id"])
        proposal = draft(connection,owner,version_number=2,parent_version_id=first["id"],origin="RESCHEDULED")
        item = placement(connection,owner,proposal,kind="BREAK",label="Rejected recovery")
        run = insert(connection,"rescheduling_runs",owner,request_key=uuid4(),previous_version_id=first["id"],trigger_type="MANUAL",reason="User rejected this proposal")
        runs = table("rescheduling_runs")
        connection.execute(runs.update().where(runs.c.id==run["id"]).values(status="PROPOSED",new_version_id=proposal["id"]))
        connection.execute(runs.update().where(runs.c.id==run["id"]).values(status="REJECTED",finished_at=sa.func.now()))
    for statement in (
        table("schedule_items").update().where(table("schedule_items").c.id==item["id"]).values(label="Rewritten rejection"),
        table("schedule_items").delete().where(table("schedule_items").c.id==item["id"]),
        table("schedule_versions").update().where(table("schedule_versions").c.id==proposal["id"]).values(explanation="Rewritten rejection"),
        table("schedule_versions").update().where(table("schedule_versions").c.id==proposal["id"]).values(status="PUBLISHED"),
    ):
        with pytest.raises(IntegrityError),database.admin.begin() as connection:
            connection.execute(statement)


def test_failed_rescheduling_can_reference_its_failed_decision_execution(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        first = draft(connection,owner)
        publish(connection,first["id"])
        execution = insert(connection,"decision_executions",owner,execution_key=uuid4(),input_schedule_version_id=first["id"],algorithm_version="fixture-v1",input_snapshot={})
        decisions = table("decision_executions")
        connection.execute(decisions.update().where(decisions.c.id==execution["id"]).values(status="FAILED",finished_at=sa.func.now(),failure_code="INPUT_UNAVAILABLE"))
        run = insert(connection,"rescheduling_runs",owner,request_key=uuid4(),previous_version_id=first["id"],trigger_type="ENERGY_CHANGED",reason="Input unavailable during recomputation")
        runs = table("rescheduling_runs")
        connection.execute(runs.update().where(runs.c.id==run["id"]).values(status="FAILED",finished_at=sa.func.now(),decision_execution_id=execution["id"]))


def test_presented_run_cannot_be_rewound_retargeted_or_deleted(database, users):
    owner = users[0]
    with database.admin.begin() as connection:
        first = draft(connection,owner)
        publish(connection,first["id"])
        proposal = draft(connection,owner,version_number=2,parent_version_id=first["id"])
        replacement = draft(connection,owner,version_number=3,parent_version_id=first["id"])
        run = insert(connection,"rescheduling_runs",owner,request_key=uuid4(),previous_version_id=first["id"],trigger_type="MANUAL",reason="Presented proposal")
        runs = table("rescheduling_runs")
        connection.execute(runs.update().where(runs.c.id==run["id"]).values(status="PROPOSED",new_version_id=proposal["id"]))
    for statement in (
        runs.update().where(runs.c.id==run["id"]).values(status="PENDING"),
        runs.update().where(runs.c.id==run["id"]).values(new_version_id=replacement["id"]),
        runs.delete().where(runs.c.id==run["id"]),
    ):
        with pytest.raises(IntegrityError),database.admin.begin() as connection:
            connection.execute(statement)

