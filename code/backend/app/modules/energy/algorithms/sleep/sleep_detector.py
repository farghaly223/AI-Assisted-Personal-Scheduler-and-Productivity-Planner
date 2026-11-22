"""Pure deterministic sleep detection algorithm from device usage events."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules.energy.algorithms.sleep.config import (
    DEFAULT_SLEEP_CONFIG,
    SleepDetectionConfig,
    UserSleepSchedule,
)

# Known device event types
EVENT_SCREEN_INTERACTIVE = "SCREEN_INTERACTIVE"
EVENT_SCREEN_NON_INTERACTIVE = "SCREEN_NON_INTERACTIVE"
EVENT_KEYGUARD_SHOWN = "KEYGUARD_SHOWN"
EVENT_KEYGUARD_HIDDEN = "KEYGUARD_HIDDEN"

KNOWN_EVENT_TYPES = frozenset({
    EVENT_SCREEN_INTERACTIVE,
    EVENT_SCREEN_NON_INTERACTIVE,
    EVENT_KEYGUARD_SHOWN,
    EVENT_KEYGUARD_HIDDEN,
})


@dataclass(frozen=True)
class SleepCandidate:
    """Estimated sleep session candidate derived from device signals."""

    estimated_start: datetime  # Always UTC
    estimated_end: datetime    # Always UTC
    duration_minutes: int      # Satisfies DB check: ROUND(EXTRACT(EPOCH FROM (end-start))/60)::int
    confidence_score: float    # In [0.0, 1.0]
    source: str = "android_usage"
    status: str = "pending_confirmation"


def time_to_circular_minutes(t: time) -> float:
    """Convert a time of day to circular minutes in [0.0, 1440.0)."""
    return t.hour * 60.0 + t.minute + t.second / 60.0 + t.microsecond / 60_000_000.0


def circular_distance_minutes(m1: float, m2: float) -> float:
    """Compute the shortest distance between two clock times on the 24h circle."""
    diff = abs(m1 - m2) % 1440.0
    return min(diff, 1440.0 - diff)


def circular_mean_minutes(minutes_list: Sequence[float]) -> float:
    """Compute the circular mean of clock times on the 24-hour circle (1440 minutes).

    For example, 23:30 (1410 min) and 00:30 (30 min) average to 00:00 (0.0 min), not 12:00.
    """
    if not minutes_list:
        return 0.0

    sum_sin = sum(math.sin(m * 2.0 * math.pi / 1440.0) for m in minutes_list)
    sum_cos = sum(math.cos(m * 2.0 * math.pi / 1440.0) for m in minutes_list)

    if math.isclose(sum_sin, 0.0, abs_tol=1e-9) and math.isclose(sum_cos, 0.0, abs_tol=1e-9):
        # Diametrically opposed or symmetrical points; return first point
        return minutes_list[0] % 1440.0

    mean_rad = math.atan2(sum_sin, sum_cos) % (2.0 * math.pi)
    return (mean_rad * 1440.0 / (2.0 * math.pi)) % 1440.0


def resolve_local_time(dt_naive: datetime, tz: ZoneInfo) -> datetime:
    """Resolve a naive local datetime into a UTC datetime, DST-safe.

    Rules:
    - Ambiguous local time (DST fold / fall back): picks first occurrence (fold=0).
    - Nonexistent local time (DST gap / spring forward): shifts forward across gap (fold=0).
    """
    if dt_naive.tzinfo is not None:
        raise ValueError("resolve_local_time requires a naive datetime")
    dt_aware = dt_naive.replace(tzinfo=tz, fold=0)
    return dt_aware.astimezone(timezone.utc)


def calculate_duration_minutes(start_utc: datetime, end_utc: datetime) -> int:
    """Compute duration in minutes using integer microseconds and Decimal ROUND_HALF_UP.

    Satisfies the PostgreSQL DB check:
        duration_minutes = ROUND(EXTRACT(EPOCH FROM (end_time - start_time)) / 60)::integer
    """
    delta = end_utc - start_utc
    micros = delta // timedelta(microseconds=1)
    minutes_decimal = Decimal(micros) / Decimal(60_000_000)
    return int(minutes_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def detect_sleep_candidate(
    events: Sequence[tuple[str, datetime]],
    timezone_name_or_zone: str | ZoneInfo,
    target_date: date,
    schedule: UserSleepSchedule | None = None,
    history: Sequence[tuple[datetime, datetime]] | None = None,
    config: SleepDetectionConfig = DEFAULT_SLEEP_CONFIG,
) -> SleepCandidate | None:
    """Detect the primary sleep candidate session for target_date from device events.

    Args:
        events: Sequence of (event_type, timestamp). Input is never mutated.
        timezone_name_or_zone: User's IANA timezone name or ZoneInfo object.
        target_date: User's local wake-up date.
        schedule: Optional active UserSleepSchedule.
        history: Optional sequence of past (start, end) sleep intervals.
        config: Sleep detection heuristics and weights configuration.

    Returns:
        SleepCandidate with UTC timestamps, or None if evidence is insufficient.

    Raises:
        ValueError: If event or history timestamps are naive, or timezone is invalid.
    """
    if not events:
        return None

    # Validate timezone
    if isinstance(timezone_name_or_zone, str):
        if not timezone_name_or_zone or timezone_name_or_zone != timezone_name_or_zone.strip():
            raise ValueError(f"Invalid timezone name: {timezone_name_or_zone!r}")
        try:
            tz = ZoneInfo(timezone_name_or_zone)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise ValueError(f"Unsupported timezone: {timezone_name_or_zone}") from err
    elif isinstance(timezone_name_or_zone, ZoneInfo):
        tz = timezone_name_or_zone
    else:
        raise ValueError("timezone must be an IANA timezone name string or ZoneInfo")

    # Validate and normalize events without mutating input
    normalized_events: list[tuple[str, datetime]] = []
    for etype, ts in events:
        if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
            raise ValueError(f"Event timestamp {ts} must be timezone-aware")
        if etype in KNOWN_EVENT_TYPES:
            normalized_events.append((etype, ts.astimezone(timezone.utc)))

    if not normalized_events:
        return None

    # Deduplicate and sort chronologically
    sorted_events = sorted(set(normalized_events), key=lambda e: (e[1], e[0]))

    # Filter to screen state events for inactive gap extraction
    screen_events = [
        (etype, ts)
        for etype, ts in sorted_events
        if etype in {EVENT_SCREEN_NON_INTERACTIVE, EVENT_SCREEN_INTERACTIVE}
    ]

    # Pair events into raw inactive gaps (SCREEN_NON_INTERACTIVE -> SCREEN_INTERACTIVE)
    # Amendment 3: NON_INTERACTIVE followed by another NON_INTERACTIVE or with no later
    # INTERACTIVE must not produce a sleep gap; lower data quality (S_qual) instead.
    pending_start: datetime | None = None
    raw_gaps: list[tuple[datetime, datetime]] = []
    inconsistency_count = 0

    for etype, ts in screen_events:
        if etype == EVENT_SCREEN_NON_INTERACTIVE:
            if pending_start is not None:
                # Consecutive NON_INTERACTIVE without INTERACTIVE: drop the previous start
                inconsistency_count += 1
            pending_start = ts
        elif etype == EVENT_SCREEN_INTERACTIVE:
            if pending_start is not None:
                if ts > pending_start:
                    raw_gaps.append((pending_start, ts))
                pending_start = None
            else:
                # INTERACTIVE with no preceding pending NON_INTERACTIVE
                pass

    if pending_start is not None:
        # NON_INTERACTIVE with no following INTERACTIVE
        inconsistency_count += 1
        pending_start = None

    if not raw_gaps:
        return None

    # Merge inactive gaps separated by short awake bursts (<= max_awake_burst_minutes)
    merged_gaps: list[dict] = []
    curr_start, curr_end = raw_gaps[0]
    curr_bursts = 0

    for next_start, next_end in raw_gaps[1:]:
        awake_burst_micros = (next_start - curr_end) // timedelta(microseconds=1)
        awake_burst_minutes = awake_burst_micros / 60_000_000.0

        if 0.0 <= awake_burst_minutes <= float(config.max_awake_burst_minutes):
            # Short awake burst: merge into surrounding sleep gap
            curr_end = next_end
            curr_bursts += 1
        else:
            merged_gaps.append({
                "start": curr_start,
                "end": curr_end,
                "merged_bursts": curr_bursts,
            })
            curr_start = next_start
            curr_end = next_end
            curr_bursts = 0

    merged_gaps.append({
        "start": curr_start,
        "end": curr_end,
        "merged_bursts": curr_bursts,
    })

    # Compute search window in UTC from target_date in local timezone
    # Default: local 18:00 (target_date - 1) to local 14:00 (target_date)
    window_start_naive = datetime.combine(
        target_date - timedelta(days=1), config.search_window_start_time
    )
    window_end_naive = datetime.combine(target_date, config.search_window_end_time)
    window_start_utc = resolve_local_time(window_start_naive, tz)
    window_end_utc = resolve_local_time(window_end_naive, tz)

    # Determine schedule applicability
    sched_is_applicable = False
    sched_bed_min = 0.0
    sched_wake_min = 0.0
    if schedule is not None and schedule.is_active:
        sched_bed_min = time_to_circular_minutes(schedule.bedtime)
        sched_wake_min = time_to_circular_minutes(schedule.wake_time)
        # Check repeat_days for target_date or night before
        prev_day_num = (target_date - timedelta(days=1)).weekday()
        curr_day_num = target_date.weekday()
        if prev_day_num in schedule.repeat_days or curr_day_num in schedule.repeat_days:
            sched_is_applicable = True

    # Validate and prepare history if provided
    hist_is_applicable = False
    mean_hist_bed = 0.0
    mean_hist_wake = 0.0
    if history is not None and len(history) > 0:
        hist_starts: list[float] = []
        hist_ends: list[float] = []
        for h_start, h_end in history:
            if h_start.tzinfo is None or h_start.tzinfo.utcoffset(h_start) is None:
                raise ValueError("History interval start timestamps must be timezone-aware")
            if h_end.tzinfo is None or h_end.tzinfo.utcoffset(h_end) is None:
                raise ValueError("History interval end timestamps must be timezone-aware")
            hist_starts.append(time_to_circular_minutes(h_start.astimezone(tz).time()))
            hist_ends.append(time_to_circular_minutes(h_end.astimezone(tz).time()))
        mean_hist_bed = circular_mean_minutes(hist_starts)
        mean_hist_wake = circular_mean_minutes(hist_ends)
        hist_is_applicable = True

    # Amendment 2: Gap selection rule
    # Among merged inactive gaps whose START falls inside search window and whose
    # END is a real SCREEN_INTERACTIVE event, pick the longest within [min, max];
    # break ties by closeness to the schedule. Never clip gap to window edge.
    candidates: list[dict] = []
    for gap in merged_gaps:
        start_utc = gap["start"]
        end_utc = gap["end"]

        # START must fall inside the search window
        if not (window_start_utc <= start_utc <= window_end_utc):
            continue

        dur_mins = calculate_duration_minutes(start_utc, end_utc)
        if not (config.min_sleep_minutes <= dur_mins <= config.max_sleep_minutes):
            continue

        # Closeness to schedule for tie-breaking (smaller deviation = closer)
        start_local_time = start_utc.astimezone(tz).time()
        end_local_time = end_utc.astimezone(tz).time()
        start_circ = time_to_circular_minutes(start_local_time)
        end_circ = time_to_circular_minutes(end_local_time)

        if sched_is_applicable:
            sched_dist = (
                circular_distance_minutes(start_circ, sched_bed_min)
                + circular_distance_minutes(end_circ, sched_wake_min)
            )
        else:
            sched_dist = 0.0

        candidates.append({
            "start": start_utc,
            "end": end_utc,
            "duration_minutes": dur_mins,
            "merged_bursts": gap["merged_bursts"],
            "start_circ": start_circ,
            "end_circ": end_circ,
            "sched_dist": sched_dist,
        })

    if not candidates:
        return None

    # Pick longest; break ties with smallest schedule distance (or earlier start)
    candidates.sort(key=lambda c: (c["duration_minutes"], -c["sched_dist"], -c["start"].timestamp()), reverse=True)
    best = candidates[0]

    # --- Confidence Calculation ---
    # 1. Duration plausibility (S_dur) in [0.5, 1.0]
    dur = best["duration_minutes"]
    if config.optimal_sleep_min_minutes <= dur <= config.optimal_sleep_max_minutes:
        s_dur = 1.0
    elif dur < config.optimal_sleep_min_minutes:
        ratio = (dur - config.min_sleep_minutes) / float(
            config.optimal_sleep_min_minutes - config.min_sleep_minutes
        )
        s_dur = 0.5 + 0.5 * max(0.0, min(1.0, ratio))
    else:
        ratio = (config.max_sleep_minutes - dur) / float(
            config.max_sleep_minutes - config.optimal_sleep_max_minutes
        )
        s_dur = 0.5 + 0.5 * max(0.0, min(1.0, ratio))

    # 2. Data quality (S_qual) in [0.0, 1.0]
    # Penalize for merged awake bursts and data inconsistencies
    penalty = 0.10 * best["merged_bursts"] + 0.15 * inconsistency_count
    s_qual = max(0.0, min(1.0, 1.0 - penalty))

    # 3. Schedule overlap (S_sched) in [0.0, 1.0]
    # Without schedule, s_sched = 0.0 (max confidence without schedule & history = 0.75)
    if sched_is_applicable:
        dev = best["sched_dist"] / 2.0
        s_sched = max(0.0, 1.0 - dev / config.max_tolerance_deviation_minutes)
    else:
        s_sched = 0.0

    # 4. History similarity (S_hist) in [0.0, 1.0]
    if hist_is_applicable:
        hist_dev = (
            circular_distance_minutes(best["start_circ"], mean_hist_bed)
            + circular_distance_minutes(best["end_circ"], mean_hist_wake)
        ) / 2.0
        s_hist = max(0.0, 1.0 - hist_dev / config.max_tolerance_deviation_minutes)
    else:
        s_hist = 0.0

    confidence = (
        config.weight_duration_plausibility * s_dur
        + config.weight_data_quality * s_qual
        + config.weight_schedule_overlap * s_sched
        + config.weight_history_similarity * s_hist
    )
    final_confidence = max(0.0, min(1.0, round(confidence, 4)))

    return SleepCandidate(
        estimated_start=best["start"],
        estimated_end=best["end"],
        duration_minutes=best["duration_minutes"],
        confidence_score=final_confidence,
        source="android_usage",
        status="pending_confirmation",
    )
