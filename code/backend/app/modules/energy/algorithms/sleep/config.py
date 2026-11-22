"""Sleep detection configuration and schedule data structures.

PROVISIONAL:
The thresholds, scoring curves, and confidence weights defined here are initial
engineering heuristics and have NOT been scientifically validated or approved as
final by clinical or product leadership. All values are subject to change.

CONFIDENCE SCORING PROPERTY:
Without an active user sleep schedule and historical sleep data, the maximum
achievable confidence score is 0.75 (0.45 from duration plausibility + 0.30 from
data quality). Supplying matching schedule and history evidence can increase the
confidence up to 1.00 (additional 0.15 for schedule overlap + 0.10 for history
similarity).
"""

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class UserSleepSchedule:
    """User sleep schedule contract for algorithms.
    
    Attributes:
        bedtime: Local expected bedtime.
        wake_time: Local expected wake time.
        repeat_days: Days of week (0=Monday, ..., 6=Sunday) when schedule applies.
        is_active: Whether the schedule is active.
    """

    bedtime: time
    wake_time: time
    repeat_days: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    is_active: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.bedtime, time) or not isinstance(self.wake_time, time):
            raise ValueError("bedtime and wake_time must be datetime.time instances")
        if self.bedtime == self.wake_time:
            raise ValueError("bedtime and wake_time must differ")
        # Ensure repeat_days is a tuple of valid day integers (0..6)
        if not isinstance(self.repeat_days, tuple):
            object.__setattr__(self, "repeat_days", tuple(self.repeat_days))
        for d in self.repeat_days:
            if not isinstance(d, int) or isinstance(d, bool) or d < 0 or d > 6:
                raise ValueError(f"Invalid repeat_day: {d}. Expected int in 0..6.")


@dataclass(frozen=True)
class SleepDetectionConfig:
    """Configuration thresholds and weights for sleep candidate detection.

    All fields are PROVISIONAL and have not been scientifically validated.
    """

    # Search window boundaries relative to user local target wake-up date
    # Default: search window = local 18:00 previous day to local 14:00 target day
    search_window_start_time: time = time(18, 0)
    search_window_end_time: time = time(14, 0)

    # Allowable sleep duration limits in minutes
    min_sleep_minutes: int = 180  # 3 hours minimum
    max_sleep_minutes: int = 840  # 14 hours maximum

    # Optimal duration range for maximum duration plausibility score (7-9 hours)
    optimal_sleep_min_minutes: int = 420  # 7 hours
    optimal_sleep_max_minutes: int = 540  # 9 hours

    # Max duration of awake bursts (minutes) between inactive gaps to merge
    max_awake_burst_minutes: int = 10

    # Max schedule/history deviation (minutes) before score drops to 0.0 (3 hours)
    max_tolerance_deviation_minutes: float = 180.0

    # Confidence factor weights (sum to 1.0)
    # Notice: weight_duration + weight_quality = 0.75 (max confidence without schedule/history)
    weight_duration_plausibility: float = 0.45
    weight_data_quality: float = 0.30
    weight_schedule_overlap: float = 0.15
    weight_history_similarity: float = 0.10


DEFAULT_SLEEP_CONFIG = SleepDetectionConfig()
