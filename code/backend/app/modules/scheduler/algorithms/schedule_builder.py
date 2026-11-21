"""Schedule builder and analyzer: combine fixed commitments and allocated tasks into one schedule."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.modules.scheduler.algorithms.availability_calculator import calculate_available_slots, expand_availability
from app.modules.scheduler.algorithms.busy_calculator import calculate_busy_blocks
from app.modules.scheduler.algorithms.schedule_validator import PlannedItem, ValidationIssue, is_valid, validate_schedule
from app.modules.scheduler.algorithms.task_allocator import TaskToSchedule, UnscheduledTask, allocate_tasks_to_slots
from app.modules.scheduler.algorithms.time_intervals import Interval


@dataclass(frozen=True)
class ScheduleItem:
    kind: str  # "EVENT" or "TASK"
    source_id: UUID
    title: str
    start: datetime
    end: datetime
    is_fixed: bool
    session_number: int | None = None
    deadline: datetime | None = None
    occurrence_date: date | None = None  # weekly event occurrences only
    is_locked: bool = False
    logical_key: UUID | None = None  # identity kept across schedule versions


@dataclass(frozen=True)
class ScheduleStatistics:
    scheduled_tasks: int
    unscheduled_tasks: int
    total_scheduled_minutes: int  # minutes of FLEXIBLE tasks placed by the allocator
    available_minutes: int  # free time before placing flexible tasks
    unused_minutes: int  # free time left after placing them
    deadline_violations: int
    conflicts_count: int
    completion_ratio: float  # flexible tasks scheduled / flexible tasks requested


@dataclass
class ScheduleResult:
    range_start: datetime
    range_end: datetime
    items: list[ScheduleItem]
    unscheduled: list[UnscheduledTask]
    issues: list[ValidationIssue]
    available_slots: list[Interval]
    remaining_slots: list[Interval]
    statistics: ScheduleStatistics

    @property
    def is_publishable(self) -> bool:
        return is_valid(self.issues)


def _minutes(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def filter_schedulable_tasks(tasks, excluded_task_ids=frozenset()):
    """Split tasks into (flexible tasks to place, fixed tasks that are commitments).

    Excluded (completed/skipped) tasks are dropped from the flexible list only: a FIXED task
    always stays in the schedule because the database requires its placement to be preserved.
    """
    flexible, fixed = [], []
    for task in tasks:
        if task.task_type == "FIXED":
            fixed.append(task)
        elif task.task_id not in excluded_task_ids:
            flexible.append(task)
    return flexible, fixed


def analyze_schedule(items, issues, unscheduled_count, flexible_total, free_before, free_after) -> ScheduleStatistics:
    flexible_items = [item for item in items if item.kind == "TASK" and not item.is_fixed]
    flexible_scheduled = len({item.source_id for item in flexible_items})
    return ScheduleStatistics(
        scheduled_tasks=len({item.source_id for item in items if item.kind == "TASK"}),
        unscheduled_tasks=unscheduled_count,
        total_scheduled_minutes=sum(_minutes(item.start, item.end) for item in flexible_items),
        available_minutes=sum(slot.minutes for slot in free_before),
        unused_minutes=sum(slot.minutes for slot in free_after),
        deadline_violations=sum(1 for issue in issues if issue.code == "DEADLINE_EXCEEDED"),
        conflicts_count=sum(1 for issue in issues if issue.code == "TIME_OVERLAP"),
        completion_ratio=round(flexible_scheduled / flexible_total, 4) if flexible_total else 1.0,
    )


def build_schedule(*, windows, events, tasks, range_start: datetime, range_end: datetime, now: datetime,
                   requirements=None, excluded_task_ids=frozenset(), busy_range=None,
                   carried_items=()) -> ScheduleResult:
    """requirements maps task_id -> object with can_split and minimum_session_minutes.

    busy_range: wider (start, end) used to collect events/fixed tasks; free time is still only
    taken from [range_start, range_end]. carried_items: placements copied unchanged from the
    previous version (locked or completed); their tasks are not planned again.
    """
    requirements = requirements or {}
    busy_start, busy_end = busy_range or (range_start, range_end)
    flexible, fixed = filter_schedulable_tasks(tasks, excluded_task_ids)
    carried_ids = {item.source_id for item in carried_items}
    flexible = [task for task in flexible if task.task_id not in carried_ids]
    titles = {event.event_id: event.title for event in events} | {task.task_id: task.title for task in tasks}
    deadlines = {task.task_id: task.deadline for task in tasks}

    busy = calculate_busy_blocks(events, fixed, busy_start, busy_end)
    start = max(range_start, now)  # never schedule in the past
    availability = expand_availability(windows, start, range_end)
    blocked = [block.interval for block in busy] + [Interval(item.start, item.end) for item in carried_items]
    free = calculate_available_slots(windows, blocked, start, range_end)

    to_place = []
    for task in flexible:
        requirement = requirements.get(task.task_id)
        to_place.append(TaskToSchedule(
            task.task_id, task.title, task.estimated_duration_minutes, task.priority, task.deadline,
            can_split=bool(requirement and requirement.can_split),
            min_session_minutes=requirement.minimum_session_minutes if requirement else None))
    allocation = allocate_tasks_to_slots(to_place, free)

    items = [ScheduleItem(block.kind, block.source_id, titles[block.source_id], block.interval.start,
                          block.interval.end, True,
                          deadline=deadlines.get(block.source_id) if block.kind == "TASK" else None,
                          occurrence_date=block.occurrence_date, is_locked=True)
             for block in busy]
    items += list(carried_items)
    items += [ScheduleItem("TASK", p.task_id, titles[p.task_id], p.start, p.end, False,
                           session_number=p.session_number, deadline=deadlines[p.task_id])
              for p in allocation.placements]
    items.sort(key=lambda item: (item.start, item.end))

    planned = [PlannedItem(item.kind, item.source_id, item.start, item.end, item.deadline, item.is_fixed)
               for item in items]
    issues = validate_schedule(planned, availability)
    statistics = analyze_schedule(items, issues, len(allocation.unscheduled), len(flexible),
                                  free, allocation.remaining_slots)
    return ScheduleResult(range_start, range_end, items, allocation.unscheduled, issues,
                          free, allocation.remaining_slots, statistics)