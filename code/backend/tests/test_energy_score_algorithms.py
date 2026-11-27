"""Unit tests for Energy module energy and workload algorithms.

Plain unit tests only; no database fixtures, network connections, or Supabase.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from app.modules.energy.algorithms.energy import (
    ALGORITHM_VERSION,
    DEFAULT_ENERGY_CONFIG,
    Component,
    EnergyConfig,
    EnergyResult,
    Insight,
    RecoveryInput,
    SleepInput,
    TaskLoad,
    WorkloadSummary,
    calculate_confidence,
    calculate_energy,
    calculate_workload,
)
from app.modules.energy.algorithms.recovery.recovery_scorer import RecoveryScore


# ==============================================================================
# 1. Workload Calculator Tests
# ==============================================================================

def test_workload_empty_is_no_data():
    """Empty tasks and events must return has_data=False, not a light day."""
    day_start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc)

    summary = calculate_workload([], [], day_start, day_end)
    assert summary.has_data is False
    assert summary.load_ratio is None
    assert summary.level is None
    assert summary.total_task_minutes == 0
    assert summary.completed_task_minutes == 0
    assert summary.fixed_event_minutes == 0
    assert summary.high_priority_tasks == 0


def test_workload_completed_propagation():
    """completed_task_minutes is None if any task has completed=None."""
    day_start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc)

    # All completed known
    tasks_known = [
        TaskLoad(duration_minutes=60, priority=3, completed=True),
        TaskLoad(duration_minutes=30, priority=2, completed=False),
        TaskLoad(duration_minutes=45, priority=4, completed=True),
    ]
    summary_known = calculate_workload(tasks_known, [], day_start, day_end)
    assert summary_known.has_data is True
    assert summary_known.total_task_minutes == 135
    assert summary_known.completed_task_minutes == 105
    assert summary_known.high_priority_tasks == 1

    # One completed=None -> propagation
    tasks_unknown = [
        TaskLoad(duration_minutes=60, priority=3, completed=True),
        TaskLoad(duration_minutes=30, priority=2, completed=None),
    ]
    summary_unknown = calculate_workload(tasks_unknown, [], day_start, day_end)
    assert summary_unknown.has_data is True
    assert summary_unknown.total_task_minutes == 90
    assert summary_unknown.completed_task_minutes is None


def test_workload_events_overlapping_union():
    """Overlapping and adjacent events must be merged without double counting."""
    day_start = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 5, 1, 20, 0, tzinfo=timezone.utc)

    events = [
        (datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc), datetime(2026, 5, 1, 10, 30, tzinfo=timezone.utc)),  # 90m
        (datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc), datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc)),   # overlaps -> 9:00..11:00 (120m)
        (datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc), datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc)),   # 60m
    ]
    # Total merged events = 120 + 60 = 180m
    summary = calculate_workload([], events, day_start, day_end)
    assert summary.fixed_event_minutes == 180


def test_workload_events_clipped_to_day():
    """Events extending beyond day boundaries are clipped; outside events ignored."""
    day_start = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc)

    events = [
        # Crosses day_start: 07:00..09:00 -> clipped to 08:00..09:00 (60m)
        (datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc), datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)),
        # Crosses day_end: 17:30..19:00 -> clipped to 17:30..18:00 (30m)
        (datetime(2026, 5, 1, 17, 30, tzinfo=timezone.utc), datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc)),
        # Completely outside: 05:00..06:00 -> ignored (0m)
        (datetime(2026, 5, 1, 5, 0, tzinfo=timezone.utc), datetime(2026, 5, 1, 6, 0, tzinfo=timezone.utc)),
        # Completely outside: 19:00..20:00 -> ignored (0m)
        (datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc), datetime(2026, 5, 1, 20, 0, tzinfo=timezone.utc)),
    ]
    summary = calculate_workload([], events, day_start, day_end)
    assert summary.fixed_event_minutes == 90


def test_workload_load_ratio_cap_and_levels():
    """Check ratio capping at max_load_ratio and exact level boundaries."""
    day_start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc)
    config = EnergyConfig(available_minutes=1000, max_load_ratio=1.5, workload_low_threshold=0.40, workload_medium_threshold=0.75)

    # 399 minutes -> ratio 0.399 -> LOW (< 0.40)
    s_low = calculate_workload([TaskLoad(399, 1)], [], day_start, day_end, config)
    assert s_low.level == "LOW"
    assert s_low.load_ratio == 0.399

    # Exactly 400 minutes -> ratio 0.400 -> MEDIUM
    s_med_boundary = calculate_workload([TaskLoad(400, 1)], [], day_start, day_end, config)
    assert s_med_boundary.level == "MEDIUM"
    assert s_med_boundary.load_ratio == 0.40

    # 749 minutes -> ratio 0.749 -> MEDIUM (< 0.75)
    s_med = calculate_workload([TaskLoad(749, 1)], [], day_start, day_end, config)
    assert s_med.level == "MEDIUM"
    assert s_med.load_ratio == 0.749

    # Exactly 750 minutes -> ratio 0.750 -> HIGH
    s_high_boundary = calculate_workload([TaskLoad(750, 1)], [], day_start, day_end, config)
    assert s_high_boundary.level == "HIGH"
    assert s_high_boundary.load_ratio == 0.75

    # 2000 minutes -> 2.0 capped at 1.5
    s_capped = calculate_workload([TaskLoad(1440, 1), TaskLoad(560, 1)], [], day_start, day_end, config)
    assert s_capped.level == "HIGH"
    assert s_capped.load_ratio == 1.5


@pytest.mark.parametrize("invalid_task", [
    {"duration_minutes": 0, "priority": 3},
    {"duration_minutes": -10, "priority": 3},
    {"duration_minutes": 1441, "priority": 3},
    {"duration_minutes": True, "priority": 3},
    {"duration_minutes": 60.5, "priority": 3},
    {"duration_minutes": 60, "priority": 0},
    {"duration_minutes": 60, "priority": 6},
    {"duration_minutes": 60, "priority": True},
    {"duration_minutes": 60, "priority": 3, "completed": "yes"},
])
def test_task_load_invalid_inputs(invalid_task):
    with pytest.raises(ValueError):
        TaskLoad(**invalid_task)


def test_workload_naive_datetimes():
    """Naive datetimes must raise ValueError."""
    naive_start = datetime(2026, 5, 1, 0, 0)
    aware_end = datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_workload([], [], naive_start, aware_end)

    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_workload([], [], aware_end, naive_start)

    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_workload([], [(naive_start, aware_end)], aware_end, aware_end + timedelta(days=1))


def test_workload_invalid_intervals():
    """Event end <= start or day_end <= day_start raises ValueError."""
    t1 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="day_end"):
        calculate_workload([], [], t1, t2)

    with pytest.raises(ValueError, match="Event end"):
        calculate_workload([], [(t1, t2)], t2, t1 + timedelta(days=1))


# ==============================================================================
# 2. Sleep Component Tests
# ==============================================================================

@pytest.mark.parametrize("invalid_sleep", [
    {"duration_minutes": 0, "confirmed": True},
    {"duration_minutes": -5, "confirmed": True},
    {"duration_minutes": 1441, "confirmed": True},
    {"duration_minutes": True, "confirmed": True},
    {"duration_minutes": 480, "confirmed": "yes"},
    {"duration_minutes": 480, "confirmed": True, "quality_rating": 0},
    {"duration_minutes": 480, "confirmed": True, "quality_rating": 6},
    {"duration_minutes": 480, "confirmed": True, "quality_rating": True},
    {"duration_minutes": 480, "confirmed": True, "quality_rating": 4.5},
])
def test_sleep_input_validation(invalid_sleep):
    with pytest.raises(ValueError):
        SleepInput(**invalid_sleep)


def test_sleep_unconfirmed_treated_as_missing():
    """confirmed=False must result in status insufficient_data."""
    unconfirmed = SleepInput(duration_minutes=480, confirmed=False, quality_rating=4)
    res = calculate_energy(sleep=unconfirmed, workload=None, recovery=None)
    assert res.status == "insufficient_data"
    assert res.score is None
    assert res.level is None
    assert res.confidence is None
    assert any(i.code == "SLEEP_UNCONFIRMED" for i in res.insights)


def test_workload_available_minutes_override_and_validation():
    """calculate_workload accepts optional available_minutes (1..1440, strict int) and extra_high_priority_tasks."""
    day_start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc)

    # 300m over 600 available_minutes -> ratio 0.50 -> MEDIUM
    s_override = calculate_workload(
        [TaskLoad(300, 3)],
        [],
        day_start,
        day_end,
        available_minutes=600,
    )
    assert s_override.load_ratio == 0.5
    assert s_override.level == "MEDIUM"

    # Invalid available_minutes rejected
    for bad_avail in (0, -10, 1441, True, False, 960.5, "960"):
        with pytest.raises(ValueError, match="available_minutes"):
            calculate_workload([], [], day_start, day_end, available_minutes=bad_avail)  # type: ignore[arg-type]

    # Invalid extra_high_priority_tasks rejected
    for bad_extra in (-1, True, "1"):
        with pytest.raises(ValueError, match="extra_high_priority_tasks"):
            calculate_workload([], [], day_start, day_end, extra_high_priority_tasks=bad_extra)  # type: ignore[arg-type]


def test_workload_fixed_intervals_and_extra_high_priority_only():
    """FIXED task intervals alone or extra_high_priority_tasks alone still mark has_data=True."""
    day_start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc)

    fixed_interval = (
        datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
    )
    summary = calculate_workload(
        [],
        [fixed_interval],
        day_start,
        day_end,
        extra_high_priority_tasks=1,
    )
    assert summary.has_data is True
    assert summary.total_task_minutes == 0
    assert summary.fixed_event_minutes == 120
    assert summary.high_priority_tasks == 1


def test_sleep_piecewise_curve_boundaries():
    """Verify piecewise linear duration scoring at exact points: <=180, 300, 420, 540, 600, 660, 840, 900."""
    assert ALGORITHM_VERSION == "energy-algo-2"

    # Under hard_min (180): 0.0
    r179 = calculate_energy(SleepInput(179, confirmed=True), None, None)
    assert r179.score == 0.0

    # Exactly hard_min (180): 0.0
    r180 = calculate_energy(SleepInput(180, confirmed=True), None, None)
    assert r180.score == 0.0

    # Midpoint 300 min: 100 * (300 - 180) / (420 - 180) = 100 * 120 / 240 = 50.0
    r300 = calculate_energy(SleepInput(300, confirmed=True), None, None)
    assert r300.score == 50.0

    # Optimal min (420): 100.0
    r420 = calculate_energy(SleepInput(420, confirmed=True), None, None)
    assert r420.score == 100.0

    # Optimal max (540): 100.0
    r540 = calculate_energy(SleepInput(540, confirmed=True), None, None)
    assert r540.score == 100.0

    # Midpoint above optimal: 600 min: 100 - (100 - 85) * (600 - 540) / (660 - 540) = 100 - 15 * 60 / 120 = 92.5
    r600 = calculate_energy(SleepInput(600, confirmed=True), None, None)
    assert r600.score == 92.5

    # Long-sleep floor boundary (660): floor 85.0
    r660 = calculate_energy(SleepInput(660, confirmed=True), None, None)
    assert r660.score == 85.0

    # Beyond 660 (840, 900): flat 85.0 floor
    r840 = calculate_energy(SleepInput(840, confirmed=True), None, None)
    assert r840.score == 85.0
    r900 = calculate_energy(SleepInput(900, confirmed=True), None, None)
    assert r900.score == 85.0


def test_sleep_quality_blending():
    """Verify quality blending formula: 0.7 * duration_score + 0.3 * quality_score."""
    # Duration = 480 (optimal -> 100). Rating = 1 -> quality = 0.0
    # Score = 0.7 * 100 + 0.3 * 0 = 70.0
    r_q1 = calculate_energy(SleepInput(480, confirmed=True, quality_rating=1), None, None)
    assert r_q1.score == 70.0

    # Duration = 480 (100). Rating = 3 -> quality = 50.0
    # Score = 0.7 * 100 + 0.3 * 50 = 85.0
    r_q3 = calculate_energy(SleepInput(480, confirmed=True, quality_rating=3), None, None)
    assert r_q3.score == 85.0

    # Duration = 480 (100). Rating = 5 -> quality = 100.0
    # Score = 0.7 * 100 + 0.3 * 100 = 100.0
    r_q5 = calculate_energy(SleepInput(480, confirmed=True, quality_rating=5), None, None)
    assert r_q5.score == 100.0

    # Missing rating is never 0 -> pure duration_score = 100.0
    r_unrated = calculate_energy(SleepInput(480, confirmed=True, quality_rating=None), None, None)
    assert r_unrated.score == 100.0


# ==============================================================================
# 3. Recovery Input Validation Tests
# ==============================================================================

def test_recovery_input_validation():
    valid_score = RecoveryScore(score=80.0, level="HIGH", deficit_minutes=0, status="ok")
    # Valid
    rec = RecoveryInput(result=valid_score, data_status="OBSERVED")
    assert rec.data_status == "OBSERVED"

    # Invalid result type
    with pytest.raises(ValueError, match="RecoveryScore"):
        RecoveryInput(result={"score": 80.0}, data_status="OBSERVED")  # type: ignore

    # Invalid data_status
    with pytest.raises(ValueError, match="data_status"):
        RecoveryInput(result=valid_score, data_status="INVALID")


# ==============================================================================
# 4. Energy Score & Level Rounding Tests
# ==============================================================================

def test_energy_all_factors_ok():
    """Verify calculation when all factors (sleep, workload, recovery) are available."""
    sleep = SleepInput(duration_minutes=480, confirmed=True, quality_rating=5)  # 100.0
    workload = WorkloadSummary(
        total_task_minutes=240,
        completed_task_minutes=240,
        fixed_event_minutes=0,
        high_priority_tasks=0,
        load_ratio=0.25,  # 100 * (1 - 0.25) = 75.0
        level="LOW",
        has_data=True,
    )
    recovery = RecoveryInput(
        result=RecoveryScore(score=80.0, level="HIGH", deficit_minutes=0, status="ok"),
        data_status="OBSERVED",
    )

    # Weights: sleep 0.50, workload 0.25, recovery 0.25 (sum = 1.0)
    # Expected: 0.5 * 100 + 0.25 * 75 + 0.25 * 80 = 50 + 18.75 + 20 = 88.75
    res = calculate_energy(sleep, workload, recovery)
    assert res.status == "ok"
    assert res.score == 88.75
    assert res.level == "HIGH"
    assert res.confidence == 1.0
    assert len(res.components) == 3


def test_energy_sleep_only_renormalized():
    """Sleep-only input must renormalize sleep weight to 1.0 and return status ok."""
    sleep = SleepInput(duration_minutes=420, confirmed=True, quality_rating=3)  # score: 0.7*100 + 0.3*50 = 85.0
    res = calculate_energy(sleep=sleep, workload=None, recovery=None)

    assert res.status == "ok"
    assert res.score == 85.0
    assert res.level == "HIGH"
    assert len(res.components) == 1
    assert res.components[0].name == "sleep"
    assert res.components[0].weight_used == 1.0
    assert res.components[0].contribution == 85.0
    # Confidence: 0.5 * 1.0 = 0.50 (rated sleep)
    assert res.confidence == 0.50
    assert any(i.code == "LOW_CONFIDENCE" for i in res.insights)


def test_energy_missing_sleep_insufficient_data():
    """When sleep is missing, status must be insufficient_data."""
    workload = WorkloadSummary(
        total_task_minutes=240, completed_task_minutes=240, fixed_event_minutes=0,
        high_priority_tasks=0, load_ratio=0.25, level="LOW", has_data=True,
    )
    recovery = RecoveryInput(
        result=RecoveryScore(score=80.0, level="HIGH", deficit_minutes=0, status="ok"),
        data_status="OBSERVED",
    )

    res = calculate_energy(sleep=None, workload=workload, recovery=recovery)
    assert res.status == "insufficient_data"
    assert res.score is None
    assert res.level is None
    assert res.confidence is None
    # Still lists available components with weight_used=0.0 and contribution=0.0
    assert len(res.components) == 2
    for comp in res.components:
        assert comp.weight_used == 0.0
        assert comp.contribution == 0.0
    assert any(i.code == "INSUFFICIENT_DATA" for i in res.insights)
    assert any(i.code == "SLEEP_NO_DATA" for i in res.insights)


def test_energy_level_rounding_boundaries():
    """Test exact rounding half-up and level boundaries (39.995 -> 40.00 MEDIUM, 74.995 -> 75.00 HIGH)."""
    # Equal weights: sleep 0.5, workload 0.25, recovery 0.25
    # score = 0.5 * S + 0.25 * W + 0.25 * R
    # To get 74.995:
    # 0.5 * 100 + 0.25 * 50 + 0.25 * 49.98 = 50 + 12.5 + 12.495 = 74.995
    # S = 100.0 (duration 480, rating 5)
    # W = 50.0 (load_ratio = 0.5)
    # R = 49.98
    sleep = SleepInput(duration_minutes=480, confirmed=True, quality_rating=5)
    workload = WorkloadSummary(
        total_task_minutes=480, completed_task_minutes=480, fixed_event_minutes=0,
        high_priority_tasks=0, load_ratio=0.5, level="MEDIUM", has_data=True,
    )
    rec_74_995 = RecoveryInput(
        result=RecoveryScore(score=49.98, level="MEDIUM", deficit_minutes=0, status="ok"),
        data_status="OBSERVED",
    )
    res_74_995 = calculate_energy(sleep, workload, rec_74_995)
    # 50 + 12.5 + 12.495 = 74.995 -> ROUND_HALF_UP -> 75.00 -> HIGH
    assert res_74_995.score == 75.00
    assert res_74_995.level == "HIGH"

    # Now slightly below: R = 49.97 -> 50 + 12.5 + 12.4925 = 74.9925 -> 74.99 -> MEDIUM
    rec_74_9925 = RecoveryInput(
        result=RecoveryScore(score=49.97, level="MEDIUM", deficit_minutes=0, status="ok"),
        data_status="OBSERVED",
    )
    res_74_9925 = calculate_energy(sleep, workload, rec_74_9925)
    assert res_74_9925.score == 74.99
    assert res_74_9925.level == "MEDIUM"

    # Exactly 40.00:
    # 0.5 * 40 + 0.25 * 40 + 0.25 * 40 = 40.00 -> MEDIUM
    # To get 39.995:
    # S = 40.0 (duration: 180 + 0.40 * 240 = 276 min, unrated)
    # W = 40.0 (load_ratio = 0.6)
    # R = 39.98 -> 0.5*40 + 0.25*40 + 0.25*39.98 = 20 + 10 + 9.995 = 39.995 -> 40.00 -> MEDIUM
    sleep_40 = SleepInput(duration_minutes=276, confirmed=True, quality_rating=None)
    workload_40 = WorkloadSummary(
        total_task_minutes=576, completed_task_minutes=576, fixed_event_minutes=0,
        high_priority_tasks=0, load_ratio=0.6, level="MEDIUM", has_data=True,
    )
    rec_39_995 = RecoveryInput(
        result=RecoveryScore(score=39.98, level="LOW", deficit_minutes=15, status="ok"),
        data_status="OBSERVED",
    )
    res_39_995 = calculate_energy(sleep_40, workload_40, rec_39_995)
    assert res_39_995.score == 40.00
    assert res_39_995.level == "MEDIUM"

    # Slightly below: R = 39.97 -> 20 + 10 + 9.9925 = 39.9925 -> 39.99 -> LOW
    rec_39_9925 = RecoveryInput(
        result=RecoveryScore(score=39.97, level="LOW", deficit_minutes=15, status="ok"),
        data_status="OBSERVED",
    )
    res_39_9925 = calculate_energy(sleep_40, workload_40, rec_39_9925)
    assert res_39_9925.score == 39.99
    assert res_39_9925.level == "LOW"


def test_energy_components_contribution_sum():
    """Component contributions sum must equal rounded score within 0.01 and unrounded score within 0.0001."""
    sleep = SleepInput(duration_minutes=430, confirmed=True, quality_rating=4)
    workload = WorkloadSummary(
        total_task_minutes=350, completed_task_minutes=350, fixed_event_minutes=45,
        high_priority_tasks=1, load_ratio=0.4115, level="MEDIUM", has_data=True,
    )
    recovery = RecoveryInput(
        result=RecoveryScore(score=63.33, level="MEDIUM", deficit_minutes=5, status="ok"),
        data_status="PARTIAL",
    )

    res = calculate_energy(sleep, workload, recovery)
    assert res.score is not None

    contrib_sum = sum(c.contribution for c in res.components)
    # Sum equals rounded score within 0.01
    assert abs(contrib_sum - res.score) <= 0.01

    # Reconstruct unrounded score with Decimal:
    weights = {"sleep": Decimal("0.5"), "workload": Decimal("0.25"), "recovery": Decimal("0.25")}
    unrounded = sum(Decimal(str(c.score)) * weights[c.name] for c in res.components)
    assert abs(Decimal(str(contrib_sum)) - unrounded) <= Decimal("0.0002")


def test_energy_inputs_not_mutated():
    """calculate_energy must never mutate its inputs."""
    sleep = SleepInput(duration_minutes=450, confirmed=True, quality_rating=3)
    workload = WorkloadSummary(
        total_task_minutes=200, completed_task_minutes=200, fixed_event_minutes=60,
        high_priority_tasks=2, load_ratio=0.27, level="LOW", has_data=True,
    )
    recovery = RecoveryInput(
        result=RecoveryScore(score=75.0, level="HIGH", deficit_minutes=0, status="ok"),
        data_status="OBSERVED",
    )

    s_copy = deepcopy(sleep)
    w_copy = deepcopy(workload)
    r_copy = deepcopy(recovery)

    calculate_energy(sleep, workload, recovery)

    assert sleep == s_copy
    assert workload == w_copy
    assert recovery == r_copy


def test_energy_config_validation():
    """EnergyConfig validates weights positive, summing to 1.0, and valid factors."""
    with pytest.raises(ValueError, match="sum to 1.0"):
        EnergyConfig(weight_sleep=0.6, weight_workload=0.3, weight_recovery=0.3)

    with pytest.raises(ValueError, match="positive"):
        EnergyConfig(weight_sleep=1.0, weight_workload=0.0, weight_recovery=0.0)

    with pytest.raises(ValueError, match="Invalid required factor"):
        EnergyConfig(required_factors=("invalid_factor",))


# ==============================================================================
# 5. Confidence Calculator Tests
# ==============================================================================

def test_confidence_calculations():
    """Verify confidence with full data, partial data, and data_status transitions."""
    sleep_rated = SleepInput(480, confirmed=True, quality_rating=4)
    sleep_unrated = SleepInput(480, confirmed=True, quality_rating=None)
    workload_known = WorkloadSummary(100, 100, 0, 0, 0.1, "LOW", True)
    workload_unknown = WorkloadSummary(100, None, 0, 0, 0.1, "LOW", True)
    rec_obs = RecoveryInput(RecoveryScore(80.0, "HIGH", 0, "ok"), "OBSERVED")
    rec_part = RecoveryInput(RecoveryScore(80.0, "HIGH", 0, "ok"), "PARTIAL")
    rec_unk = RecoveryInput(RecoveryScore(None, None, None, "insufficient_data"), "UNKNOWN")

    # Full data: rated sleep (1.0), known workload (1.0), observed recovery (1.0)
    # 0.5 * 1.0 + 0.25 * 1.0 + 0.25 * 1.0 = 1.0
    assert calculate_confidence(sleep_rated, workload_known, rec_obs) == 1.0000

    # Partial reliabilities: unrated sleep (0.8), unknown workload (0.7), partial recovery (0.6)
    # 0.5 * 0.8 + 0.25 * 0.7 + 0.25 * 0.6 = 0.40 + 0.175 + 0.150 = 0.725
    assert calculate_confidence(sleep_unrated, workload_unknown, rec_part) == 0.7250

    # Sleep only (unrated): 0.5 * 0.8 = 0.40
    assert calculate_confidence(sleep_unrated, None, None) == 0.4000

    # Sleep only (rated): 0.5 * 1.0 = 0.50
    assert calculate_confidence(sleep_rated, None, None) == 0.5000

    # UNKNOWN recovery contributes 0
    # 0.5 * 1.0 + 0.25 * 1.0 + 0.25 * 0 = 0.75
    assert calculate_confidence(sleep_rated, workload_known, rec_unk) == 0.7500


# ==============================================================================
# 6. Insights Tests
# ==============================================================================

def test_insights_all_codes_and_order():
    """Verify triggers, texts, and exact ordering of insights."""
    # Test HIGH_WORKLOAD and RECOVERY_INSUFFICIENT exact texts
    sleep = SleepInput(duration_minutes=350, confirmed=True, quality_rating=2)  # SLEEP_SHORT, SLEEP_QUALITY_POOR
    workload = WorkloadSummary(
        total_task_minutes=800, completed_task_minutes=800, fixed_event_minutes=0,
        high_priority_tasks=0, load_ratio=0.85, level="HIGH", has_data=True,
    )  # HIGH_WORKLOAD
    recovery = RecoveryInput(
        result=RecoveryScore(score=35.0, level="LOW", deficit_minutes=25, status="ok"),  # RECOVERY_INSUFFICIENT
        data_status="OBSERVED",
    )

    res = calculate_energy(sleep, workload, recovery)
    codes = [i.code for i in res.insights]

    # Verify exact required text:
    sleep_short_insight = next(i for i in res.insights if i.code == "SLEEP_SHORT")
    assert sleep_short_insight.message == "Sleep duration is below 7 hours"

    high_wl_insight = next(i for i in res.insights if i.code == "HIGH_WORKLOAD")
    assert high_wl_insight.message == "High workload detected"

    rec_insuf_insight = next(i for i in res.insights if i.code == "RECOVERY_INSUFFICIENT")
    assert rec_insuf_insight.message == "Recovery time is insufficient"

    # Verify order: SLEEP_SHORT -> SLEEP_QUALITY_POOR -> HIGH_WORKLOAD -> RECOVERY_INSUFFICIENT
    assert codes == ["SLEEP_SHORT", "SLEEP_QUALITY_POOR", "HIGH_WORKLOAD", "RECOVERY_INSUFFICIENT"]


def test_insights_sleep_long_and_unrated():
    sleep = SleepInput(duration_minutes=600, confirmed=True, quality_rating=None)
    res = calculate_energy(sleep, None, None)
    codes = [i.code for i in res.insights]
    assert "SLEEP_LONG" in codes
    sleep_long_insight = next(i for i in res.insights if i.code == "SLEEP_LONG")
    assert sleep_long_insight.message == "Sleep duration is above 9 hours"
    assert "SLEEP_QUALITY_NOT_RATED" in codes
    assert "WORKLOAD_NO_DATA" in codes
    assert "RECOVERY_NO_DATA" in codes
    assert "LOW_CONFIDENCE" in codes


def test_insights_insufficient_data_reasons():
    """Insights on insufficient_data must explain which factors are missing/unconfirmed."""
    # Case A: Sleep is None
    res_no_sleep = calculate_energy(None, None, None)
    codes_a = [i.code for i in res_no_sleep.insights]
    assert codes_a[0] == "INSUFFICIENT_DATA"
    assert "SLEEP_NO_DATA" in codes_a
    assert "WORKLOAD_NO_DATA" in codes_a
    assert "RECOVERY_NO_DATA" in codes_a

    # Case B: Sleep is unconfirmed
    res_unconf = calculate_energy(SleepInput(480, confirmed=False), None, None)
    codes_b = [i.code for i in res_unconf.insights]
    assert codes_b[0] == "INSUFFICIENT_DATA"
    assert "SLEEP_UNCONFIRMED" in codes_b
    assert "WORKLOAD_NO_DATA" in codes_b
    assert "RECOVERY_NO_DATA" in codes_b


def test_insights_no_duplicates():
    """Ensure no insight code is duplicated in the output."""
    sleep = SleepInput(duration_minutes=480, confirmed=True, quality_rating=4)
    res = calculate_energy(sleep, None, None)
    codes = [i.code for i in res.insights]
    assert len(codes) == len(set(codes))


# ==============================================================================
# 7. Monotonicity Tests (Grids & Invariants)
# ==============================================================================

def test_monotonicity_sleep_duration_rising():
    """More sleep within [180, 420] never lowers the score."""
    scores = []
    for dur in range(180, 425, 15):
        sleep = SleepInput(duration_minutes=dur, confirmed=True, quality_rating=None)
        res = calculate_energy(sleep, None, None)
        assert res.score is not None
        scores.append(res.score)

    for i in range(len(scores) - 1):
        assert scores[i + 1] >= scores[i], f"Score lowered from {scores[i]} to {scores[i+1]}"


def test_monotonicity_sleep_quality_rising():
    """A higher rating (1..5) never lowers the score."""
    scores = []
    for rating in range(1, 6):
        sleep = SleepInput(duration_minutes=480, confirmed=True, quality_rating=rating)
        res = calculate_energy(sleep, None, None)
        assert res.score is not None
        scores.append(res.score)

    for i in range(len(scores) - 1):
        assert scores[i + 1] >= scores[i], f"Score lowered from {scores[i]} to {scores[i+1]}"


def test_monotonicity_workload_ratio_rising():
    """A higher load_ratio never raises the energy score."""
    sleep = SleepInput(duration_minutes=480, confirmed=True, quality_rating=4)
    scores = []
    for ratio_x10 in range(0, 15, 1):
        ratio = ratio_x10 / 10.0
        workload = WorkloadSummary(
            total_task_minutes=int(ratio * 960), completed_task_minutes=int(ratio * 960),
            fixed_event_minutes=0, high_priority_tasks=0, load_ratio=ratio,
            level="LOW" if ratio < 0.4 else "MEDIUM", has_data=True,
        )
        res = calculate_energy(sleep, workload, None)
        assert res.score is not None
        scores.append(res.score)

    for i in range(len(scores) - 1):
        assert scores[i + 1] <= scores[i], f"Score raised from {scores[i]} to {scores[i+1]} when workload increased"


def test_monotonicity_recovery_score_rising():
    """A better recovery score never lowers the energy score."""
    sleep = SleepInput(duration_minutes=480, confirmed=True, quality_rating=4)
    scores = []
    for s in range(0, 105, 10):
        rec = RecoveryInput(
            result=RecoveryScore(score=float(s), level="HIGH" if s >= 70 else "MEDIUM", deficit_minutes=0, status="ok"),
            data_status="OBSERVED",
        )
        res = calculate_energy(sleep, None, rec)
        assert res.score is not None
        scores.append(res.score)

    for i in range(len(scores) - 1):
        assert scores[i + 1] >= scores[i], f"Score lowered from {scores[i]} to {scores[i+1]} when recovery improved"


def test_monotonicity_confidence_adding_factors():
    """Adding an available factor never makes confidence lower."""
    sleep = SleepInput(duration_minutes=480, confirmed=True, quality_rating=4)
    workload = WorkloadSummary(100, 100, 0, 0, 0.1, "LOW", True)
    recovery = RecoveryInput(
        result=RecoveryScore(score=80.0, level="HIGH", deficit_minutes=0, status="ok"),
        data_status="OBSERVED",
    )

    c0 = calculate_confidence(None, None, None)
    c1 = calculate_confidence(sleep, None, None)
    c2 = calculate_confidence(sleep, workload, None)
    c3 = calculate_confidence(sleep, workload, recovery)

    assert c0 <= c1 <= c2 <= c3
