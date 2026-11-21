"""Pure helpers that prepare database rows: carried placements, logical keys, omissions, windows."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID, uuid4

from app.modules.scheduler.algorithms.schedule_builder import ScheduleItem
from app.modules.scheduler.algorithms.task_allocator import UnscheduledTask


@dataclass(frozen=True)
class StoredItem:
    """What we know about an item that already exists in the previous version."""
    kind: str
    task_id: UUID | None
    event_id: UUID | None
    event_occurrence_date: date | None
    start_at: datetime
    end_at: datetime
    is_locked: bool
    logical_key: UUID


@dataclass(frozen=True)
class ItemRow:
    """One schedule_items row to insert."""
    kind: str
    task_id: UUID | None
    event_id: UUID | None
    event_occurrence_date: date | None
    start_at: datetime
    end_at: datetime
    is_locked: bool
    logical_key: UUID


def _signature(kind, source_id, occurrence_date, start, end):
    return (kind, source_id, occurrence_date, start, end)


def select_carried_items(parent_items, flexible_tasks_by_id, completed_task_ids, window_start, window_end):
    """Previous placements of FLEXIBLE tasks that must be copied unchanged: locked or completed ones."""
    carried = []
    for item in parent_items:
        if item.kind != "TASK" or item.task_id is None:
            continue
        task = flexible_tasks_by_id.get(item.task_id)
        if task is None:
            continue
        if not (item.end_at > window_start and item.start_at < window_end):
            continue
        if not (item.is_locked or item.task_id in completed_task_ids):
            continue
        if task.deadline is not None and item.end_at > task.deadline:
            continue  # the source changed; the database does not require keeping this one
        carried.append(ScheduleItem("TASK", item.task_id, task.title, item.start_at, item.end_at, False,
                                    deadline=task.deadline, is_locked=item.is_locked,
                                    logical_key=item.logical_key))
    return carried


def to_item_rows(items, parent_items):
    """Convert schedule items to rows, reusing the logical key of an identical previous item."""
    known = {_signature(p.kind, p.task_id or p.event_id, p.event_occurrence_date, p.start_at, p.end_at): p.logical_key
             for p in parent_items}
    rows, used = [], set()
    for item in items:
        key = item.logical_key or known.get(
            _signature(item.kind, item.source_id, item.occurrence_date, item.start, item.end))
        if key is None or key in used:
            key = uuid4()
        used.add(key)
        rows.append(ItemRow(item.kind,
                            item.source_id if item.kind == "TASK" else None,
                            item.source_id if item.kind == "EVENT" else None,
                            item.occurrence_date, item.start, item.end, item.is_locked, key))
    return rows


def partial_allocation_omissions(items, estimates, omitted_task_ids):
    """The database requires an omission row for every flexible task placed for less than its estimate."""
    minutes: dict[UUID, int] = {}
    for item in items:
        if item.kind == "TASK" and not item.is_fixed:
            minutes[item.source_id] = minutes.get(item.source_id, 0) + int((item.end - item.start).total_seconds() // 60)
    return [UnscheduledTask(task_id, "PARTIAL_ALLOCATION",
                            "Only part of the task's estimated time is placed in this schedule")
            for task_id, placed in minutes.items()
            if task_id in estimates and placed < estimates[task_id] and task_id not in omitted_task_ids]


def widen_window(window_start, window_end, items):
    """Every placement must fit inside its version's window, so grow it to cover all items."""
    return (min([window_start] + [item.start for item in items]),
            max([window_end] + [item.end for item in items]))