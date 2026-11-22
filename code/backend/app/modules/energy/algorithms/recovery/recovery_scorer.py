"""Recovery score calculation algorithm.

SCORING FORMULA:
1. When data_status is 'UNKNOWN':
   - score = None
   - level = None
   - deficit_minutes = None
   - status = 'insufficient_data'

2. Deficit Minutes:
   - deficit_raw = max(0, planned_recovery_minutes - capped_recovery_minutes)
   - deficit_minutes = ROUND_HALF_UP(deficit_raw) as integer

3. Completion Ratio & Relative Deficit:
   - capped_ratio = clamp(0.0, 1.0, detection.completion_ratio)
   - relative_deficit = 1.0 - capped_ratio  (in [0.0, 1.0])

4. Workload Scaling:
   - Higher workload increases deficit cost:
     effective_deficit = min(1.0, relative_deficit * workload_multipliers[workload_level])
     where multipliers are LOW=0.8, MEDIUM=1.0, HIGH=1.25, None=1.0
   - Base Score = 100.0 * (1.0 - effective_deficit)

5. Consecutive Focus Sessions Penalty:
   - excess = max(0, consecutive_focus_sessions - threshold)
   - penalty = min(max_consecutive_penalty, excess * penalty_per_excess_session)
   - Final Score = clamp(0.0, 100.0, Base Score - penalty)

6. Level Thresholds:
   - score >= 70.0: 'HIGH'
   - score >= 40.0: 'MEDIUM'
   - score < 40.0: 'LOW'

Monotonicity guarantees:
- More observed recovery increases capped_ratio, decreasing deficit, which never lowers the score.
- More consecutive focus sessions without a long break increases penalty, which never raises the score.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.modules.energy.algorithms.recovery.config import (
    DEFAULT_RECOVERY_CONFIG,
    RecoveryConfig,
)
from app.modules.energy.algorithms.recovery.recovery_detector import RecoveryDetection


@dataclass(frozen=True)
class RecoveryScore:
    """Calculated recovery score, categorical level, and deficit.

    Attributes:
        score: Normalized recovery score in [0.0, 100.0], or None if insufficient data.
        level: Categorical level 'LOW', 'MEDIUM', 'HIGH', or None if insufficient data.
        deficit_minutes: Whole minutes of recovery deficit, or None if insufficient data.
        status: 'ok' or 'insufficient_data'.
    """

    score: float | None
    level: str | None
    deficit_minutes: int | None
    status: str


VALID_WORKLOAD_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", None})


def calculate_recovery_score(
    detection: RecoveryDetection,
    workload_level: str | None = None,
    config: RecoveryConfig = DEFAULT_RECOVERY_CONFIG,
) -> RecoveryScore:
    """Calculate the deterministic recovery score from detection results.

    Args:
        detection: RecoveryDetection output from detect_actual_recovery.
        workload_level: Workload level in 'LOW', 'MEDIUM', 'HIGH', or None.
        config: Optional RecoveryConfig.

    Returns:
        RecoveryScore with score in [0.0, 100.0], level, deficit_minutes, and status.

    Raises:
        ValueError: If workload_level is invalid.
    """
    if workload_level not in VALID_WORKLOAD_LEVELS:
        raise ValueError(
            f"Invalid workload_level: {workload_level!r}. Expected one of {sorted(repr(k) for k in VALID_WORKLOAD_LEVELS)}"
        )

    # 1. Handle insufficient data (UNKNOWN data status)
    if detection.data_status == "UNKNOWN":
        return RecoveryScore(
            score=None,
            level=None,
            deficit_minutes=None,
            status="insufficient_data",
        )

    # 2. Deficit calculation with Decimal ROUND_HALF_UP
    deficit_raw = max(
        Decimal("0"),
        Decimal(str(detection.planned_recovery_minutes)) - Decimal(str(detection.capped_recovery_minutes)),
    )
    deficit_minutes = int(deficit_raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    # 3. Completion ratio and relative deficit
    ratio = detection.completion_ratio if detection.completion_ratio is not None else 0.0
    capped_ratio = min(1.0, max(0.0, ratio))
    relative_deficit = 1.0 - capped_ratio

    # 4. Workload scaling
    multiplier = config.workload_multipliers.get(workload_level, 1.0)
    effective_deficit = min(1.0, relative_deficit * multiplier)
    base_score = 100.0 * (1.0 - effective_deficit)

    # 5. Consecutive focus sessions without long break penalty
    threshold = detection.long_break_after_sessions
    excess_sessions = max(0, detection.consecutive_focus_sessions_without_long_break - threshold)
    consecutive_penalty = min(
        config.max_consecutive_penalty,
        float(excess_sessions) * config.penalty_per_excess_session,
    )

    raw_score = max(0.0, min(100.0, base_score - consecutive_penalty))
    final_score = float(Decimal(str(raw_score)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    # 6. Assign categorical level
    if final_score >= config.score_high_threshold:
        level = "HIGH"
    elif final_score >= config.score_medium_threshold:
        level = "MEDIUM"
    else:
        level = "LOW"

    return RecoveryScore(
        score=final_score,
        level=level,
        deficit_minutes=deficit_minutes,
        status="ok",
    )
