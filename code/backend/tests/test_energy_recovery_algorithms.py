"""Unit tests for Energy module recovery algorithms.

Plain unit tests only; no database fixtures or network connections.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest

from app.modules.energy.algorithms.recovery import (
    DEFAULT_RECOVERY_CONFIG,
    DEFAULT_WORKLOAD_MULTIPLIERS,
    ObservedBreak,
    PlannedSchedule,
    RecoveryBlock,
    RecoveryConfig,
    RecoveryDetection,
    RecoveryScore,
    StrategyParams,
    calculate_planned_recovery,
    calculate_recovery_score,
    detect_actual_recovery,
)


# ==============================================================================
# 1. Recovery Planner Tests
# ==============================================================================

def test_pomodoro_timeline_exact_times():
    """Verify Pomodoro 25/5/20/4 timeline generation with exact timestamps."""
    strategy = StrategyParams(
        work_minutes=25,
        short_break_minutes=5,
        long_break_minutes=20,
        long_break_after_sessions=4,
    )
    start = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    schedule = calculate_planned_recovery(strategy, start, sessions_count=5)

    assert schedule.sessions_count == 5
    assert schedule.total_focus_minutes == 125
    assert schedule.total_planned_recovery_minutes == 35  # 5 + 5 + 5 + 20

    # 5 focus sessions + 4 breaks = 9 blocks total
    assert len(schedule.blocks) == 9

    # Session 1: 09:00 -> 09:25
    assert schedule.blocks[0] == RecoveryBlock(
        kind="FOCUS",
        index=1,
        start=datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 10, 9, 25, tzinfo=timezone.utc),
        duration_minutes=25,
    )
    # Break 1: 09:25 -> 09:30 (short)
    assert schedule.blocks[1] == RecoveryBlock(
        kind="SHORT_BREAK",
        index=1,
        start=datetime(2026, 5, 10, 9, 25, tzinfo=timezone.utc),
        end=datetime(2026, 5, 10, 9, 30, tzinfo=timezone.utc),
        duration_minutes=5,
    )
    # Session 2: 09:30 -> 09:55
    assert schedule.blocks[2] == RecoveryBlock(
        kind="FOCUS",
        index=2,
        start=datetime(2026, 5, 10, 9, 30, tzinfo=timezone.utc),
        end=datetime(2026, 5, 10, 9, 55, tzinfo=timezone.utc),
        duration_minutes=25,
    )
    # Break 2: 09:55 -> 10:00 (short)
    assert schedule.blocks[3].kind == "SHORT_BREAK"
    assert schedule.blocks[3].duration_minutes == 5

    # Session 3: 10:00 -> 10:25
    assert schedule.blocks[4].kind == "FOCUS"

    # Break 3: 10:25 -> 10:30 (short)
    assert schedule.blocks[5].kind == "SHORT_BREAK"

    # Session 4: 10:30 -> 10:55
    assert schedule.blocks[6].kind == "FOCUS"

    # Break 4: 10:55 -> 11:15 (long break after 4 sessions!)
    assert schedule.blocks[7] == RecoveryBlock(
        kind="LONG_BREAK",
        index=4,
        start=datetime(2026, 5, 10, 10, 55, tzinfo=timezone.utc),
        end=datetime(2026, 5, 10, 11, 15, tzinfo=timezone.utc),
        duration_minutes=20,
    )

    # Session 5: 11:15 -> 11:40
    assert schedule.blocks[8] == RecoveryBlock(
        kind="FOCUS",
        index=5,
        start=datetime(2026, 5, 10, 11, 15, tzinfo=timezone.utc),
        end=datetime(2026, 5, 10, 11, 40, tzinfo=timezone.utc),
        duration_minutes=25,
    )


def test_custom_50_10_after_3():
    """Verify custom strategy 50/10 with long break after 3 sessions."""
    strategy = StrategyParams(
        work_minutes=50,
        short_break_minutes=10,
        long_break_minutes=30,
        long_break_after_sessions=3,
    )
    start = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
    schedule = calculate_planned_recovery(strategy, start, sessions_count=4)

    assert schedule.sessions_count == 4
    assert schedule.total_focus_minutes == 200
    # Breaks: short (10), short (10), long (30) = 50 min
    assert schedule.total_planned_recovery_minutes == 50

    breaks = [b for b in schedule.blocks if b.kind != "FOCUS"]
    assert len(breaks) == 3
    assert breaks[0].kind == "SHORT_BREAK"
    assert breaks[0].duration_minutes == 10
    assert breaks[1].kind == "SHORT_BREAK"
    assert breaks[1].duration_minutes == 10
    assert breaks[2].kind == "LONG_BREAK"
    assert breaks[2].duration_minutes == 30


def test_no_trailing_break_after_last_session():
    """Verify that no break block is scheduled after the final focus session."""
    strategy = StrategyParams(work_minutes=25, short_break_minutes=5, long_break_minutes=20, long_break_after_sessions=2)
    start = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)

    for n in (1, 2, 3, 4):
        plan = calculate_planned_recovery(strategy, start, sessions_count=n)
        assert plan.blocks[-1].kind == "FOCUS"
        assert plan.blocks[-1].index == n


def test_planner_timezone_normalization():
    """Verify that non-UTC timezone-aware start is normalized cleanly to UTC."""
    cairo_tz = ZoneInfo("Africa/Cairo")
    start_cairo = datetime(2026, 7, 1, 12, 0, tzinfo=cairo_tz)  # Cairo UTC+3 -> 09:00 UTC
    strategy = StrategyParams(work_minutes=30, short_break_minutes=10, long_break_minutes=20, long_break_after_sessions=4)

    plan = calculate_planned_recovery(strategy, start_cairo, sessions_count=2)
    assert plan.start == datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    assert plan.blocks[0].start == datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    assert plan.blocks[0].end == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("invalid_kwargs", [
    {"work_minutes": 0},
    {"work_minutes": -10},
    {"short_break_minutes": 0},
    {"short_break_minutes": -5},
    {"long_break_minutes": 0},
    {"long_break_after_sessions": 0},
    {"work_minutes": True},
    {"short_break_minutes": 5.5},
])
def test_strategy_params_invalid_values(invalid_kwargs):
    kwargs = {"work_minutes": 25, "short_break_minutes": 5, "long_break_minutes": 20, "long_break_after_sessions": 4}
    kwargs.update(invalid_kwargs)
    with pytest.raises(ValueError):
        StrategyParams(**kwargs)


@pytest.mark.parametrize("invalid_sessions", [0, 25, -1, 100, True, False, 3.5, "4"])
def test_planner_invalid_sessions_count(invalid_sessions):
    strategy = StrategyParams()
    start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        calculate_planned_recovery(strategy, start, sessions_count=invalid_sessions)


def test_planner_naive_datetime():
    strategy = StrategyParams()
    naive_start = datetime(2026, 1, 1, 10, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_planned_recovery(strategy, naive_start, sessions_count=2)


# ==============================================================================
# 2. Recovery Detector Tests
# ==============================================================================

@pytest.fixture
def standard_pomodoro_plan():
    strategy = StrategyParams(work_minutes=25, short_break_minutes=5, long_break_minutes=20, long_break_after_sessions=4)
    start = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    return calculate_planned_recovery(strategy, start, sessions_count=4)


def test_detector_all_breaks_taken(standard_pomodoro_plan):
    """Verify detection when user takes all breaks as planned."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=30), base + timedelta(minutes=55), "COMPLETED"),
        (base + timedelta(minutes=60), base + timedelta(minutes=85), "COMPLETED"),
        (base + timedelta(minutes=90), base + timedelta(minutes=115), "COMPLETED"),
    ]

    detection = detect_actual_recovery(standard_pomodoro_plan, sessions)

    assert detection.data_status == "OBSERVED"
    assert len(detection.breaks) == 3
    assert detection.missed_breaks == 0
    assert detection.shortened_breaks == 0
    assert detection.extended_breaks == 0
    assert detection.taken_breaks == 3
    assert detection.planned_recovery_minutes == 15.0  # 3 breaks of 5 min each
    assert detection.observed_recovery_minutes == 15.0
    assert detection.completion_ratio == 1.0


