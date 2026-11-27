"""Energy score algorithm configuration and data structures.

PROVISIONAL:
The thresholds, weights, piecewise scoring curves, and reliability constants
defined here are initial engineering heuristics and have NOT been scientifically
validated or approved as final by clinical or product leadership. All values
are subject to change.

SCORING FORMULAS AND PROPERTIES:
1. Workload Calculation:
   - Planned task minutes are summed across all input tasks, including completed ones,
     representing total planned cognitive demand for the day.
   - Fixed calendar events are clipped to [day_start, day_end) and merged (interval union)
     so overlapping events are never double-counted.
   - load_ratio = (total_task_minutes + fixed_event_minutes) / available_minutes,
     capped at max_load_ratio (default 1.5).
   - available_minutes=960 (16 hours) is a fixed provisional default until available waking
     hours can be derived dynamically from the user's sleep schedule.
   - workload_score = max(0.0, 100.0 * (1.0 - min(load_ratio, 1.0)) - penalty),
     where penalty = min(high_priority_penalty_max, high_priority_tasks * penalty_per_task).

2. Sleep Scoring:
   - Sleep scoring boundaries:
   - Duration score is piecewise linear:
     * duration <= sleep_hard_min (180m): 0.0
     * sleep_hard_min < duration < sleep_optimal_min (420m): 0.0 -> 100.0 linear interpolation
     * sleep_optimal_min <= duration <= sleep_optimal_max (540m): 100.0 (optimal 7-9 hours)
     * sleep_optimal_max < duration <= sleep_long_max (660m): 100.0 -> 85.0 (long_sleep_floor)
     * duration > sleep_long_max: 85.0 (floor)
   - Blending with subjective rating:
     * If rated: sleep_score = (1 - w_q) * duration_score + w_q * normalize_sleep_quality(rating)
       where w_q = sleep_quality_blend (default 0.3).
     * If unrated: sleep_score = duration_score directly (a missing rating is never treated as 0).
   - ONLY confirmed sleep sessions count. Unconfirmed sessions are excluded from scoring.

3. Recovery Scoring:
   - Taken directly from RecoveryScore.score (algorithms/recovery) when status != 'insufficient_data'.

4. Energy Score Aggregation:
   - Computed using Decimal end-to-end.
   - Weights of available factors are renormalized to sum to 1.0:
     weight_used_i = weight_i / sum(available_weights)
     raw_score = sum(score_i * weight_used_i)
   - Final score is rounded ROUND_HALF_UP to 2 decimal places.
   - Categorical level ('LOW' < 40.0, 'MEDIUM' < 75.0, 'HIGH' >= 75.0) is derived from
     the ROUNDED Decimal score.
   - If a required factor (default: sleep) is unavailable, or total available weight <
     min_available_weight (default 0.50), status is 'insufficient_data', and
     score, level, and confidence are None.
   - A sleep-only input yields status ok with low confidence (max 0.5 * 0.8 = 0.40 for unrated,
     or 0.5 * 1.0 = 0.50 for rated) and a LOW_CONFIDENCE insight.

5. Confidence Calculation:
   - confidence = min(1.0, sum(weight_i * reliability_i)), rounded to 4 decimals.
   - Weights are NOT renormalized for confidence: an unavailable factor contributes 0.0,
     ensuring partial data can never appear as confident as full data.
   - Reliabilities:
     * Sleep: 1.0 (rated), 0.8 (unrated), 0.0 (unavailable).
     * Workload: 1.0 (completed status known), 0.7 (completed unknown), 0.0 (unavailable).
     * Recovery: 1.0 (OBSERVED), 0.6 (PARTIAL), 0.0 (UNKNOWN or unavailable).
"""

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Sequence


ALGORITHM_VERSION = "energy-algo-2"
VALID_FACTOR_NAMES = frozenset({"sleep", "workload", "recovery"})
VALID_ENERGY_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
VALID_WORKLOAD_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})


