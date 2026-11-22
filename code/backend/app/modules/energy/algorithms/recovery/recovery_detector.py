"""Recovery detector algorithm: analyzes focus sessions to observe actual recovery."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from app.modules.energy.algorithms.recovery.config import (
    DEFAULT_RECOVERY_CONFIG,
    RecoveryConfig,
)
from app.modules.energy.algorithms.recovery.recovery_planner import PlannedSchedule


@dataclass(frozen=True)
class ObservedBreak:
    """An observed candidate recovery break between completed focus sessions.

    Attributes:
        index: 1-based index among all observed breaks in the day.
        block_break_index: 1-based break index within the current work block.
        start: UTC start datetime of the break.
        end: UTC end datetime of the break.
        raw_gap_minutes: Unadjusted gap duration in minutes.
        credited_recovery_minutes: Recovery duration after deducting non-rest overlaps.
        capped_recovery_minutes: Credited recovery capped at planned duration (min(credited, planned)).
        planned_duration_minutes: Planned break duration used as baseline.
        classification: One of 'MISSED', 'SHORTENED', 'TAKEN', 'EXTENDED'.
    """

    index: int
    block_break_index: int
    start: datetime
    end: datetime
    raw_gap_minutes: float
    credited_recovery_minutes: float
    capped_recovery_minutes: float
    planned_duration_minutes: int
    classification: str


@dataclass(frozen=True)
class RecoveryDetection:
    """Aggregated recovery detection results for a planned schedule.

    Attributes:
        breaks: Observed breaks between consecutive completed sessions in valid work blocks.
        observed_recovery_minutes: Total uncapped credited recovery minutes.
        capped_recovery_minutes: Total credited recovery minutes capped at planned durations.
        planned_recovery_minutes: Total planned recovery minutes for the observed breaks.
        missed_breaks: Count of breaks classified as MISSED.
        shortened_breaks: Count of breaks classified as SHORTENED.
        extended_breaks: Count of breaks classified as EXTENDED.
        taken_breaks: Count of breaks classified as TAKEN.
        completion_ratio: capped_recovery / planned_recovery, or None when data_status is UNKNOWN.
        data_status: 'UNKNOWN', 'PARTIAL', or 'OBSERVED'.
        consecutive_focus_sessions_without_long_break: Count of completed sessions since last long break.
        long_break_after_sessions: Target threshold for consecutive sessions from strategy.
    """

    breaks: tuple[ObservedBreak, ...]
    observed_recovery_minutes: float
    capped_recovery_minutes: float
    planned_recovery_minutes: float
    missed_breaks: int
    shortened_breaks: int
    extended_breaks: int
    taken_breaks: int
    completion_ratio: float | None
    data_status: str
    consecutive_focus_sessions_without_long_break: int
    long_break_after_sessions: int


VALID_FOCUS_STATUSES = frozenset({"COMPLETED", "CANCELLED", "ABANDONED", "IN_PROGRESS"})


def _validate_and_normalize_interval(
    start: datetime,
    end: datetime,
    name: str = "interval",
) -> tuple[datetime, datetime]:
    """Validate a timezone-aware interval where end > start and normalize to UTC."""
    if start.tzinfo is None or start.tzinfo.utcoffset(start) is None:
        raise ValueError(f"{name} start must be timezone-aware, got naive {start}")
    if end.tzinfo is None or end.tzinfo.utcoffset(end) is None:
        raise ValueError(f"{name} end must be timezone-aware, got naive {end}")
    if end <= start:
        raise ValueError(f"{name} end ({end}) must be strictly greater than start ({start})")
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _union_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Merge overlapping or touching datetime intervals into disjoint sorted intervals."""
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged: list[tuple[datetime, datetime]] = [sorted_intervals[0]]
    for s, e in sorted_intervals[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))
    return merged