def test_detector_missed_break(standard_pomodoro_plan):
    """Verify detection of missed break (< 60 seconds gap)."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        # Next session starts 30 seconds after previous one ends
        (base + timedelta(minutes=25, seconds=30), base + timedelta(minutes=50, seconds=30), "COMPLETED"),
    ]

    detection = detect_actual_recovery(standard_pomodoro_plan, sessions)
    assert len(detection.breaks) == 1
    assert detection.breaks[0].classification == "MISSED"
    assert detection.missed_breaks == 1
    assert detection.taken_breaks == 0


def test_detector_shortened_and_extended_breaks(standard_pomodoro_plan):
    """Verify classification of shortened (<80%) and extended (>150%) breaks."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        # Break 1: 3 minutes gap (3/5 = 60% < 80%) -> SHORTENED
        (base + timedelta(minutes=28), base + timedelta(minutes=53), "COMPLETED"),
        # Break 2: 9 minutes gap (9/5 = 180% > 150%) -> EXTENDED
        (base + timedelta(minutes=62), base + timedelta(minutes=87), "COMPLETED"),
    ]

    detection = detect_actual_recovery(standard_pomodoro_plan, sessions)
    assert len(detection.breaks) == 2
    assert detection.breaks[0].classification == "SHORTENED"
    assert detection.breaks[1].classification == "EXTENDED"
    assert detection.shortened_breaks == 1
    assert detection.extended_breaks == 1


