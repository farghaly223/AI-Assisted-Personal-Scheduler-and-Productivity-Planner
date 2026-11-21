from datetime import datetime, time, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.modules.scheduler.algorithms.schedule_builder import ScheduleItem, build_schedule


def at(hour, minute=0):
    return datetime(2026, 11, 23, hour, minute, tzinfo=timezone.utc)  # a Monday


NEXT_DAY = datetime(2026, 11, 24, tzinfo=timezone.utc)


def window():
    return SimpleNamespace(weekday=0, start_time=time(9), end_time=time(17), timezone="UTC",
                           valid_from=None, valid_until=None)


def flexible(minutes):
    return SimpleNamespace(task_id=uuid4(), title="Study", task_type="FLEXIBLE", estimated_duration_minutes=minutes,
                           priority=3, deadline=None, fixed_start_at=None, fixed_end_at=None)


def fixed(start, end):
    return SimpleNamespace(task_id=uuid4(), title="Meeting", task_type="FIXED", estimated_duration_minutes=60,
                           priority=3, deadline=None, fixed_start_at=start, fixed_end_at=end)


def once(start, end):
    return SimpleNamespace(event_id=uuid4(), title="Lecture", recurrence_type="ONCE", start_at=start, end_at=end,
                           recurrence_first_date=None, recurrence_until_date=None, recurrence_start_time=None,
                           recurrence_end_time=None, recurrence_end_day_offset=None, recurrence_timezone=None)


def build(**kwargs):
    kwargs.setdefault("windows", [window()])
    kwargs.setdefault("events", [])
    kwargs.setdefault("tasks", [])
    return build_schedule(range_start=at(0), range_end=NEXT_DAY, now=at(0), **kwargs)


def test_carried_item_blocks_time_and_its_task_is_not_planned_again():
    task, other = flexible(60), flexible(60)
    carried = ScheduleItem("TASK", task.task_id, task.title, at(9), at(10), False, is_locked=True)
    result = build(tasks=[task, other], carried_items=[carried])
    spans = [(i.source_id, i.start, i.end) for i in result.items]
    assert (task.task_id, at(9), at(10)) in spans
    assert (other.task_id, at(10), at(11)) in spans
    assert len([s for s in spans if s[0] == task.task_id]) == 1


def test_completed_fixed_task_stays_in_the_schedule():
    meeting = fixed(at(14), at(15))
    result = build(tasks=[meeting], excluded_task_ids={meeting.task_id})
    assert [i.source_id for i in result.items] == [meeting.task_id]


def test_fixed_items_and_events_are_locked():
    result = build(events=[once(at(10), at(11))], tasks=[fixed(at(14), at(15))])
    assert all(item.is_locked for item in result.items)


def test_busy_range_collects_commitments_beyond_the_planning_range():
    late = once(datetime(2026, 11, 24, 1, tzinfo=timezone.utc), datetime(2026, 11, 24, 2, tzinfo=timezone.utc))
    assert build(events=[late]).items == []
    wider = build(events=[late], busy_range=(at(0), datetime(2026, 11, 24, 3, tzinfo=timezone.utc)))
    assert [i.source_id for i in wider.items] == [late.event_id]