def detect_actual_recovery(
    planned: PlannedSchedule,
    focus_sessions: Sequence[tuple[datetime, datetime | None, str]],
    fixed_events: Sequence[tuple[datetime, datetime]] = (),
    app_activity: Sequence[tuple[datetime, datetime]] | None = None,
    config: RecoveryConfig = DEFAULT_RECOVERY_CONFIG,
) -> RecoveryDetection:
    """Detect actual recovery breaks and compute observed recovery metrics.

    Data status definitions:
    - UNKNOWN: When there are 0 or 1 completed focus sessions (nothing observable).
    - PARTIAL: When there is observable data (>= 2 completed sessions) but part of the span is
      unobserved (an inter-session gap > max_gap_same_block, an IN_PROGRESS/ABANDONED session
      inside the observed span, or fewer completed sessions than planned). Missing data is never
      treated as rest and never as missed breaks.
    - OBSERVED: All planned sessions are observed without interruptions or unobserved gaps.

    Break-index rule:
    - The break index restarts with each new work block (a gap > max_gap_same_block ends the block).
    - The gap is compared with the planned break at that block index, falling back to the
      strategy's short break when out of range.

    Args:
        planned: PlannedSchedule baseline.
        focus_sessions: Sequence of (start, end, status) focus sessions. Input is never mutated.
        fixed_events: Sequence of (start, end) occupied events (not rest).
        app_activity: Optional sequence of (start, end) app active intervals (not rest).
        config: RecoveryConfig heuristics and thresholds.

    Returns:
        RecoveryDetection summary.

    Raises:
        ValueError: If timestamps are naive or end <= start for completed intervals.
    """
    # 1. Validate fixed_events without mutating
    normalized_fixed: list[tuple[datetime, datetime]] = []
    for s, e in fixed_events:
        normalized_fixed.append(_validate_and_normalize_interval(s, e, "fixed_event"))

    # 2. Validate app_activity without mutating
    normalized_activity: list[tuple[datetime, datetime]] | None = None
    if app_activity is not None:
        normalized_activity = []
        for s, e in app_activity:
            normalized_activity.append(_validate_and_normalize_interval(s, e, "app_activity"))

    # 3. Validate focus sessions without mutating
    raw_completed: list[tuple[datetime, datetime]] = []
    unobserved_sessions_in_input: list[tuple[datetime, datetime | None, str]] = []

    for s, e, status in focus_sessions:
        if status not in VALID_FOCUS_STATUSES:
            raise ValueError(f"Invalid focus session status: {status!r}")
        if s.tzinfo is None or s.tzinfo.utcoffset(s) is None:
            raise ValueError(f"focus_session start must be timezone-aware, got {s}")

        s_utc = s.astimezone(timezone.utc)
        if status == "IN_PROGRESS":
            e_utc = e.astimezone(timezone.utc) if e is not None else None
            unobserved_sessions_in_input.append((s_utc, e_utc, status))
        else:
            if e is None:
                raise ValueError(f"focus_session with status {status} must have an end time")
            if e.tzinfo is None or e.tzinfo.utcoffset(e) is None:
                raise ValueError(f"focus_session end must be timezone-aware, got {e}")
            e_utc = e.astimezone(timezone.utc)
            if e_utc <= s_utc:
                raise ValueError(f"focus_session end ({e}) must be strictly greater than start ({s})")

            if status == "COMPLETED":
                raw_completed.append((s_utc, e_utc))
            elif status == "ABANDONED":
                unobserved_sessions_in_input.append((s_utc, e_utc, status))
            # CANCELLED sessions are ignored entirely

    # 4. Merge overlapping/touching COMPLETED sessions into union (DB has no exclusion constraint)
    merged_completed = _union_intervals(raw_completed)

    # 5. Handle UNKNOWN data status (0 or 1 completed session -> nothing observable)
    if len(merged_completed) < 2:
        return RecoveryDetection(
            breaks=(),
            observed_recovery_minutes=0.0,
            capped_recovery_minutes=0.0,
            planned_recovery_minutes=0.0,
            missed_breaks=0,
            shortened_breaks=0,
            extended_breaks=0,
            taken_breaks=0,
            completion_ratio=None,
            data_status="UNKNOWN",
            consecutive_focus_sessions_without_long_break=len(merged_completed),
            long_break_after_sessions=planned.strategy.long_break_after_sessions,
        )

    # 6. Extract candidate recovery breaks and process work blocks
    planned_breaks = [b for b in planned.blocks if b.kind in ("SHORT_BREAK", "LONG_BREAK")]

    breaks: list[ObservedBreak] = []
    has_large_gap = False
    missed_count = 0
    shortened_count = 0
    extended_count = 0
    taken_count = 0

    block_break_index = 0
    consecutive_focus_sessions = 1
    total_observed_rec_minutes = 0.0
    total_capped_rec_minutes = 0.0
    total_planned_rec_minutes = 0.0

    # Combine non-rest candidate intervals for overlap union
    all_busy_raw: list[tuple[datetime, datetime]] = list(normalized_fixed)
    if normalized_activity is not None:
        all_busy_raw.extend(normalized_activity)

    for i in range(len(merged_completed) - 1):
        gap_start = merged_completed[i][1]
        gap_end = merged_completed[i + 1][0]

        delta = gap_end - gap_start
        gap_micros = delta // timedelta(microseconds=1)
        gap_seconds = gap_micros / 1_000_000.0
        gap_minutes = gap_seconds / 60.0

        if gap_minutes > float(config.max_gap_same_block_minutes):
            # Gap larger than max_gap_same_block ends the work block
            has_large_gap = True
            block_break_index = 0
            consecutive_focus_sessions = 1
            continue

        # Candidate break within work block
        block_break_index += 1
        overall_break_index = len(breaks) + 1

        # Determine target planned break
        if block_break_index <= len(planned_breaks):
            target_planned_minutes = planned_breaks[block_break_index - 1].duration_minutes
        else:
            target_planned_minutes = planned.strategy.short_break_minutes

        # Clip busy intervals to gap and union them
        clipped_busy: list[tuple[datetime, datetime]] = []
        for bs, be in all_busy_raw:
            c_s = max(bs, gap_start)
            c_e = min(be, gap_end)
            if c_e > c_s:
                clipped_busy.append((c_s, c_e))

        merged_busy = _union_intervals(clipped_busy)
        busy_micros = sum((be - bs) // timedelta(microseconds=1) for bs, be in merged_busy)
        busy_seconds = busy_micros / 1_000_000.0

        credited_seconds = max(0.0, gap_seconds - busy_seconds)
        credited_minutes = credited_seconds / 60.0
        capped_minutes = min(credited_minutes, float(target_planned_minutes))

        # Integer arithmetic on seconds for 80% and 150% boundaries
        credited_sec_int = int(Decimal(str(credited_seconds)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        gap_sec_int = int(Decimal(str(gap_seconds)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        planned_sec_int = target_planned_minutes * 60

        if gap_sec_int < config.min_break_seconds:
            classification = "MISSED"
            missed_count += 1
        elif credited_sec_int * 100 < planned_sec_int * 80:
            classification = "SHORTENED"
            shortened_count += 1
        elif credited_sec_int * 100 <= planned_sec_int * 150:
            classification = "TAKEN"
            taken_count += 1
        else:
            classification = "EXTENDED"
            extended_count += 1

        # Check if credited recovery qualifies as long break
        long_break_target_sec = planned.strategy.long_break_minutes * 60
        long_break_threshold_scaled = int(
            round(long_break_target_sec * config.long_break_recovery_ratio * 100)
        )
        if credited_sec_int * 100 >= long_break_threshold_scaled:
            consecutive_focus_sessions = 0

        # Increment consecutive sessions for the next completed session
        consecutive_focus_sessions += 1

        total_observed_rec_minutes += credited_minutes
        total_capped_rec_minutes += capped_minutes
        total_planned_rec_minutes += float(target_planned_minutes)

        breaks.append(
            ObservedBreak(
                index=overall_break_index,
                block_break_index=block_break_index,
                start=gap_start,
                end=gap_end,
                raw_gap_minutes=gap_minutes,
                credited_recovery_minutes=credited_minutes,
                capped_recovery_minutes=capped_minutes,
                planned_duration_minutes=target_planned_minutes,
                classification=classification,
            )
        )

    # 7. Check for unobserved sessions inside the observed span
    span_start = merged_completed[0][0]
    span_end = merged_completed[-1][1]

    has_unobserved_session_in_span = False
    for s_utc, e_utc, st in unobserved_sessions_in_input:
        if st == "ABANDONED" and e_utc is not None:
            if s_utc < span_end and e_utc > span_start:
                has_unobserved_session_in_span = True
                break
        elif st == "IN_PROGRESS":
            if span_start <= s_utc <= span_end:
                has_unobserved_session_in_span = True
                break

    # 8. Determine data status
    fewer_sessions_than_planned = len(merged_completed) < planned.sessions_count
    if has_large_gap or has_unobserved_session_in_span or fewer_sessions_than_planned:
        data_status = "PARTIAL"
    else:
        data_status = "OBSERVED"

    # 9. Compute completion ratio
    completion_ratio = (
        total_capped_rec_minutes / total_planned_rec_minutes
        if total_planned_rec_minutes > 0.0
        else 1.0
    )

    return RecoveryDetection(
        breaks=tuple(breaks),
        observed_recovery_minutes=total_observed_rec_minutes,
        capped_recovery_minutes=total_capped_rec_minutes,
        planned_recovery_minutes=total_planned_rec_minutes,
        missed_breaks=missed_count,
        shortened_breaks=shortened_count,
        extended_breaks=extended_count,
        taken_breaks=taken_count,
        completion_ratio=completion_ratio,
        data_status=data_status,
        consecutive_focus_sessions_without_long_break=consecutive_focus_sessions,
        long_break_after_sessions=planned.strategy.long_break_after_sessions,
    )