def test_detector_integer_arithmetic_exact_boundaries(standard_pomodoro_plan):
    """Verify integer comparisons on seconds at exactly 80% and 150% boundaries."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    # Planned short break = 5 min = 300 sec.
    # 80% boundary: 240 sec.
    # 239 sec -> SHORTENED, 240 sec -> TAKEN
    # 150% boundary: 450 sec.
    # 450 sec -> TAKEN, 451 sec -> EXTENDED

    # Test 239s vs 240s
    sessions_80 = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=25, seconds=239), base + timedelta(minutes=50), "COMPLETED"),
        (base + timedelta(minutes=50, seconds=240), base + timedelta(minutes=75), "COMPLETED"),
    ]
    det_80 = detect_actual_recovery(standard_pomodoro_plan, sessions_80)
    assert det_80.breaks[0].classification == "SHORTENED"
    assert det_80.breaks[1].classification == "TAKEN"

    # Test 450s vs 451s
    sessions_150 = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=25, seconds=450), base + timedelta(minutes=50), "COMPLETED"),
        (base + timedelta(minutes=50, seconds=451), base + timedelta(minutes=75), "COMPLETED"),
    ]
    det_150 = detect_actual_recovery(standard_pomodoro_plan, sessions_150)
    assert det_150.breaks[0].classification == "TAKEN"
    assert det_150.breaks[1].classification == "EXTENDED"


def test_detector_fixed_event_reduces_credit(standard_pomodoro_plan):
    """Verify that a fixed event overlapping a break reduces credited recovery."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    # 5-minute break from 09:25 to 09:30
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=30), base + timedelta(minutes=55), "COMPLETED"),
    ]
    # Fixed event: 09:26 -> 09:28 (2 minutes occupied)
    fixed = [(base + timedelta(minutes=26), base + timedelta(minutes=28))]

    detection = detect_actual_recovery(standard_pomodoro_plan, sessions, fixed_events=fixed)
    assert len(detection.breaks) == 1
    brk = detection.breaks[0]
    assert brk.raw_gap_minutes == 5.0
    assert brk.credited_recovery_minutes == 3.0
    assert brk.classification == "SHORTENED"  # 3m < 4m (80%)


def test_detector_app_activity_reduces_credit(standard_pomodoro_plan):
    """Verify that app activity overlapping a break reduces credited recovery."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=30), base + timedelta(minutes=55), "COMPLETED"),
    ]
    activity = [(base + timedelta(minutes=27), base + timedelta(minutes=29))]

    detection = detect_actual_recovery(standard_pomodoro_plan, sessions, app_activity=activity)
    brk = detection.breaks[0]
    assert brk.credited_recovery_minutes == 3.0


def test_detector_union_overlaps_no_double_count(standard_pomodoro_plan):
    """Verify union of fixed events and app activity prevents double counting."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    # Gap is 10 minutes: 09:25 to 09:35
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=35), base + timedelta(minutes=60), "COMPLETED"),
    ]
    # Fixed: 09:26 to 09:30 (4 minutes)
    fixed = [(base + timedelta(minutes=26), base + timedelta(minutes=30))]
    # Activity: 09:28 to 09:32 (4 minutes, overlaps fixed by 2 minutes)
    activity = [(base + timedelta(minutes=28), base + timedelta(minutes=32))]
    # Union is 09:26 to 09:32 = 6 minutes total occupied time.

    detection = detect_actual_recovery(standard_pomodoro_plan, sessions, fixed_events=fixed, app_activity=activity)
    brk = detection.breaks[0]
    assert brk.raw_gap_minutes == 10.0
    # Credited = 10 - 6 = 4 minutes (if double counted, would be 10 - 8 = 2 min)
    assert brk.credited_recovery_minutes == 4.0


