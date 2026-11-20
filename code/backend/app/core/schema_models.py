"""Complete migration metadata on the existing Base; no engine or second history."""

from app.core.models import Base, Profile, UserPreferences, Task, Event
from app.modules.user.planning_models import AvailabilityWindow
from app.modules.scheduler.planning_models import (
    TaskRequirement, TaskOutcome, Schedule, ScheduleVersion, ScheduleItem,
    UnscheduledTask, ScheduleValidationIssue,
)
from app.modules.energy.models import (
    SleepSchedule, SleepSession, SleepUsageSignal, SleepQualityRating,
    RecoveryStrategy, RecoveryPeriod, EnergyScore,
)
from app.modules.attention.models import (
    FocusSession, UsageSyncBatch, AppClassification, AppUsageSummary,
    AttentionAssessment, AttentionQuestionnaireResponse, DistractionEvent, AttentionScore,
)
from app.modules.decision.models import DecisionExecution, DecisionCandidate
from app.modules.rescheduling.models import ReschedulingRun, ReschedulingChange
