from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.modules.scheduler.algorithms.persistence_planner import (
    StoredItem, partial_allocation_omissions, select_carried_items, to_item_rows, widen_window)
from app.modules.scheduler.algorithms.schedule_builder import ScheduleItem


def at(hour, minute=0):
    return datetime(2026, 11, 23, hour, minute, tzinfo=timezone.utc)


def stored(task_id, start, end, locked=False):
    return StoredItem("TASK", task_id, None, None, start, end, locked, uuid4())


def flex_task(deadline=None):
    return SimpleNamespace(title="Study", deadline=deadline)


WINDOW = (at(0), at(23))


def test_locked_item_is_carried():
    tid = uuid4()
    item = stored(tid, at(9), at(10), locked=True)
    carried = select_carried_items([item], {tid: flex_task()}, set(), *WINDOW)
    assert [(c.source_id, c.is_locked, c.logical_key) for c in carried] == [(tid, True, item.logical_key)]


def test_unlocked_unfinished_item_is_replanned_not_carried():
    tid = uuid4()
    assert select_carried_items([stored(tid, at(9), at(10))], {tid: flex_task()}, set(), *WINDOW) == []


def test_completed_item_is_carried_even_when_not_locked():
    tid = uuid4()
    carried = select_carried_items([stored(tid, at(9), at(10))], {tid: flex_task()}, {tid}, *WINDOW)
    assert len(carried) == 1 and carried[0].is_locked is False


def test_item_of_unknown_or_fixed_task_is_not_carried():
    tid = uuid4()
    assert select_carried_items([stored(tid, at(9), at(10), locked=True)], {}, set(), *WINDOW) == []


def test_item_past_a_new_deadline_is_dropped():
    tid = uuid4()
    item = stored(tid, at(9), at(11), locked=True)
    assert select_carried_items([item], {tid: flex_task(deadline=at(10))}, set(), *WINDOW) == []


def test_item_outside_the_window_is_not_carried():
    tid = uuid4()
    item = stored(tid, at(9), at(10), locked=True)
    assert select_carried_items([item], {tid: flex_task()}, set(), at(12), at(18)) == []


def test_identical_previous_item_keeps_its_logical_key():
    tid = uuid4()
    previous = stored(tid, at(9), at(10))
    same = ScheduleItem("TASK", tid, "Study", at(9), at(10), False)
    moved = ScheduleItem("TASK", tid, "Study", at(11), at(12), False)
    rows = to_item_rows([same, moved], [previous])
    assert rows[0].logical_key == previous.logical_key
    assert rows[1].logical_key not in (previous.logical_key, None)


def test_event_rows_carry_event_id_and_occurrence_date():
    eid = uuid4()
    item = ScheduleItem("EVENT", eid, "Lecture", at(9), at(10), True, occurrence_date=at(9).date(), is_locked=True)
    row = to_item_rows([item], [])[0]
    assert (row.kind, row.task_id, row.event_id, row.event_occurrence_date, row.is_locked) == \
        ("EVENT", None, eid, at(9).date(), True)


def test_carried_item_keeps_its_own_logical_key():
    tid = uuid4()
    previous = stored(tid, at(9), at(10), locked=True)
    carried = select_carried_items([previous], {tid: flex_task()}, set(), *WINDOW)
    assert to_item_rows(carried, [previous])[0].logical_key == previous.logical_key


def test_partial_allocation_needs_an_omission_row():
    tid, full = uuid4(), uuid4()
    items = [ScheduleItem("TASK", tid, "A", at(9), at(10), False), ScheduleItem("TASK", full, "B", at(10), at(11), False)]
    omissions = partial_allocation_omissions(items, {tid: 120, full: 60}, set())
    assert [(o.task_id, o.reason_code) for o in omissions] == [(tid, "PARTIAL_ALLOCATION")]


def test_already_omitted_task_gets_no_second_omission():
    tid = uuid4()
    items = [ScheduleItem("TASK", tid, "A", at(9), at(10), False)]
    assert partial_allocation_omissions(items, {tid: 120}, {tid}) == []


def test_window_grows_to_cover_items():
    items = [ScheduleItem("EVENT", uuid4(), "Night shift", at(22), datetime(2026, 11, 24, 6, tzinfo=timezone.utc), True)]
    assert widen_window(at(0), at(23), items) == (at(0), datetime(2026, 11, 24, 6, tzinfo=timezone.utc))