def test_detector_gap_larger_than_max_ends_work_block(standard_pomodoro_plan):
    """Verify gap > max_gap_same_block is not counted as recovery and ends work block."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    # Gap 1: 5 min. Gap 2: 70 min (> 60 min default). Gap 3: 5 min.
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=30), base + timedelta(minutes=55), "COMPLETED"),
        (base + timedelta(minutes=125), base + timedelta(minutes=150), "COMPLETED"),
        (base + timedelta(minutes=155), base + timedelta(minutes=180), "COMPLETED"),
    ]

    detection = detect_actual_recovery(standard_pomodoro_plan, sessions)
    # Only 2 breaks observed: the 70-min gap was ignored
    assert len(detection.breaks) == 2
    assert detection.data_status == "PARTIAL"


def test_detector_break_index_restarts_rule(standard_pomodoro_plan):
    """Verify break index restarts with each new work block and compares to planned break."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=30), base + timedelta(minutes=55), "COMPLETED"),
        # Gap > 60 ends block
        (base + timedelta(minutes=120), base + timedelta(minutes=145), "COMPLETED"),
        (base + timedelta(minutes=150), base + timedelta(minutes=175), "COMPLETED"),
    ]

    detection = detect_actual_recovery(standard_pomodoro_plan, sessions)
    assert len(detection.breaks) == 2
    # First break in block 1: block_break_index = 1
    assert detection.breaks[0].block_break_index == 1
    assert detection.breaks[0].planned_duration_minutes == 5
    # First break in block 2: block_break_index restarts at 1!
    assert detection.breaks[1].block_break_index == 1
    assert detection.breaks[1].planned_duration_minutes == 5


def test_detector_ignored_and_unobserved_sessions(standard_pomodoro_plan):
    """Verify IN_PROGRESS, CANCELLED, and ABANDONED session handling."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=26), base + timedelta(minutes=27), "CANCELLED"),  # Ignored
        (base + timedelta(minutes=30), base + timedelta(minutes=55), "COMPLETED"),
        (base + timedelta(minutes=60), None, "IN_PROGRESS"),  # Inside span -> PARTIAL
    ]

    detection = detect_actual_recovery(standard_pomodoro_plan, sessions)
    assert len(detection.breaks) == 1
    assert detection.data_status == "PARTIAL"


def test_detector_overlapping_completed_sessions_merged(standard_pomodoro_plan):
    """Verify overlapping/touching completed sessions are merged into union."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    # Session 1: 09:00 -> 09:30
    # Session 2: 09:20 -> 09:50 (overlaps Session 1)
    # Session 3: 09:50 -> 10:15 (touches Session 2)
    # Merged continuous completed session: 09:00 -> 10:15
    # Session 4: 10:20 -> 10:45 (gap of 5 min from 10:15 to 10:20)
    sessions = [
        (base, base + timedelta(minutes=30), "COMPLETED"),
        (base + timedelta(minutes=20), base + timedelta(minutes=50), "COMPLETED"),
        (base + timedelta(minutes=50), base + timedelta(minutes=75), "COMPLETED"),
        (base + timedelta(minutes=80), base + timedelta(minutes=105), "COMPLETED"),
    ]

    # Verify input is not mutated
    sessions_copy = list(sessions)
    detection = detect_actual_recovery(standard_pomodoro_plan, sessions)
    assert sessions == sessions_copy

    assert len(detection.breaks) == 1
    brk = detection.breaks[0]
    assert brk.start == base + timedelta(minutes=75)
    assert brk.end == base + timedelta(minutes=80)
    assert brk.raw_gap_minutes == 5.0
    assert brk.classification == "TAKEN"


