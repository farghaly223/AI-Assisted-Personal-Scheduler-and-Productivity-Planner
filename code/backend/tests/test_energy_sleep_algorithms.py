"""Unit tests for Energy module sleep algorithms.

Plain unit tests only; no database fixtures or network connections.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
from zoneinfo import ZoneInfo
import pytest

from app.modules.energy.algorithms.sleep import (
    DEFAULT_SLEEP_CONFIG,
    EVENT_KEYGUARD_HIDDEN,
    EVENT_KEYGUARD_SHOWN,
    EVENT_SCREEN_INTERACTIVE,
    EVENT_SCREEN_NON_INTERACTIVE,
    SleepCandidate,
    SleepDetectionConfig,
    UserSleepSchedule,
    calculate_duration_minutes,
    circular_distance_minutes,
    circular_mean_minutes,
    detect_sleep_candidate,
    normalize_sleep_quality,
    resolve_local_time,
    time_to_circular_minutes,
)


# ==============================================================================
# 1. Sleep Quality Normalization Tests
# ==============================================================================

def test_normalize_sleep_quality_valid_ratings():
    assert normalize_sleep_quality(1) == 0.0
    assert normalize_sleep_quality(2) == 25.0
    assert normalize_sleep_quality(3) == 50.0
    assert normalize_sleep_quality(4) == 75.0
    assert normalize_sleep_quality(5) == 100.0


def test_normalize_sleep_quality_none():
    assert normalize_sleep_quality(None) is None


@pytest.mark.parametrize("invalid_rating", [
    True,
    False,
    3.5,
    "4",
    0,
    6,
    -1,
    10,
    [],
    {},
])
def test_normalize_sleep_quality_invalid_inputs(invalid_rating):
    with pytest.raises(ValueError):
        normalize_sleep_quality(invalid_rating)


# ==============================================================================
# 2. Math & Circular Time Utility Tests
# ==============================================================================

def test_circular_mean_midnight():
    # 23:30 (1410 min) and 00:30 (30 min) must average to 00:00 (0.0 min), not 12:00
    m1 = time_to_circular_minutes(time(23, 30))
    m2 = time_to_circular_minutes(time(0, 30))
    avg = circular_mean_minutes([m1, m2])
    assert math.isclose(avg, 0.0, abs_tol=1e-5) or math.isclose(avg, 1440.0, abs_tol=1e-5)


def test_circular_distance():
    m1 = time_to_circular_minutes(time(23, 45))  # 1425 min
    m2 = time_to_circular_minutes(time(0, 15))   # 15 min
    dist = circular_distance_minutes(m1, m2)
    assert dist == 30.0


def test_duration_rounding_half_up():
    # Integer microseconds -> Decimal ROUND_HALF_UP (satisfying Postgres DB check)
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    # 180 min 29 sec -> 180 min
    t_29 = base + timedelta(minutes=180, seconds=29)
    assert calculate_duration_minutes(base, t_29) == 180

    # 180 min 30 sec (180.5 min) -> 181 min (ROUND_HALF_UP)
    t_30 = base + timedelta(minutes=180, seconds=30)
    assert calculate_duration_minutes(base, t_30) == 181

    # 180 min 31 sec -> 181 min
    t_31 = base + timedelta(minutes=180, seconds=31)
    assert calculate_duration_minutes(base, t_31) == 181

    # 181 min 30 sec (181.5 min) -> 182 min (Banker's round in Python would round to 182,
    # but 182.5 would round to 182, whereas ROUND_HALF_UP rounds 182.5 to 183)
    t_even_half = base + timedelta(minutes=182, seconds=30)
    assert calculate_duration_minutes(base, t_even_half) == 183


# ==============================================================================
# 3. DST Handling Tests
# ==============================================================================

def test_resolve_local_time_dst_gap_and_fold():
    tz_ny = ZoneInfo("America/New_York")

    # Nonexistent local time (spring forward): March 8, 2026 02:30 doesn't exist
    # Shift forward behavior: resolves to 03:30 local time in EDT (-04:00)
    gap_dt = datetime(2026, 3, 8, 2, 30)
    resolved_gap = resolve_local_time(gap_dt, tz_ny)
    assert resolved_gap == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
    assert resolved_gap.astimezone(tz_ny).time() == time(3, 30)

    # Ambiguous local time (fall back): November 1, 2026 01:30 occurs twice
    # First occurrence behavior (fold=0): EDT (-04:00) -> 05:30 UTC
    fold_dt = datetime(2026, 11, 1, 1, 30)
    resolved_fold = resolve_local_time(fold_dt, tz_ny)
    assert resolved_fold == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)


def test_sleep_candidate_dst_transition_africa_cairo():
    # Egypt last Friday of April 2026 is April 24, 2026 (spring forward: 00:00 -> 01:00)
    tz = "Africa/Cairo"
    target_date = date(2026, 4, 24)
    cairo_tz = ZoneInfo(tz)

    # Sleep starts at 23:30 local on April 23 (UTC+2 -> 21:30 UTC)
    # Sleep ends at 07:30 local on April 24 (UTC+3 -> 04:30 UTC)
    start_dt = datetime(2026, 4, 23, 23, 30, tzinfo=cairo_tz)
    end_dt = datetime(2026, 4, 24, 7, 30, tzinfo=cairo_tz)

    events = [
        (EVENT_SCREEN_NON_INTERACTIVE, start_dt),
        (EVENT_SCREEN_INTERACTIVE, end_dt),
    ]

    candidate = detect_sleep_candidate(events, tz, target_date)
    assert candidate is not None
    # 21:30 UTC to 04:30 UTC is exactly 7 hours = 420 minutes
    assert candidate.duration_minutes == 420
    assert candidate.estimated_start == start_dt.astimezone(timezone.utc)
    assert candidate.estimated_end == end_dt.astimezone(timezone.utc)


def test_sleep_candidate_dst_transition_america_new_york():
    # New York second Sunday of March 2026 is March 8, 2026 (spring forward: 02:00 -> 03:00)
    tz = "America/New_York"
    target_date = date(2026, 3, 8)
    ny_tz = ZoneInfo(tz)

    start_dt = datetime(2026, 3, 7, 23, 0, tzinfo=ny_tz)  # EST (-5) -> March 8 04:00 UTC
    end_dt = datetime(2026, 3, 8, 7, 0, tzinfo=ny_tz)     # EDT (-4) -> March 8 11:00 UTC

    events = [
        (EVENT_SCREEN_NON_INTERACTIVE, start_dt),
        (EVENT_SCREEN_INTERACTIVE, end_dt),
    ]

    candidate = detect_sleep_candidate(events, tz, target_date)
    assert candidate is not None
    # 04:00 UTC to 11:00 UTC = 7 hours = 420 minutes
    assert candidate.duration_minutes == 420


# ==============================================================================
# 4. Sleep Candidate Detection Scenarios
# ==============================================================================

def test_normal_night_overnight_crossing_midnight():
    tz = "UTC"
    target_date = date(2026, 5, 10)

    start_dt = datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)

    events = [
        (EVENT_SCREEN_NON_INTERACTIVE, start_dt),
        (EVENT_SCREEN_INTERACTIVE, end_dt),
    ]

    candidate = detect_sleep_candidate(events, tz, target_date)
    assert candidate is not None
    assert candidate.estimated_start == start_dt
    assert candidate.estimated_end == end_dt
    assert candidate.duration_minutes == 480
    assert candidate.source == "android_usage"
    assert candidate.status == "pending_confirmation"
    assert 0.0 <= candidate.confidence_score <= 1.0


def test_short_phone_check_at_3am_merged_into_sleep():
    tz = "UTC"
    target_date = date(2026, 5, 10)

    start_dt = datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)
    wake_check = datetime(2026, 5, 10, 3, 0, tzinfo=timezone.utc)
    sleep_again = datetime(2026, 5, 10, 3, 4, tzinfo=timezone.utc)  # 4 min check (< 10 min)
    morning_wake = datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)

    events = [
        (EVENT_SCREEN_NON_INTERACTIVE, start_dt),
        (EVENT_SCREEN_INTERACTIVE, wake_check),
        (EVENT_SCREEN_NON_INTERACTIVE, sleep_again),
        (EVENT_SCREEN_INTERACTIVE, morning_wake),
    ]

    candidate = detect_sleep_candidate(events, tz, target_date)
    assert candidate is not None
    assert candidate.estimated_start == start_dt
    assert candidate.estimated_end == morning_wake
    assert candidate.duration_minutes == 480


def test_unsorted_and_duplicate_events():
    tz = "UTC"
    target_date = date(2026, 5, 10)

    start_dt = datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)

    events = [
        (EVENT_SCREEN_INTERACTIVE, end_dt),
        (EVENT_SCREEN_NON_INTERACTIVE, start_dt),
        (EVENT_SCREEN_NON_INTERACTIVE, start_dt),  # Duplicate
        (EVENT_SCREEN_INTERACTIVE, end_dt),        # Duplicate
    ]

    candidate = detect_sleep_candidate(events, tz, target_date)
    assert candidate is not None
    assert candidate.estimated_start == start_dt
    assert candidate.estimated_end == end_dt
    assert candidate.duration_minutes == 480


def test_unknown_event_types_ignored_and_keyguard_handled():
    tz = "UTC"
    target_date = date(2026, 5, 10)

    start_dt = datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)

    events = [
        ("BATTERY_LOW", start_dt - timedelta(minutes=30)),
        (EVENT_KEYGUARD_SHOWN, start_dt),
        (EVENT_SCREEN_NON_INTERACTIVE, start_dt),
        ("WIFI_DISCONNECTED", start_dt + timedelta(minutes=10)),
        (EVENT_KEYGUARD_HIDDEN, end_dt),
        (EVENT_SCREEN_INTERACTIVE, end_dt),
        ("AIRPLANE_MODE_OFF", end_dt + timedelta(minutes=5)),
    ]

    candidate = detect_sleep_candidate(events, tz, target_date)
    assert candidate is not None
    assert candidate.estimated_start == start_dt
    assert candidate.estimated_end == end_dt
    assert candidate.duration_minutes == 480


def test_naive_timestamps_rejected():
    tz = "UTC"
    target_date = date(2026, 5, 10)

    naive_events = [
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 23, 0)),
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)),
    ]

    with pytest.raises(ValueError, match="timezone-aware"):
        detect_sleep_candidate(naive_events, tz, target_date)


def test_no_events_returns_none():
    tz = "UTC"
    target_date = date(2026, 5, 10)
    assert detect_sleep_candidate([], tz, target_date) is None


def test_only_one_boundary_returns_none():
    tz = "UTC"
    target_date = date(2026, 5, 10)

    # Only start boundary
    events_only_start = [
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc))
    ]
    assert detect_sleep_candidate(events_only_start, tz, target_date) is None

    # Only end boundary
    events_only_end = [
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc))
    ]
    assert detect_sleep_candidate(events_only_end, tz, target_date) is None


def test_gap_shorter_than_min_and_longer_than_max():
    tz = "UTC"
    target_date = date(2026, 5, 10)

    # Shorter than 180 min (e.g. 2 hours = 120 min)
    short_events = [
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)),
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 1, 0, tzinfo=timezone.utc)),
    ]
    assert detect_sleep_candidate(short_events, tz, target_date) is None

    # Longer than 840 min (e.g. 15 hours = 900 min)
    long_events = [
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 18, 0, tzinfo=timezone.utc)),
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)),
    ]
    assert detect_sleep_candidate(long_events, tz, target_date) is None


def test_event_pairing_inconsistencies():
    # Consecutive NON_INTERACTIVE and trailing NON_INTERACTIVE must lower S_qual
    tz = "UTC"
    target_date = date(2026, 5, 10)

    # Clean night
    clean_events = [
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)),
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)),
    ]
    cand_clean = detect_sleep_candidate(clean_events, tz, target_date)
    assert cand_clean is not None

    # Night with inconsistent consecutive NON_INTERACTIVE
    inconsistent_events = [
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 21, 0, tzinfo=timezone.utc)),  # Missing interactive
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)),
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)),
    ]
    cand_inconsistent = detect_sleep_candidate(inconsistent_events, tz, target_date)
    assert cand_inconsistent is not None
    # Both detect 23:00 to 07:00 (longest/valid gap), but inconsistent data has lower confidence
    assert cand_inconsistent.estimated_start == datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)
    assert cand_inconsistent.confidence_score < cand_clean.confidence_score


def test_gap_selection_longest_and_tie_breaker():
    tz = "UTC"
    target_date = date(2026, 5, 10)

    # Gap 1: 19:00 to 23:00 (4h = 240m)
    # Gap 2: 23:30 to 07:00 (7.5h = 450m) - should be chosen as longest
    events = [
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 19, 0, tzinfo=timezone.utc)),
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)),
        # 30 min awake burst (not merged)
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 23, 30, tzinfo=timezone.utc)),
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)),
    ]

    candidate = detect_sleep_candidate(events, tz, target_date)
    assert candidate is not None
    assert candidate.estimated_start == datetime(2026, 5, 9, 23, 30, tzinfo=timezone.utc)
    assert candidate.estimated_end == datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)
    assert candidate.duration_minutes == 450


def test_confidence_comparison_schedule_and_history():
    tz = "UTC"
    target_date = date(2026, 5, 10)  # Sunday (weekday 6); Saturday is 5

    events = [
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)),
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)),
    ]

    # Baseline without schedule or history (max possible confidence is 0.75)
    cand_base = detect_sleep_candidate(events, tz, target_date)
    assert cand_base is not None
    assert cand_base.confidence_score <= 0.75

    # Matching schedule
    matching_schedule = UserSleepSchedule(
        bedtime=time(23, 0),
        wake_time=time(7, 0),
        repeat_days=(5, 6),
        is_active=True,
    )
    cand_matching_sched = detect_sleep_candidate(events, tz, target_date, schedule=matching_schedule)
    assert cand_matching_sched is not None
    assert cand_matching_sched.confidence_score > cand_base.confidence_score

    # Inactive schedule should not boost score
    inactive_schedule = UserSleepSchedule(
        bedtime=time(23, 0),
        wake_time=time(7, 0),
        repeat_days=(5, 6),
        is_active=False,
    )
    cand_inactive = detect_sleep_candidate(events, tz, target_date, schedule=inactive_schedule)
    assert cand_inactive is not None
    assert cand_inactive.confidence_score == cand_base.confidence_score

    # Mismatching schedule (bedtime 11:00 AM, wake 17:00 PM)
    mismatch_schedule = UserSleepSchedule(
        bedtime=time(11, 0),
        wake_time=time(17, 0),
        repeat_days=(5, 6),
        is_active=True,
    )
    cand_mismatch_sched = detect_sleep_candidate(events, tz, target_date, schedule=mismatch_schedule)
    assert cand_mismatch_sched is not None
    assert cand_mismatch_sched.confidence_score < cand_matching_sched.confidence_score

    # Matching history
    matching_history = [
        (
            datetime(2026, 5, 8, 23, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 9, 7, 0, tzinfo=timezone.utc),
        ),
    ]
    cand_matching_hist = detect_sleep_candidate(events, tz, target_date, history=matching_history)
    assert cand_matching_hist is not None
    assert cand_matching_hist.confidence_score > cand_base.confidence_score

    # Both matching schedule and history -> highest confidence (up to 1.0)
    cand_all = detect_sleep_candidate(
        events, tz, target_date, schedule=matching_schedule, history=matching_history
    )
    assert cand_all is not None
    assert cand_all.confidence_score >= cand_matching_sched.confidence_score
    assert cand_all.confidence_score == 1.0


def test_history_timestamps_must_be_timezone_aware():
    tz = "UTC"
    target_date = date(2026, 5, 10)
    events = [
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)),
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)),
    ]

    naive_history = [
        (datetime(2026, 5, 8, 23, 0), datetime(2026, 5, 9, 7, 0, tzinfo=timezone.utc))
    ]
    with pytest.raises(ValueError, match="timezone-aware"):
        detect_sleep_candidate(events, tz, target_date, history=naive_history)


def test_input_events_not_mutated():
    tz = "UTC"
    target_date = date(2026, 5, 10)

    original_events = [
        (EVENT_SCREEN_INTERACTIVE, datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)),
        ("UNKNOWN_EVENT", datetime(2026, 5, 10, 2, 0, tzinfo=timezone.utc)),
        (EVENT_SCREEN_NON_INTERACTIVE, datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)),
    ]
    events_copy = list(original_events)

    detect_sleep_candidate(original_events, tz, target_date)
    assert original_events == events_copy
