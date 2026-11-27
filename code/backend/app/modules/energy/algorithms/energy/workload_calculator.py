"""Workload calculation algorithm for energy scoring."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from app.modules.energy.algorithms.energy.config import (
    DEFAULT_ENERGY_CONFIG,
    EnergyConfig,
)


@dataclass(frozen=True)
class TaskLoad:
    """Individual task cognitive/effort load.

    Attributes:
        duration_minutes: Task planned duration in minutes (strict int 1..1440).
        priority: Task priority rating (strict int 1..5).
        completed: Whether task is completed (bool), or None if tracking is unavailable.
    """

    duration_minutes: int
    priority: int
    completed: bool | None = None

    def __post_init__(self) -> None:
        if isinstance(self.duration_minutes, bool) or not isinstance(self.duration_minutes, int):
            raise ValueError(
                f"Invalid duration_minutes type: {type(self.duration_minutes).__name__}. Expected int."
            )
        if self.duration_minutes < 1 or self.duration_minutes > 1440:
            raise ValueError(
                f"duration_minutes {self.duration_minutes} out of valid range 1..1440."
            )

        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError(
                f"Invalid priority type: {type(self.priority).__name__}. Expected int."
            )
        if self.priority < 1 or self.priority > 5:
            raise ValueError(
                f"priority {self.priority} out of valid range 1..5."
            )

        if self.completed is not None and not isinstance(self.completed, bool):
            raise ValueError(
                f"Invalid completed type: {type(self.completed).__name__}. Expected bool or None."
            )


@dataclass(frozen=True)
class WorkloadSummary:
    """Aggregated daily workload summary.

    Attributes:
        total_task_minutes: Sum of duration_minutes of all planned tasks.
        completed_task_minutes: Sum of completed task minutes, or None if any task has completed=None.
        fixed_event_minutes: Total minutes of merged fixed events clipped to the local day bounds.
        high_priority_tasks: Count of tasks with priority >= config.high_priority_min.
        load_ratio: (total_task_minutes + fixed_event_minutes) / available_minutes, capped at max_load_ratio.
        level: Categorical workload level 'LOW', 'MEDIUM', 'HIGH', or None if has_data is False.
        has_data: False when tasks and events are both empty, indicating absence of workload tracking.
        truncated: True when task or event rows exceeded read limits.
    """

    total_task_minutes: int
    completed_task_minutes: int | None
    fixed_event_minutes: int
    high_priority_tasks: int
    load_ratio: float | None
    level: str | None
    has_data: bool
    truncated: bool = False


def calculate_workload(
    tasks: Sequence[TaskLoad],
    events: Sequence[tuple[datetime, datetime]],
    day_start: datetime,
    day_end: datetime,
    config: EnergyConfig = DEFAULT_ENERGY_CONFIG,
    *,
    available_minutes: int | None = None,
    extra_high_priority_tasks: int = 0,
    truncated: bool = False,
) -> WorkloadSummary:
    """Calculate aggregated workload metrics for a local day.

    Args:
        tasks: Sequence of TaskLoad items.
        events: Sequence of (start, end) timezone-aware datetimes.
        day_start: Timezone-aware start of the local day.
        day_end: Timezone-aware end of the local day.
        config: EnergyConfig instance with thresholds and parameters.
        available_minutes: Optional waking minutes override (1..1440); falls back to config.available_minutes.
        extra_high_priority_tasks: Additional high-priority count (e.g. FIXED tasks merged into intervals).

    Returns:
        WorkloadSummary with total/completed minutes, load_ratio, level, and has_data flag.

    Raises:
        ValueError: If dates are naive, day_end <= day_start, event end <= start,
            available_minutes/extra_high_priority_tasks invalid, or task validation fails.
    """
    if available_minutes is not None:
        if isinstance(available_minutes, bool) or not isinstance(available_minutes, int):
            raise ValueError(
                f"Invalid available_minutes type: {type(available_minutes).__name__}. Expected int."
            )
        if available_minutes < 1 or available_minutes > 1440:
            raise ValueError(
                f"available_minutes {available_minutes} out of valid range 1..1440."
            )
        eff_available_minutes = available_minutes
    else:
        eff_available_minutes = config.available_minutes

    if isinstance(extra_high_priority_tasks, bool) or not isinstance(extra_high_priority_tasks, int):
        raise ValueError(
            f"Invalid extra_high_priority_tasks type: {type(extra_high_priority_tasks).__name__}. Expected int."
        )
    if extra_high_priority_tasks < 0:
        raise ValueError(
            f"extra_high_priority_tasks {extra_high_priority_tasks} must be non-negative."
        )

    if not isinstance(day_start, datetime) or not isinstance(day_end, datetime):
        raise ValueError("day_start and day_end must be datetime instances")

    if day_start.tzinfo is None or day_start.tzinfo.utcoffset(day_start) is None:
        raise ValueError("day_start must be timezone-aware")
    if day_end.tzinfo is None or day_end.tzinfo.utcoffset(day_end) is None:
        raise ValueError("day_end must be timezone-aware")

    if day_end <= day_start:
        raise ValueError(f"day_end ({day_end}) must be after day_start ({day_start})")

    # Validate events
    for idx, event in enumerate(events):
        if not isinstance(event, (tuple, list)) or len(event) != 2:
            raise ValueError(f"Event at index {idx} must be a (start, end) pair")
        ev_start, ev_end = event
        if not isinstance(ev_start, datetime) or not isinstance(ev_end, datetime):
            raise ValueError(f"Event at index {idx} contains non-datetime elements")
        if ev_start.tzinfo is None or ev_start.tzinfo.utcoffset(ev_start) is None:
            raise ValueError(f"Event start at index {idx} must be timezone-aware")
        if ev_end.tzinfo is None or ev_end.tzinfo.utcoffset(ev_end) is None:
            raise ValueError(f"Event end at index {idx} must be timezone-aware")
        if ev_end <= ev_start:
            raise ValueError(f"Event end ({ev_end}) must be after start ({ev_start}) at index {idx}")

    # Handle truncation: treat workload as unavailable
    if truncated or getattr(tasks, "truncated", False) or getattr(events, "truncated", False):
        return WorkloadSummary(
            total_task_minutes=0,
            completed_task_minutes=0,
            fixed_event_minutes=0,
            high_priority_tasks=0,
            load_ratio=None,
            level=None,
            has_data=False,
            truncated=True,
        )

    # Empty inputs represent absence of data, NOT a light day
    if len(tasks) == 0 and len(events) == 0 and extra_high_priority_tasks == 0:
        return WorkloadSummary(
            total_task_minutes=0,
            completed_task_minutes=0,
            fixed_event_minutes=0,
            high_priority_tasks=0,
            load_ratio=None,
            level=None,
            has_data=False,
            truncated=False,
        )

    # 1. Process tasks
    total_task_minutes = 0
    completed_task_minutes: int | None = 0
    high_priority_tasks = extra_high_priority_tasks
    has_unknown_completion = False

    for task in tasks:
        if not isinstance(task, TaskLoad):
            raise ValueError(f"Expected TaskLoad instance, got {type(task).__name__}")
        total_task_minutes += task.duration_minutes
        if task.priority >= config.high_priority_min:
            high_priority_tasks += 1

        if task.completed is None:
            has_unknown_completion = True
        elif not has_unknown_completion and task.completed is True:
            completed_task_minutes += task.duration_minutes

    if has_unknown_completion:
        completed_task_minutes = None

    # 2. Process events: clip to [day_start, day_end) and merge interval union
    clipped_intervals: list[tuple[datetime, datetime]] = []
    for ev_start, ev_end in events:
        c_start = max(ev_start, day_start)
        c_end = min(ev_end, day_end)
        if c_end > c_start:
            clipped_intervals.append((c_start, c_end))

    fixed_event_minutes = 0
    if clipped_intervals:
        clipped_intervals.sort(key=lambda iv: (iv[0], iv[1]))
        merged: list[tuple[datetime, datetime]] = []
        for interval in clipped_intervals:
            if not merged:
                merged.append(interval)
            else:
                prev_start, prev_end = merged[-1]
                curr_start, curr_end = interval
                if curr_start <= prev_end:
                    merged[-1] = (prev_start, max(prev_end, curr_end))
                else:
                    merged.append(interval)

        for s, e in merged:
            fixed_event_minutes += int(round((e - s).total_seconds() / 60.0))

    # 3. Calculate load ratio and categorical level
    total_busy_minutes = Decimal(total_task_minutes + fixed_event_minutes)
    raw_ratio = total_busy_minutes / Decimal(eff_available_minutes)
    capped_ratio = min(Decimal(str(config.max_load_ratio)), raw_ratio)
    load_ratio_float = float(capped_ratio.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

    if capped_ratio < Decimal(str(config.workload_low_threshold)):
        level = "LOW"
    elif capped_ratio < Decimal(str(config.workload_medium_threshold)):
        level = "MEDIUM"
    else:
        level = "HIGH"

    return WorkloadSummary(
        total_task_minutes=total_task_minutes,
        completed_task_minutes=completed_task_minutes,
        fixed_event_minutes=fixed_event_minutes,
        high_priority_tasks=high_priority_tasks,
        load_ratio=load_ratio_float,
        level=level,
        has_data=True,
    )