@dataclass(frozen=True)
class EnergyConfig:
    """Configuration thresholds, weights, and limits for energy score calculation.

    All fields are PROVISIONAL and pending team approval.
    """

    # Workload parameters
    # Available waking minutes per day (default: 16 hours = 960 minutes)
    available_minutes: int = 960
    # Maximum load ratio cap
    max_load_ratio: float = 1.5
    # Priority threshold for high-priority task count (1..5 scale)
    high_priority_min: int = 4
    # Workload categorical level thresholds
    workload_low_threshold: float = 0.40
    workload_medium_threshold: float = 0.75
    # High-priority task score deduction penalty
    high_priority_penalty_per_task: float = 2.0
    high_priority_penalty_max: float = 10.0

    # Sleep scoring boundaries in minutes
    sleep_hard_min: int = 180       # 3 hours minimum
    sleep_optimal_min: int = 420    # 7 hours optimal lower bound
    sleep_optimal_max: int = 540    # 9 hours optimal upper bound
    sleep_long_max: int = 660       # 11 hours long-sleep floor point
    sleep_long_sleep_floor: float = 85.0
    sleep_quality_blend: float = 0.30

    # Factor weights (must be positive and sum to 1.0)
    weight_sleep: float = 0.50
    weight_workload: float = 0.25
    weight_recovery: float = 0.25

    # Minimum available weight required to compute an EnergyScore
    min_available_weight: float = 0.50

    # Required factors: if any is unavailable, status becomes 'insufficient_data'
    required_factors: tuple[str, ...] = ("sleep",)

    # Energy categorical level thresholds (applied to rounded score)
    level_low_threshold: float = 40.0
    level_high_threshold: float = 75.0

    # Reliability weights for confidence calculation
    reliability_sleep_rated: float = 1.0
    reliability_sleep_unrated: float = 0.8
    reliability_workload_known: float = 1.0
    reliability_workload_unknown: float = 0.7
    reliability_recovery_observed: float = 1.0
    reliability_recovery_partial: float = 0.6

    # Confidence threshold below which a LOW_CONFIDENCE insight is emitted
    low_confidence_threshold: float = 0.70

    # Recovery deficit threshold in minutes to trigger RECOVERY_INSUFFICIENT insight
    recovery_deficit_insight_minutes: int = 10

    def __post_init__(self) -> None:
        # Validate weights
        if self.weight_sleep <= 0 or self.weight_workload <= 0 or self.weight_recovery <= 0:
            raise ValueError("All factor weights must be positive floats.")

        weights_sum = Decimal(str(self.weight_sleep)) + Decimal(str(self.weight_workload)) + Decimal(str(self.weight_recovery))
        if weights_sum != Decimal("1.0"):
            raise ValueError(f"Factor weights must sum to 1.0, got {weights_sum}")

        # Validate required factors
        if not isinstance(self.required_factors, tuple):
            object.__setattr__(self, "required_factors", tuple(self.required_factors))
        for factor in self.required_factors:
            if factor not in VALID_FACTOR_NAMES:
                raise ValueError(
                    f"Invalid required factor: {factor!r}. Allowed factors: {sorted(VALID_FACTOR_NAMES)}"
                )

        # Validate boundaries
        if not (0 < self.sleep_hard_min < self.sleep_optimal_min <= self.sleep_optimal_max < self.sleep_long_max):
            raise ValueError("Invalid sleep duration boundaries ordering.")

        if not (0.0 <= self.sleep_long_sleep_floor <= 100.0):
            raise ValueError("sleep_long_sleep_floor must be in [0.0, 100.0].")

        if not (0.0 <= self.sleep_quality_blend <= 1.0):
            raise ValueError("sleep_quality_blend must be in [0.0, 1.0].")

        if not (0.0 < self.level_low_threshold < self.level_high_threshold <= 100.0):
            raise ValueError("Invalid energy level thresholds.")

        if not (0.0 < self.min_available_weight <= 1.0):
            raise ValueError("min_available_weight must be in (0.0, 1.0].")

        if self.available_minutes <= 0:
            raise ValueError("available_minutes must be positive.")

        if self.recovery_deficit_insight_minutes < 0:
            raise ValueError("recovery_deficit_insight_minutes must be non-negative.")


DEFAULT_ENERGY_CONFIG = EnergyConfig()
