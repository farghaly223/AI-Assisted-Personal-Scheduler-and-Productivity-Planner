from datetime import datetime, time, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.modules.scheduler.algorithms.schedule_builder import build_schedule


def at(hour, minute=0):
    return datetime(2026, 11, 23, hour, minute, tzinfo=timezone.utc)  # a Monday


DAY = (at(0), at(0).replace(day=24))
EARLY = at(0)


def window(start=9, end=17):
    return SimpleNamespace(weekday=0, start_time=time(start), end_time=time(end), timezone="UTC",
                           valid_from=None, valid_until=None)


def once(start, end, title="Lecture"):
    return SimpleNamespace(event_id=uuid4(), title=title, recurrence_type="ONCE", start_at=start, end_at=end,
                           recurrence_first_date=None, recurrence_until_date=None, recurrence_start_time=None,
                           recurrence_end_time=None, recurrence_end_day_offset=None, recurrence_timezone=None)


def flexible(minutes, deadline=None, priority=3, title="Study"):
    return SimpleNamespace(task_id=uuid4(), title=title, task_type="FLEXIBLE", estimated_duration_minutes=minutes,
                           priority=priority, deadline=deadline, fixed_start_at=None, fixed_end_at=None)


def fixed(start, end, title="Meeting"):
    return SimpleNamespace(task_id=uuid4(), title=title, task_type="FIXED", estimated_duration_minutes=60,
                           priority=3, deadline=None, fixed_start_at=start, fixed_end_at=end)


def build(**kwargs):
    kwargs.setdefault("windows", [window()])
    kwargs.setdefault("events", [])
    kwargs.setdefault("tasks", [])
    kwargs.setdefault("now", EARLY)
    return build_schedule(range_start=DAY[0], range_end=DAY[1], **kwargs)


def test_task_is_placed_before_a_lecture_and_statistics_add_up():
    task = flexible(120)
    result = build(events=[once(at(11), at(13))], tasks=[task])
    assert [(i.kind, i.start, i.end) for i in result.items] == [("TASK", at(9), at(11)), ("EVENT", at(11), at(13))]
    assert result.issues == [] and result.is_publishable
    stats = result.statistics
    assert (stats.scheduled_tasks, stats.unscheduled_tasks, stats.total_scheduled_minutes) == (1, 0, 120)
    assert (stats.available_minutes, stats.unused_minutes, stats.completion_ratio) == (360, 240, 1.0)


def test_task_that_does_not_fit_is_reported_as_unscheduled():
    result = build(windows=[window(9, 12)], tasks=[flexible(120, deadline=at(12)), flexible(120, deadline=at(12))])
    assert len(result.unscheduled) == 1
    assert result.statistics.completion_ratio == 0.5
    assert result.statistics.unscheduled_tasks == 1


def test_excluded_tasks_are_ignored_completely():
    done = flexible(60)
    result = build(tasks=[done], excluded_task_ids={done.task_id})
    assert result.items == [] and result.unscheduled == []
    assert result.statistics.completion_ratio == 1.0


def test_nothing_is_scheduled_in_the_past():
    result = build(events=[once(at(11), at(13))], tasks=[flexible(60)], now=at(10))
    task_item = next(i for i in result.items if i.kind == "TASK")
    assert (task_item.start, task_item.end) == (at(10), at(11))


def test_overlapping_commitments_are_reported_and_block_publishing():
    result = build(events=[once(at(10), at(12))], tasks=[fixed(at(11), at(13))])
    assert result.statistics.conflicts_count == 1
    assert not result.is_publishable


def test_fixed_task_appears_in_the_schedule_with_its_title():
    result = build(tasks=[fixed(at(14), at(15), title="Team meeting")])
    assert [(i.title, i.is_fixed) for i in result.items] == [("Team meeting", True)]


def test_splittable_task_uses_its_requirements():
    task = flexible(90)
    requirements = {task.task_id: SimpleNamespace(can_split=True, minimum_session_minutes=30)}
    result = build(events=[once(at(10), at(11), "A"), once(at(12), at(17), "B")], tasks=[task], requirements=requirements)
    sessions = [(i.start, i.end, i.session_number) for i in result.items if i.kind == "TASK"]
    assert sessions == [(at(9), at(10), 1), (at(11), at(11, 30), 2)]