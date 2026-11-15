from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.modules.scheduler.schemas import EventCreate, TaskCreate, TaskUpdate
from app.modules.user.schemas import PreferencesUpdate


TASK = dict(title="Assignment", task_type="FLEXIBLE", estimated_duration_minutes=30,
            category="STUDY", priority=3)
ONCE = dict(title="Lecture", category="STUDY", start_at="2026-11-16T09:00:00Z",
            end_at="2026-11-16T10:00:00Z")
WEEKLY = dict(title="Work shift", category="WORK", recurrence_type="WEEKLY",
              recurrence_first_date="2026-11-16", recurrence_start_time="22:00:00",
              recurrence_end_time="06:00:00", recurrence_end_day_offset=1,
              recurrence_timezone="Africa/Cairo", weekday=1)


@pytest.mark.parametrize("change", [
    {"title": " \t"}, {"title": "x" * 501}, {"priority": 0}, {"priority": 6},
    {"priority": True}, {"estimated_duration_minutes": 0}, {"estimated_duration_minutes": 1.5},
    {"category": "UNKNOWN"}, {"task_type": "OTHER"}, {"deadline": "2026-11-16T09:00:00"},
    {"fixed_start_at": "2026-11-16T09:00:00Z"}, {"task_type": "FIXED"},
    {"user_id": "00000000-0000-0000-0000-000000000001"}, {"status": "UNSCHEDULED"},
])
def test_task_create_rejects_invalid_and_client_owned_fields(change):
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(TASK | change)


def test_valid_fixed_task_and_utc_conversion():
    task = TaskCreate.model_validate(TASK | dict(task_type="FIXED",
        fixed_start_at="2026-11-16T09:00:00+02:00", fixed_end_at="2026-11-16T10:00:00+02:00"))
    assert task.fixed_start_at == datetime(2026, 11, 16, 7, tzinfo=timezone.utc)


@pytest.mark.parametrize("field", ["title", "task_type", "category", "priority", "estimated_duration_minutes"])
def test_patch_cannot_explicitly_null_required_task_fields(field):
    with pytest.raises(ValidationError):
        TaskUpdate.model_validate({field: None})


@pytest.mark.parametrize("change", [
    {"end_at": "2026-11-16T09:00:00Z"}, {"start_at": "2026-11-16T09:00:00"},
    {"recurrence_timezone": "UTC"}, {"weekday": 1}, {"source": "MANUAL"},
    {"event_id": "00000000-0000-0000-0000-000000000001"},
])
def test_once_event_rejects_invalid_interval_or_weekly_fields(change):
    with pytest.raises(ValidationError):
        EventCreate.model_validate(ONCE | change)


@pytest.mark.parametrize("change", [
    {"weekday": 2}, {"weekday": 0}, {"weekday": True}, {"recurrence_timezone": "Imaginary/Nowhere"},
    {"recurrence_until_date": "2026-11-15"}, {"recurrence_end_day_offset": -1},
    {"recurrence_end_day_offset": 32768}, {"recurrence_end_day_offset": 0},
    {"recurrence_start_time": "22:00:00+02:00"}, {"recurrence_start_time": "22:00:00.5"},
    {"start_at": "2026-11-16T09:00:00Z"},
])
def test_weekly_event_rejects_invalid_shape_or_calendar_values(change):
    with pytest.raises(ValidationError):
        EventCreate.model_validate(WEEKLY | change)


@pytest.mark.parametrize("first", ["2026-03-08", "2026-11-01"])
def test_weekly_event_rejects_dst_gap_or_fold(first):
    with pytest.raises(ValidationError):
        EventCreate.model_validate(WEEKLY | dict(recurrence_first_date=first, weekday=7,
            recurrence_timezone="America/New_York", recurrence_start_time="01:30:00" if "11-01" in first else "02:30:00",
            recurrence_end_time="04:00:00", recurrence_end_day_offset=0))


def test_overnight_weekly_event_and_explicit_24_hour_interval():
    event = EventCreate.model_validate(WEEKLY)
    assert event.recurrence_end_day_offset == 1
    EventCreate.model_validate(WEEKLY | {"recurrence_end_time": "22:00:00"})


@pytest.mark.parametrize("change", [
    {"timezone": "Imaginary/Nowhere"}, {"timezone": None}, {"notifications_enabled": "true"},
    {"preferred_work_start": "09:00:00+02:00"}, {"user_id": "forged"},
])
def test_preferences_patch_rejects_invalid_fields(change):
    with pytest.raises(ValidationError):
        PreferencesUpdate.model_validate(change)


@pytest.mark.parametrize("endpoint", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-12:00"])
def test_instant_outside_utc_calendar_range_is_validation_error(endpoint):
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(TASK | {"deadline": endpoint})


@pytest.mark.parametrize("field", ["title", "context"])
def test_unpaired_unicode_surrogate_is_validation_error(field):
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(TASK | {field: "bad\ud800text"})
