from datetime import datetime, timezone
from uuid import uuid4

from app.modules.scheduler.algorithms.task_allocator import TaskToSchedule, allocate_tasks_to_slots
from app.modules.scheduler.algorithms.time_intervals import Interval


def at(hour, minute=0):
    return datetime(2026, 11, 23, hour, minute, tzinfo=timezone.utc)


def task(minutes, priority=3, deadline=None, can_split=False, min_session=None):
    return TaskToSchedule(uuid4(), "task", minutes, priority, deadline, can_split, min_session)


def spans(result):
    return [(p.start, p.end) for p in result.placements]


def test_task_goes_to_the_start_of_the_first_slot():
    result = allocate_tasks_to_slots([task(60)], [Interval(at(9), at(12))])
    assert spans(result) == [(at(9), at(10))]
    assert result.unscheduled == []
    assert result.remaining_slots == [Interval(at(10), at(12))]


def test_earlier_deadline_is_scheduled_first():
    late, early = task(60, deadline=at(12)), task(60, deadline=at(11))
    result = allocate_tasks_to_slots([late, early], [Interval(at(9), at(12))])
    order = {p.task_id: p.start for p in result.placements}
    assert order[early.task_id] == at(9) and order[late.task_id] == at(10)


def test_priority_breaks_deadline_ties():
    low, high = task(60, priority=2), task(60, priority=5)
    result = allocate_tasks_to_slots([low, high], [Interval(at(9), at(12))])
    order = {p.task_id: p.start for p in result.placements}
    assert order[high.task_id] == at(9) and order[low.task_id] == at(10)


def test_task_without_deadline_goes_last():
    free_task, urgent = task(60), task(60, deadline=at(12))
    result = allocate_tasks_to_slots([free_task, urgent], [Interval(at(9), at(12))])
    order = {p.task_id: p.start for p in result.placements}
    assert order[urgent.task_id] == at(9) and order[free_task.task_id] == at(10)


def test_unsplittable_task_skips_a_short_slot():
    result = allocate_tasks_to_slots([task(60)], [Interval(at(9), at(9, 30)), Interval(at(10), at(12))])
    assert spans(result) == [(at(10), at(11))]


def test_splittable_task_uses_two_slots():
    result = allocate_tasks_to_slots([task(90, can_split=True, min_session=30)],
                                     [Interval(at(9), at(10)), Interval(at(11), at(12))])
    assert spans(result) == [(at(9), at(10)), (at(11), at(11, 30))]
    assert [p.session_number for p in result.placements] == [1, 2]


def test_split_keeps_every_session_at_least_the_minimum():
    result = allocate_tasks_to_slots([task(80, can_split=True, min_session=30)],
                                     [Interval(at(9), at(10)), Interval(at(11), at(12))])
    assert spans(result) == [(at(9), at(9, 50)), (at(11), at(11, 30))]


def test_deadline_clips_the_slot():
    result = allocate_tasks_to_slots([task(90, deadline=at(10))], [Interval(at(9), at(17))])
    assert result.placements == []
    assert [u.reason_code for u in result.unscheduled] == ["NO_SLOT_LONG_ENOUGH"]


def test_no_time_before_deadline():
    result = allocate_tasks_to_slots([task(30, deadline=at(8))], [Interval(at(9), at(17))])
    assert [u.reason_code for u in result.unscheduled] == ["NO_AVAILABLE_TIME_BEFORE_DEADLINE"]


def test_failed_task_does_not_consume_time():
    big = task(120, deadline=at(12), can_split=True)
    small = task(30, deadline=at(18))
    result = allocate_tasks_to_slots([big, small], [Interval(at(9), at(10)), Interval(at(13), at(14))])
    assert [u.task_id for u in result.unscheduled] == [big.task_id]
    assert spans(result) == [(at(9), at(9, 30))]


def test_placements_never_overlap():
    tasks = [task(45, deadline=at(14)), task(60, deadline=at(14)), task(30, deadline=at(14), can_split=True)]
    result = allocate_tasks_to_slots(tasks, [Interval(at(9), at(14))])
    ordered = sorted(spans(result))
    assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(ordered, ordered[1:]))