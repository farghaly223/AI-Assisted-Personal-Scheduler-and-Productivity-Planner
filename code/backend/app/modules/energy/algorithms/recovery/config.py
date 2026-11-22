"""Recovery detection and scoring configuration.

PROVISIONAL:
The thresholds, scoring penalties, and workload weights defined here are initial
engineering heuristics and have NOT been scientifically validated or approved as
final by clinical or product leadership. All values are subject to change.

SCORING FORMULA DOCUMENTATION:
1. Completion Ratio:
   completion_ratio = sum(min(credited_recovery_i, planned_duration_i)) / sum(planned_duration_i)
   Each break's credited recovery is capped at its planned duration, ensuring an
   extended break never compensates for a missed break.
   When data_status is UNKNOWN, completion_ratio is None.

2. Relative Deficit:
   relative_deficit = 1.0 - completion_ratio  (in [0.0, 1.0])

3. Workload Scaling:
   effective_deficit = min(1.0, relative_deficit * workload_multiplier)
   Base Score = 100.0 * (1.0 - effective_deficit)

4. Consecutive Focus Sessions Penalty:
   A long break is expected after every `strategy.long_break_after_sessions` sessions.
   If consecutive completed focus sessions exceed this threshold without a qualifying
   long break (credited >= 80% of planned long break duration), an excess penalty is applied:
   excess = max(0, consecutive_sessions - threshold)
   consecutive_penalty = min(max_consecutive_penalty, excess * penalty_per_excess_session)

5. Final Score:
   Score = clamp(0.0, 100.0, Base Score - consecutive_penalty)

   Monotonicity properties:
   - More observed recovery decreases deficit, increasing or keeping score constant (never lowers score).
   - More consecutive sessions without long break increases penalty, decreasing or keeping score constant (never raises score).
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


DEFAULT_WORKLOAD_MULTIPLIERS: Mapping[str | None, float] = MappingProxyType({
    "LOW": 0.8,
    "MEDIUM": 1.0,
    "HIGH": 1.25,
    None: 1.0,
})


@dataclass(frozen=True)
class RecoveryConfig:
    """Configuration thresholds and weights for recovery detection and scoring.

    All fields are PROVISIONAL and pending team approval.
    """

    # Maximum gap between consecutive completed focus sessions belonging to the same work block (minutes)
    max_gap_same_block_minutes: int = 60

    # Minimum gap duration in seconds before considering a break taken (shorter is MISSED)
    min_break_seconds: int = 60

    # Ratio threshold for shortened breaks (credited < 80% is SHORTENED)
    shortened_ratio_threshold: float = 0.80

    # Ratio threshold for extended breaks (credited > 150% is EXTENDED)
    extended_ratio_threshold: float = 1.50

    # Threshold for qualifying as an effective long break (ratio of planned long break duration)
    long_break_recovery_ratio: float = 0.80

    # Workload deficit multipliers (immutable mapping proxy)
    workload_multipliers: Mapping[str | None, float] = DEFAULT_WORKLOAD_MULTIPLIERS

    # Penalty in score points per excess session beyond long_break_after_sessions
    penalty_per_excess_session: float = 5.0

    # Maximum score deduction for consecutive sessions without long break
    max_consecutive_penalty: float = 30.0

    # Level thresholds
    score_high_threshold: float = 70.0
    score_medium_threshold: float = 40.0


DEFAULT_RECOVERY_CONFIG = RecoveryConfig()