def test_detector_unsorted_input(standard_pomodoro_plan):
    """Verify detector handles unsorted input sessions correctly."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    s1 = (base, base + timedelta(minutes=25), "COMPLETED")
    s2 = (base + timedelta(minutes=30), base + timedelta(minutes=55), "COMPLETED")
    s3 = (base + timedelta(minutes=60), base + timedelta(minutes=85), "COMPLETED")

    # Pass in reverse order
    detection = detect_actual_recovery(standard_pomodoro_plan, [s3, s1, s2])
    assert len(detection.breaks) == 2
    assert detection.breaks[0].start == base + timedelta(minutes=25)
    assert detection.breaks[1].start == base + timedelta(minutes=55)


def test_detector_unknown_data_status(standard_pomodoro_plan):
    """Verify UNKNOWN data_status for 0 or 1 completed focus session."""
    # 0 sessions
    det_0 = detect_actual_recovery(standard_pomodoro_plan, [])
    assert det_0.data_status == "UNKNOWN"
    assert det_0.completion_ratio is None
    assert len(det_0.breaks) == 0

    # 1 session
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    det_1 = detect_actual_recovery(standard_pomodoro_plan, [(base, base + timedelta(minutes=25), "COMPLETED")])
    assert det_1.data_status == "UNKNOWN"
    assert det_1.completion_ratio is None
    assert len(det_1.breaks) == 0


def test_detector_partial_data_status_cases(standard_pomodoro_plan):
    """Verify all PARTIAL data status conditions explicitly."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)

    # Case 1: Fewer sessions completed than planned (2 of 4)
    det_fewer = detect_actual_recovery(standard_pomodoro_plan, [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=30), base + timedelta(minutes=55), "COMPLETED"),
    ])
    assert det_fewer.data_status == "PARTIAL"

    # Case 2: Abandoned session inside span
    det_abandoned = detect_actual_recovery(standard_pomodoro_plan, [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=26), base + timedelta(minutes=28), "ABANDONED"),
        (base + timedelta(minutes=30), base + timedelta(minutes=55), "COMPLETED"),
        (base + timedelta(minutes=60), base + timedelta(minutes=85), "COMPLETED"),
        (base + timedelta(minutes=90), base + timedelta(minutes=115), "COMPLETED"),
    ])
    assert det_abandoned.data_status == "PARTIAL"


def test_detector_invalid_datetimes(standard_pomodoro_plan):
    """Verify ValueError is raised on naive datetimes or end <= start."""
    base_naive = datetime(2026, 5, 10, 9, 0)
    base_aware = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)

    # Naive focus session start
    with pytest.raises(ValueError, match="timezone-aware"):
        detect_actual_recovery(standard_pomodoro_plan, [(base_naive, base_aware, "COMPLETED")])

    # End <= start
    with pytest.raises(ValueError, match="strictly greater"):
        detect_actual_recovery(standard_pomodoro_plan, [(base_aware, base_aware, "COMPLETED")])

    # Naive fixed event
    with pytest.raises(ValueError, match="timezone-aware"):
        detect_actual_recovery(
            standard_pomodoro_plan,
            [(base_aware, base_aware + timedelta(minutes=25), "COMPLETED")],
            fixed_events=[(base_naive, base_aware)],
        )


# ==============================================================================
# 3. Recovery Scorer Tests
# ==============================================================================

def test_scorer_unknown_status():
    """Verify UNKNOWN detection results in insufficient_data status."""
    detection = RecoveryDetection(
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
        consecutive_focus_sessions_without_long_break=0,
        long_break_after_sessions=4,
    )
    score_res = calculate_recovery_score(detection)
    assert score_res.status == "insufficient_data"
    assert score_res.score is None
    assert score_res.level is None
    assert score_res.deficit_minutes is None


def test_scorer_perfect_recovery():
    """Verify 100% recovery score and HIGH level on full recovery."""
    detection = RecoveryDetection(
        breaks=(),
        observed_recovery_minutes=15.0,
        capped_recovery_minutes=15.0,
        planned_recovery_minutes=15.0,
        missed_breaks=0,
        shortened_breaks=0,
        extended_breaks=0,
        taken_breaks=3,
        completion_ratio=1.0,
        data_status="OBSERVED",
        consecutive_focus_sessions_without_long_break=3,
        long_break_after_sessions=4,
    )
    score_res = calculate_recovery_score(detection, workload_level="MEDIUM")
    assert score_res.status == "ok"
    assert score_res.score == 100.0
    assert score_res.level == "HIGH"
    assert score_res.deficit_minutes == 0


