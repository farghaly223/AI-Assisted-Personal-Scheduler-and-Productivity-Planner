"""Task allocator: greedy earliest-deadline-first placement of FLEXIBLE tasks into free slots."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from app.modules.scheduler.algorithms.time_intervals import Interval, subtract_intervals

# Assumption to confirm with the database owner: tasks.priority is 1..5.
# True -> 5 is the most important; False -> 1 is the most important.
HIGHER_PRIORITY_NUMBER_IS_MORE_IMPORTANT = True


@dataclass(frozen=True)
class TaskToSchedule:
    task_id: UUID
    title: str
    duration_minutes: int
    priority: int
    deadline: datetime | None = None
    can_split: bool = False
    min_session_minutes: int | None = None


@dataclass(frozen=True)
class Placement:
    task_id: UUID
    start: datetime
    end: datetime
    session_number: int


@dataclass(frozen=True)
class UnscheduledTask:
    task_id: UUID
    reason_code: str
    explanation: str


@dataclass
class AllocationResult:
    placements: list[Placement] = field(default_factory=list)
    unscheduled: list[UnscheduledTask] = field(default_factory=list)
    remaining_slots: list[Interval] = field(default_factory=list)


def _order_key(task: TaskToSchedule):
    priority = -task.priority if HIGHER_PRIORITY_NUMBER_IS_MORE_IMPORTANT else task.priority
    return (task.deadline is None,
            task.deadline.timestamp() if task.deadline else 0,
            priority, -task.duration_minutes, str(task.task_id))


def _usable_windows(free: list[Interval], deadline: datetime | None) -> list[Interval]:
    """Free slots clipped so nothing can end after the deadline."""
    windows = []
    for slot in free:
        end = min(slot.end, deadline) if deadline is not None else slot.end
        if end > slot.start:
            windows.append(Interval(slot.start, end))
    return windows


def _plan_whole(windows: list[Interval], duration: int):
    for window in windows:
        if window.minutes >= duration:
            return [(window.start, window.start + timedelta(minutes=duration))]
    return None


def _plan_split(windows: list[Interval], duration: int, min_session: int | None):
    minimum = min_session or 1
    remaining = duration
    chosen = []
    for window in windows:
        if remaining == 0:
            break
        chunk = min(remaining, window.minutes)
        if chunk < remaining:
            left = remaining - chunk
            if left < minimum:
                chunk = remaining - minimum  # keep the last session long enough
            if chunk < minimum:
                continue  # this window is too small to be a valid session
        chosen.append((window.start, window.start + timedelta(minutes=chunk)))
        remaining -= chunk
    return chosen if remaining == 0 else None


def allocate_tasks_to_slots(tasks: list[TaskToSchedule], free_slots: list[Interval]) -> AllocationResult:
    result = AllocationResult()
    free = sorted(free_slots, key=lambda slot: slot.start)
    for task in sorted(tasks, key=_order_key):
        windows = _usable_windows(free, task.deadline)
        if not windows:
            result.unscheduled.append(UnscheduledTask(
                task.task_id, "NO_AVAILABLE_TIME_BEFORE_DEADLINE",
                "There is no free time before the task's deadline"))
            continue
        if task.can_split:
            plan = _plan_split(windows, task.duration_minutes, task.min_session_minutes)
            reason = ("NOT_ENOUGH_TIME_BEFORE_DEADLINE", "The free time before the deadline is not enough for the whole task")
        else:
            plan = _plan_whole(windows, task.duration_minutes)
            reason = ("NO_SLOT_LONG_ENOUGH", "No single free slot before the deadline is long enough for this task")
        if plan is None:
            result.unscheduled.append(UnscheduledTask(task.task_id, *reason))
            continue
        for number, (start, end) in enumerate(plan, start=1):
            result.placements.append(Placement(task.task_id, start, end, number))
        free = subtract_intervals(free, [Interval(start, end) for start, end in plan])
    result.placements.sort(key=lambda placement: placement.start)
    result.remaining_slots = free
    return result