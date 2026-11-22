"""Recovery planner algorithm: calculates planned recovery schedule blocks."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.modules.energy.algorithms.recovery.config import (
    DEFAULT_RECOVERY_CONFIG,
    RecoveryConfig,
)


@dataclass(frozen=True)
class StrategyParams:
    """User-defined focus and recovery strategy parameters.

    Attributes:
        work_minutes: Duration of each focus session in minutes.
        short_break_minutes: Duration of standard short breaks in minutes.
        long_break_minutes: Duration of extended long breaks in minutes.
        long_break_after_sessions: Number of focus sessions before each long break.
    """

    work_minutes: int = 25
    short_break_minutes: int = 5
    long_break_minutes: int = 20
    long_break_after_sessions: int = 4

    def __post_init__(self) -> None:
        for field_name, value in (
            ("work_minutes", self.work_minutes),
            ("short_break_minutes", self.short_break_minutes),
            ("long_break_minutes", self.long_break_minutes),
            ("long_break_after_sessions", self.long_break_after_sessions),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be a positive integer, got {type(value).__name__}")
            if value <= 0:
                raise ValueError(f"{field_name} must be strictly positive (> 0), got {value}")


@dataclass(frozen=True)
class RecoveryBlock:
    """A single scheduled block in the recovery timeline.

    Attributes:
        kind: 'FOCUS', 'SHORT_BREAK', or 'LONG_BREAK'.
        index: 1-based index of this session or break.
        start: UTC start datetime.
        end: UTC end datetime.
        duration_minutes: Block duration in minutes.
    """

    kind: str
    index: int
    start: datetime
    end: datetime
    duration_minutes: int


@dataclass(frozen=True)
class PlannedSchedule:
    """Complete planned alternating timeline of focus and recovery blocks."""

    strategy: StrategyParams
    start: datetime
    sessions_count: int
    blocks: tuple[RecoveryBlock, ...]
    total_focus_minutes: int
    total_planned_recovery_minutes: int


def calculate_planned_recovery(
    strategy: StrategyParams,
    start: datetime,
    sessions_count: int,
    config: RecoveryConfig = DEFAULT_RECOVERY_CONFIG,
) -> PlannedSchedule:
    """Calculate the deterministic planned timeline of focus sessions and breaks.

    Alternating timeline starting at `start`:
    focus 1, break, focus 2, break ... with the break after every
    `long_break_after_sessions`-th focus session being LONG_BREAK and others SHORT_BREAK.
    No break after the last focus session.

    Args:
        strategy: StrategyParams instance with positive durations.
        start: Timezone-aware starting datetime.
        sessions_count: Total focus sessions to plan (1 <= count <= 24).
        config: Optional RecoveryConfig.

    Returns:
        PlannedSchedule containing blocks in UTC and summary totals.

    Raises:
        ValueError: If start is naive, or sessions_count is not int or outside 1..24.
    """
    if not isinstance(strategy, StrategyParams):
        raise ValueError("strategy must be an instance of StrategyParams")

    if start.tzinfo is None or start.tzinfo.utcoffset(start) is None:
        raise ValueError("start datetime must be timezone-aware")

    if isinstance(sessions_count, bool) or not isinstance(sessions_count, int):
        raise ValueError(f"sessions_count must be an integer, got {type(sessions_count).__name__}")

    if sessions_count < 1 or sessions_count > 24:
        raise ValueError(f"sessions_count must be between 1 and 24, got {sessions_count}")

    start_utc = start.astimezone(timezone.utc)
    curr = start_utc
    blocks: list[RecoveryBlock] = []
    total_focus_minutes = 0
    total_planned_recovery_minutes = 0

    for i in range(1, sessions_count + 1):
        # 1. Focus session block
        focus_end = curr + timedelta(minutes=strategy.work_minutes)
        blocks.append(
            RecoveryBlock(
                kind="FOCUS",
                index=i,
                start=curr,
                end=focus_end,
                duration_minutes=strategy.work_minutes,
            )
        )
        total_focus_minutes += strategy.work_minutes
        curr = focus_end

        # 2. Break block (none after the last session)
        if i < sessions_count:
            is_long = (i % strategy.long_break_after_sessions == 0)
            break_kind = "LONG_BREAK" if is_long else "SHORT_BREAK"
            break_dur = strategy.long_break_minutes if is_long else strategy.short_break_minutes
            break_end = curr + timedelta(minutes=break_dur)
            blocks.append(
                RecoveryBlock(
                    kind=break_kind,
                    index=i,
                    start=curr,
                    end=break_end,
                    duration_minutes=break_dur,
                )
            )
            total_planned_recovery_minutes += break_dur
            curr = break_end

    return PlannedSchedule(
        strategy=strategy,
        start=start_utc,
        sessions_count=sessions_count,
        blocks=tuple(blocks),
        total_focus_minutes=total_focus_minutes,
        total_planned_recovery_minutes=total_planned_recovery_minutes,
    )