def test_scorer_deficit_math_round_half_up():
    """Verify deficit minutes calculation using Decimal ROUND_HALF_UP."""
    # 20.0 planned - 14.5 capped = 5.5 deficit -> rounds to 6 minutes
    det_half = RecoveryDetection(
        breaks=(),
        observed_recovery_minutes=14.5,
        capped_recovery_minutes=14.5,
        planned_recovery_minutes=20.0,
        missed_breaks=0,
        shortened_breaks=1,
        extended_breaks=0,
        taken_breaks=1,
        completion_ratio=14.5 / 20.0,
        data_status="OBSERVED",
        consecutive_focus_sessions_without_long_break=2,
        long_break_after_sessions=4,
    )
    res_half = calculate_recovery_score(det_half)
    assert res_half.deficit_minutes == 6

    # 20.0 planned - 14.6 capped = 5.4 deficit -> rounds to 5 minutes
    det_down = RecoveryDetection(
        breaks=(),
        observed_recovery_minutes=14.6,
        capped_recovery_minutes=14.6,
        planned_recovery_minutes=20.0,
        missed_breaks=0,
        shortened_breaks=1,
        extended_breaks=0,
        taken_breaks=1,
        completion_ratio=14.6 / 20.0,
        data_status="OBSERVED",
        consecutive_focus_sessions_without_long_break=2,
        long_break_after_sessions=4,
    )
    res_down = calculate_recovery_score(det_down)
    assert res_down.deficit_minutes == 5


def test_scorer_cap_credit_extended_break_cannot_compensate_missed(standard_pomodoro_plan):
    """Verify that an extended break cannot compensate for a missed break."""
    base = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    # Break 1: 10 sec gap (< 60s min_break_seconds -> MISSED).
    # Break 2: 10 min gap (EXTENDED, planned is 5 min).
    sessions = [
        (base, base + timedelta(minutes=25), "COMPLETED"),
        (base + timedelta(minutes=25, seconds=10), base + timedelta(minutes=50, seconds=10), "COMPLETED"),
        (base + timedelta(minutes=60, seconds=10), base + timedelta(minutes=85, seconds=10), "COMPLETED"),
    ]
    detection = detect_actual_recovery(standard_pomodoro_plan, sessions)

    assert len(detection.breaks) == 2
    assert detection.breaks[0].classification == "MISSED"
    assert detection.breaks[1].classification == "EXTENDED"

    # Break 1: credited ~0.166 min, capped ~0.166 min, planned 5 min
    # Break 2: credited 10.0 min, capped 5.0 min (capped at planned!), planned 5 min
    assert detection.planned_recovery_minutes == 10.0
    assert detection.observed_recovery_minutes > 10.0
    assert detection.capped_recovery_minutes < 6.0
    assert detection.completion_ratio < 0.6  # NOT 1.0!

    score_res = calculate_recovery_score(detection)
    assert score_res.deficit_minutes == 5
    assert score_res.score is not None
    assert score_res.score < 60.0  # Missed break penalized despite extended break!


def test_scorer_monotonicity_properties():
    """Verify monotonicity: more recovery never lowers score; more consecutive sessions never raises it."""
    # 1. Monotonicity with respect to recovery
    scores_recovery = []
    for capped in [0.0, 5.0, 10.0, 15.0, 20.0]:
        det = RecoveryDetection(
            breaks=(),
            observed_recovery_minutes=capped,
            capped_recovery_minutes=capped,
            planned_recovery_minutes=20.0,
            missed_breaks=0,
            shortened_breaks=0,
            extended_breaks=0,
            taken_breaks=2,
            completion_ratio=capped / 20.0,
            data_status="OBSERVED",
            consecutive_focus_sessions_without_long_break=3,
            long_break_after_sessions=4,
        )
        s = calculate_recovery_score(det).score
        assert s is not None
        scores_recovery.append(s)

    for i in range(len(scores_recovery) - 1):
        assert scores_recovery[i] <= scores_recovery[i + 1]

    # 2. Monotonicity with respect to consecutive focus sessions
    scores_consecutive = []
    for streak in range(1, 10):
        det = RecoveryDetection(
            breaks=(),
            observed_recovery_minutes=15.0,
            capped_recovery_minutes=15.0,
            planned_recovery_minutes=20.0,
            missed_breaks=0,
            shortened_breaks=0,
            extended_breaks=0,
            taken_breaks=2,
            completion_ratio=15.0 / 20.0,
            data_status="OBSERVED",
            consecutive_focus_sessions_without_long_break=streak,
            long_break_after_sessions=4,
        )
        s = calculate_recovery_score(det).score
        assert s is not None
        scores_consecutive.append(s)

    for i in range(len(scores_consecutive) - 1):
        assert scores_consecutive[i] >= scores_consecutive[i + 1]


