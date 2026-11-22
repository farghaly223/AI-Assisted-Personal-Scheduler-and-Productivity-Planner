"""Energy score calculation algorithm."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from app.modules.energy.algorithms.energy.config import (
    DEFAULT_ENERGY_CONFIG,
    EnergyConfig,
)
from app.modules.energy.algorithms.energy.confidence_calculator import calculate_confidence
from app.modules.energy.algorithms.energy.workload_calculator import WorkloadSummary
from app.modules.energy.algorithms.recovery.recovery_scorer import RecoveryScore
from app.modules.energy.algorithms.sleep.sleep_quality import normalize_sleep_quality


@dataclass(frozen=True)
class SleepInput:
    """Sleep input factor contract for energy scoring.

    Attributes:
        duration_minutes: Sleep duration in minutes (strict int 1..1440).
        confirmed: Whether the sleep candidate was confirmed by user.
            ONLY confirmed=True sleep sessions are counted in scoring;
            unconfirmed sessions are excluded because unconfirmed background
            signals are prone to false positives and noise.
        quality_rating: Subjective user rating (strict int 1..5) or None.
    """

    duration_minutes: int
    confirmed: bool
    quality_rating: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.duration_minutes, bool) or not isinstance(self.duration_minutes, int):
            raise ValueError(
                f"Invalid duration_minutes type: {type(self.duration_minutes).__name__}. Expected int."
            )
        if self.duration_minutes < 1 or self.duration_minutes > 1440:
            raise ValueError(
                f"duration_minutes {self.duration_minutes} out of valid range 1..1440."
            )

        if not isinstance(self.confirmed, bool):
            raise ValueError(
                f"Invalid confirmed type: {type(self.confirmed).__name__}. Expected bool."
            )

        if self.quality_rating is not None:
            if isinstance(self.quality_rating, bool) or not isinstance(self.quality_rating, int):
                raise ValueError(
                    f"Invalid quality_rating type: {type(self.quality_rating).__name__}. Expected int or None."
                )
            if self.quality_rating < 1 or self.quality_rating > 5:
                raise ValueError(
                    f"quality_rating {self.quality_rating} out of valid range 1..5."
                )


@dataclass(frozen=True)
class RecoveryInput:
    """Recovery input factor contract for energy scoring.

    Attributes:
        result: RecoveryScore from algorithms.recovery.
        data_status: RecoveryDetection data status ('OBSERVED', 'PARTIAL', 'UNKNOWN').
            Reliability is 1.0 for OBSERVED, 0.6 for PARTIAL.
            The factor is unavailable when result.status is 'insufficient_data'
            or data_status is 'UNKNOWN'.
    """

    result: RecoveryScore
    data_status: str

    def __post_init__(self) -> None:
        if not isinstance(self.result, RecoveryScore):
            raise ValueError(
                f"Invalid result type: {type(self.result).__name__}. Expected RecoveryScore."
            )
        if self.data_status not in {"OBSERVED", "PARTIAL", "UNKNOWN"}:
            raise ValueError(
                f"Invalid data_status: {self.data_status!r}. Expected OBSERVED, PARTIAL, or UNKNOWN."
            )


@dataclass(frozen=True)
class Component:
    """Breakdown of an individual factor's contribution to the EnergyScore.

    Attributes:
        name: Name of factor ('sleep', 'workload', 'recovery').
        score: Normalized factor score in [0.0, 100.0], rounded to 2 decimals.
        weight_used: Renormalized weight used in scoring (rounded to 4 decimals).
        contribution: Effective contribution points = score * weight_used (rounded to 4 decimals).
    """

    name: str
    score: float
    weight_used: float
    contribution: float


@dataclass(frozen=True)
class Insight:
    """Deterministic insight explaining the energy score result.

    Attributes:
        code: Stable machine-readable identifier.
        message: Human-readable explanation.
    """

    code: str
    message: str


@dataclass(frozen=True)
class EnergyResult:
    """Calculated overall daily energy score and constituent breakdown.

    Attributes:
        status: 'ok' or 'insufficient_data'.
        score: Aggregated energy score in [0.0, 100.0] (rounded half-up to 2 decimals),
            or None if status is 'insufficient_data'.
        level: Categorical energy level 'LOW', 'MEDIUM', 'HIGH', or None if status is 'insufficient_data'.
        confidence: Overall confidence in [0.0, 1.0] (rounded half-up to 4 decimals),
            or None if status is 'insufficient_data'.
        components: Tuple of Component instances detailing available factor contributions.
        sleep_duration_hours: Confirmed sleep duration in hours, or None if unavailable.
        sleep_quality_rating: Confirmed sleep quality rating (1..5), or None if unrated/unavailable.
        workload_level: Categorical workload level ('LOW', 'MEDIUM', 'HIGH') or None if unavailable.
        recovery_level: Categorical recovery level ('LOW', 'MEDIUM', 'HIGH') or None if unavailable.
        insights: Deterministic tuple of Insight instances in fixed priority order without duplicates.
    """

    status: str
    score: float | None
    level: str | None
    confidence: float | None
    components: tuple[Component, ...]
    sleep_duration_hours: float | None
    sleep_quality_rating: int | None
    workload_level: str | None
    recovery_level: str | None
    insights: tuple[Insight, ...]


def calculate_energy(
    sleep: SleepInput | None,
    workload: WorkloadSummary | None,
    recovery: RecoveryInput | None,
    config: EnergyConfig = DEFAULT_ENERGY_CONFIG,
) -> EnergyResult:
    """Calculate the overall daily EnergyResult.

    Args:
        sleep: Optional SleepInput instance.
        workload: Optional WorkloadSummary instance.
        recovery: Optional RecoveryInput instance.
        config: EnergyConfig instance.

    Returns:
        EnergyResult with status, score, level, confidence, components, and insights.
    """
    # --------------------------------------------------------------------------
    # 1. Sleep Factor Evaluation
    # --------------------------------------------------------------------------
    sleep_available = sleep is not None and sleep.confirmed is True
    sleep_factor_score: Decimal | None = None
    sleep_duration_hours: float | None = None
    sleep_quality_rating: int | None = None

    if sleep_available:
        assert sleep is not None  # type narrowing
        sleep_duration_hours = float(round(Decimal(sleep.duration_minutes) / Decimal("60.0"), 2))
        sleep_quality_rating = sleep.quality_rating

        dur = Decimal(sleep.duration_minutes)
        h_min = Decimal(config.sleep_hard_min)
        opt_min = Decimal(config.sleep_optimal_min)
        opt_max = Decimal(config.sleep_optimal_max)
        h_max = Decimal(config.sleep_hard_max)
        long_floor = Decimal(str(config.sleep_long_sleep_floor))

        if dur < h_min:
            dur_score = Decimal("0.0")
        elif dur < opt_min:
            dur_score = Decimal("100.0") * (dur - h_min) / (opt_min - h_min)
        elif dur <= opt_max:
            dur_score = Decimal("100.0")
        elif dur <= h_max:
            decay_slope = (Decimal("100.0") - long_floor) / (h_max - opt_max)
            dur_score = Decimal("100.0") - decay_slope * (dur - opt_max)
        else:
            dur_score = long_floor

        dur_score = min(Decimal("100.0"), max(Decimal("0.0"), dur_score))

        if sleep.quality_rating is not None:
            norm_q = normalize_sleep_quality(sleep.quality_rating)
            qual_score = Decimal(str(norm_q))
            w_q = Decimal(str(config.sleep_quality_blend))
            blended = (Decimal("1.0") - w_q) * dur_score + w_q * qual_score
            sleep_factor_score = min(Decimal("100.0"), max(Decimal("0.0"), blended))
        else:
            sleep_factor_score = dur_score

    # --------------------------------------------------------------------------
    # 2. Workload Factor Evaluation
    # --------------------------------------------------------------------------
    workload_available = (
        workload is not None
        and workload.has_data is True
        and workload.load_ratio is not None
        and not getattr(workload, "truncated", False)
    )
    workload_factor_score: Decimal | None = None
    workload_level: str | None = None

    if workload_available:
        assert workload is not None
        workload_level = workload.level
        load_ratio_dec = Decimal(str(workload.load_ratio))
        base_workload = Decimal("100.0") * (Decimal("1.0") - min(load_ratio_dec, Decimal("1.0")))

        penalty_calc = Decimal(workload.high_priority_tasks) * Decimal(str(config.high_priority_penalty_per_task))
        penalty = min(Decimal(str(config.high_priority_penalty_max)), penalty_calc)
        workload_factor_score = min(Decimal("100.0"), max(Decimal("0.0"), base_workload - penalty))

    # --------------------------------------------------------------------------
    # 3. Recovery Factor Evaluation
    # --------------------------------------------------------------------------
    recovery_available = (
        recovery is not None
        and recovery.result.status != "insufficient_data"
        and recovery.result.score is not None
        and recovery.data_status != "UNKNOWN"
    )
    recovery_factor_score: Decimal | None = None
    recovery_level: str | None = None

    if recovery_available:
        assert recovery is not None
        recovery_level = recovery.result.level
        assert recovery.result.score is not None
        recovery_factor_score = min(
            Decimal("100.0"),
            max(Decimal("0.0"), Decimal(str(recovery.result.score)))
        )

    # --------------------------------------------------------------------------
    # 4. Status and Aggregation Check
    # --------------------------------------------------------------------------
    available_factors: list[tuple[str, Decimal, Decimal]] = []
    if sleep_available and sleep_factor_score is not None:
        available_factors.append(("sleep", sleep_factor_score, Decimal(str(config.weight_sleep))))
    if workload_available and workload_factor_score is not None:
        available_factors.append(("workload", workload_factor_score, Decimal(str(config.weight_workload))))
    if recovery_available and recovery_factor_score is not None:
        available_factors.append(("recovery", recovery_factor_score, Decimal(str(config.weight_recovery))))

    available_names = {name for name, _, _ in available_factors}
    missing_required = any(req not in available_names for req in config.required_factors)
    total_available_weight = sum(w for _, _, w in available_factors)
    weight_insufficient = total_available_weight < Decimal(str(config.min_available_weight))

    status = "insufficient_data" if (missing_required or weight_insufficient) else "ok"

    # --------------------------------------------------------------------------
    # 5. Score & Component Contributions Calculation
    # --------------------------------------------------------------------------
    components_list: list[Component] = []
    final_score: float | None = None
    level: str | None = None
    confidence: float | None = None

    if status == "ok":
        unrounded_score = Decimal("0.0")
        for name, score_dec, base_weight in available_factors:
            weight_used = base_weight / total_available_weight
            contrib_exact = score_dec * weight_used
            unrounded_score += contrib_exact

            contrib_dec_4 = contrib_exact.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            components_list.append(
                Component(
                    name=name,
                    score=float(score_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    weight_used=float(weight_used.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
                    contribution=float(contrib_dec_4),
                )
            )

        rounded_score_dec = unrounded_score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        final_score = float(rounded_score_dec)

        # Categorical level derived from rounded Decimal score
        if rounded_score_dec < Decimal(str(config.level_low_threshold)):
            level = "LOW"
        elif rounded_score_dec < Decimal(str(config.level_high_threshold)):
            level = "MEDIUM"
        else:
            level = "HIGH"

        confidence = calculate_confidence(sleep, workload, recovery, config)
    else:
        # For insufficient_data, list available factors with weight_used=0.0 and contribution=0.0
        for name, score_dec, _ in available_factors:
            components_list.append(
                Component(
                    name=name,
                    score=float(score_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    weight_used=0.0,
                    contribution=0.0,
                )
            )

    # --------------------------------------------------------------------------
    # 6. Deterministic Insights Generation (Fixed Priority Order)
    # --------------------------------------------------------------------------
    insights_list: list[Insight] = []

    # 1. INSUFFICIENT_DATA
    if status == "insufficient_data":
        insights_list.append(
            Insight(
                code="INSUFFICIENT_DATA",
                message="Insufficient data to compute overall energy score",
            )
        )

    # 2. SLEEP_NO_DATA
    if sleep is None:
        insights_list.append(
            Insight(
                code="SLEEP_NO_DATA",
                message="No sleep data available for this date",
            )
        )
    # 3. SLEEP_UNCONFIRMED
    elif not sleep.confirmed:
        insights_list.append(
            Insight(
                code="SLEEP_UNCONFIRMED",
                message="Sleep session is unconfirmed and was excluded from scoring",
            )
        )
    else:
        # Confirmed sleep specific insights
        # 4. SLEEP_SHORT
        if sleep.duration_minutes < config.sleep_optimal_min:
            insights_list.append(
                Insight(
                    code="SLEEP_SHORT",
                    message="Sleep duration is below optimal range",
                )
            )
        # 5. SLEEP_LONG
        elif sleep.duration_minutes > config.sleep_optimal_max:
            insights_list.append(
                Insight(
                    code="SLEEP_LONG",
                    message="Sleep duration is above optimal range",
                )
            )

        # 6. SLEEP_QUALITY_POOR / 7. SLEEP_QUALITY_NOT_RATED
        if sleep.quality_rating is not None and sleep.quality_rating <= 2:
            insights_list.append(
                Insight(
                    code="SLEEP_QUALITY_POOR",
                    message="Reported sleep quality is poor",
                )
            )
        elif sleep.quality_rating is None:
            insights_list.append(
                Insight(
                    code="SLEEP_QUALITY_NOT_RATED",
                    message="Sleep quality was not rated",
                )
            )

    # 8. HIGH_WORKLOAD
    if workload_available and workload is not None and workload.level == "HIGH":
        insights_list.append(
            Insight(
                code="HIGH_WORKLOAD",
                message="High workload detected",
            )
        )
    # 9. WORKLOAD_NO_DATA / WORKLOAD_TRUNCATED
    if not workload_available:
        if getattr(workload, "truncated", False):
            insights_list.append(
                Insight(
                    code="WORKLOAD_TRUNCATED",
                    message="Workload tasks or events exceeded limit and could not be analyzed",
                )
            )
        else:
            insights_list.append(
                Insight(
                    code="WORKLOAD_NO_DATA",
                    message="No workload data available for this date",
                )
            )

    # 10. RECOVERY_INSUFFICIENT
    if recovery_available and recovery is not None:
        deficit = recovery.result.deficit_minutes
        is_low_level = recovery.result.level == "LOW"
        is_large_deficit = (
            deficit is not None and deficit >= config.recovery_deficit_insight_minutes
        )
        if is_low_level or is_large_deficit:
            insights_list.append(
                Insight(
                    code="RECOVERY_INSUFFICIENT",
                    message="Recovery time is insufficient",
                )
            )
    # 11. RECOVERY_NO_DATA
    if not recovery_available:
        insights_list.append(
            Insight(
                code="RECOVERY_NO_DATA",
                message="No recovery data available for this date",
            )
        )

    # 12. LOW_CONFIDENCE
    if status == "ok" and confidence is not None and confidence < config.low_confidence_threshold:
        insights_list.append(
            Insight(
                code="LOW_CONFIDENCE",
                message="Energy score confidence is low due to limited data",
            )
        )

    return EnergyResult(
        status=status,
        score=final_score,
        level=level,
        confidence=confidence,
        components=tuple(components_list),
        sleep_duration_hours=sleep_duration_hours,
        sleep_quality_rating=sleep_quality_rating,
        workload_level=workload_level,
        recovery_level=recovery_level,
        insights=tuple(insights_list),
    )
