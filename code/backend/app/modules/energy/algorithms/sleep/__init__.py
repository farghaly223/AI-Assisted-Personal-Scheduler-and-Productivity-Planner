"""Sleep detection and sleep quality normalization algorithms."""

from app.modules.energy.algorithms.sleep.config import (
    DEFAULT_SLEEP_CONFIG,
    SleepDetectionConfig,
    UserSleepSchedule,
)
from app.modules.energy.algorithms.sleep.sleep_detector import (
    EVENT_KEYGUARD_HIDDEN,
    EVENT_KEYGUARD_SHOWN,
    EVENT_SCREEN_INTERACTIVE,
    EVENT_SCREEN_NON_INTERACTIVE,
    KNOWN_EVENT_TYPES,
    SleepCandidate,
    calculate_duration_minutes,
    circular_distance_minutes,
    circular_mean_minutes,
    detect_sleep_candidate,
    resolve_local_time,
    time_to_circular_minutes,
)
from app.modules.energy.algorithms.sleep.sleep_quality import normalize_sleep_quality

__all__ = [
    "DEFAULT_SLEEP_CONFIG",
    "EVENT_KEYGUARD_HIDDEN",
    "EVENT_KEYGUARD_SHOWN",
    "EVENT_SCREEN_INTERACTIVE",
    "EVENT_SCREEN_NON_INTERACTIVE",
    "KNOWN_EVENT_TYPES",
    "SleepCandidate",
    "SleepDetectionConfig",
    "UserSleepSchedule",
    "calculate_duration_minutes",
    "circular_distance_minutes",
    "circular_mean_minutes",
    "detect_sleep_candidate",
    "normalize_sleep_quality",
    "resolve_local_time",
    "time_to_circular_minutes",
]