def test_scorer_workload_scaling():
    """Verify higher workload makes a deficit cost more."""
    det = RecoveryDetection(
        breaks=(),
        observed_recovery_minutes=10.0,
        capped_recovery_minutes=10.0,
        planned_recovery_minutes=20.0,
        missed_breaks=0,
        shortened_breaks=1,
        extended_breaks=0,
        taken_breaks=1,
        completion_ratio=0.5,
        data_status="OBSERVED",
        consecutive_focus_sessions_without_long_break=2,
        long_break_after_sessions=4,
    )
    score_low = calculate_recovery_score(det, workload_level="LOW").score
    score_med = calculate_recovery_score(det, workload_level="MEDIUM").score
    score_high = calculate_recovery_score(det, workload_level="HIGH").score

    assert score_low is not None and score_med is not None and score_high is not None
    assert score_low > score_med > score_high


def test_scorer_score_clamped_0_to_100():
    """Verify score is always strictly within [0.0, 100.0]."""
    # Massive penalty with 0 recovery
    det_min = RecoveryDetection(
        breaks=(),
        observed_recovery_minutes=0.0,
        capped_recovery_minutes=0.0,
        planned_recovery_minutes=20.0,
        missed_breaks=2,
        shortened_breaks=0,
        extended_breaks=0,
        taken_breaks=0,
        completion_ratio=0.0,
        data_status="OBSERVED",
        consecutive_focus_sessions_without_long_break=20,
        long_break_after_sessions=4,
    )
    res_min = calculate_recovery_score(det_min)
    assert res_min.score == 0.0
    assert res_min.level == "LOW"

    # Perfect
    det_max = RecoveryDetection(
        breaks=(),
        observed_recovery_minutes=20.0,
        capped_recovery_minutes=20.0,
        planned_recovery_minutes=20.0,
        missed_breaks=0,
        shortened_breaks=0,
        extended_breaks=0,
        taken_breaks=2,
        completion_ratio=1.0,
        data_status="OBSERVED",
        consecutive_focus_sessions_without_long_break=2,
        long_break_after_sessions=4,
    )
    res_max = calculate_recovery_score(det_max)
    assert res_max.score == 100.0
    assert res_max.level == "HIGH"


def test_scorer_invalid_workload_level():
    """Verify ValueError on invalid workload level."""
    det = RecoveryDetection(
        breaks=(),
        observed_recovery_minutes=10.0,
        capped_recovery_minutes=10.0,
        planned_recovery_minutes=10.0,
        missed_breaks=0,
        shortened_breaks=0,
        extended_breaks=0,
        taken_breaks=1,
        completion_ratio=1.0,
        data_status="OBSERVED",
        consecutive_focus_sessions_without_long_break=1,
        long_break_after_sessions=4,
    )
    with pytest.raises(ValueError, match="workload_level"):
        calculate_recovery_score(det, workload_level="CRITICAL")


def test_scorer_immutability():
    """Verify inputs to calculate_recovery_score are never mutated."""
    det = RecoveryDetection(
        breaks=(),
        observed_recovery_minutes=10.0,
        capped_recovery_minutes=10.0,
        planned_recovery_minutes=15.0,
        missed_breaks=0,
        shortened_breaks=1,
        extended_breaks=0,
        taken_breaks=1,
        completion_ratio=10.0 / 15.0,
        data_status="OBSERVED",
        consecutive_focus_sessions_without_long_break=2,
        long_break_after_sessions=4,
    )
    config = RecoveryConfig()
    det_copy = deepcopy(det)
    calculate_recovery_score(det, workload_level="HIGH", config=config)
    assert det == det_copy
