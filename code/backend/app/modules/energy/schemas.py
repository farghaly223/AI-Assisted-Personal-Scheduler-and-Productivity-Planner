"""Energy module API contracts and validation schemas."""

from datetime import date as dt_date, datetime, time, timezone
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import AfterValidator, Field, field_validator, model_validator

from app.core.schemas import Contract, WallTime, ZoneName
from app.modules.energy.algorithms.recovery.recovery_planner import StrategyParams
from app.modules.energy.algorithms.sleep.sleep_detector import calculate_duration_minutes

DayInt = Annotated[int, Field(strict=True, ge=0, le=6)]


def instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timestamp with UTC offset is required")
    try:
        return value.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("Timestamp exceeds supported UTC calendar dates") from None


Instant = Annotated[datetime, AfterValidator(instant)]

Item = TypeVar("Item")


class Page(Contract, Generic[Item]):
    items: list[Item]
    limit: int
    offset: int
    has_more: bool


class SleepScheduleCreate(Contract):
    bedtime: WallTime
    wake_time: WallTime
    timezone: ZoneName
    repeat_days: list[DayInt] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    bedtime_reminder_enabled: bool = Field(default=True, strict=True)
    reminder_minutes_before: int = Field(default=15, strict=True, ge=0, le=180)
    wake_alarm_enabled: bool = Field(default=True, strict=True)

    @field_validator("repeat_days")
    @classmethod
    def validate_repeat_days(cls, v: list[DayInt]) -> list[int]:
        if not v:
            raise ValueError("repeat_days must not be empty")
        return sorted(set(v))

    @model_validator(mode="after")
    def validate_times_differ(self):
        if self.bedtime == self.wake_time:
            raise ValueError("Bedtime and wake time must differ")
        return self


class SleepScheduleToggle(Contract):
    is_active: bool = Field(strict=True)


class SleepScheduleRead(Contract):
    id: UUID
    bedtime: time
    wake_time: time
    timezone: str
    repeat_days: list[int]
    bedtime_reminder_enabled: bool
    reminder_minutes_before: int
    wake_alarm_enabled: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SleepSessionCreate(Contract):
    start_time: Instant
    end_time: Instant
    timezone: ZoneName

    @model_validator(mode="after")
    def validate_session(self):
        if self.end_time <= self.start_time:
            raise ValueError("End time must be after start time")
        duration = calculate_duration_minutes(self.start_time, self.end_time)
        if duration < 30 or duration > 1440:
            raise ValueError("Duration must be between 30 minutes and 24 hours")
        return self


class SleepSessionConfirm(Contract):
    status: Literal["confirmed", "rejected"]
    start_time: Instant | None = None
    end_time: Instant | None = None
    timezone: ZoneName | None = None

    @model_validator(mode="after")
    def validate_confirm(self):
        if self.status == "rejected":
            if self.start_time is not None or self.end_time is not None or self.timezone is not None:
                raise ValueError("Corrections are only allowed when confirming a session")
        return self


class SleepSessionRead(Contract):
    id: UUID
    start_time: datetime
    end_time: datetime
    duration_minutes: int
    timezone: str
    sleep_date: dt_date
    source: str
    confidence: float
    status: str
    created_at: datetime
    updated_at: datetime
    quality_rating: int | None = None
    quality_score: float | None = None


RatingValue = Annotated[int, Field(strict=True, ge=1, le=5)]


class SleepQualityCreate(Contract):
    rating: RatingValue


class SleepQualityUpdate(Contract):
    rating: RatingValue


class SleepQualityRead(Contract):
    session_id: UUID
    rating: int
    quality_score: float
    created_at: datetime
    updated_at: datetime


class SleepQualityHistoryItem(Contract):
    session_id: UUID
    sleep_date: dt_date
    rating: int
    quality_score: float


EventType = Literal[
    "SCREEN_INTERACTIVE",
    "SCREEN_NON_INTERACTIVE",
    "KEYGUARD_SHOWN",
    "KEYGUARD_HIDDEN",
]


class ScreenEvent(Contract):
    event_type: EventType
    timestamp: Instant


class SignalSyncRequest(Contract):
    timezone: ZoneName
    screen_events: list[ScreenEvent] = Field(min_length=1, max_length=5000)
    date: dt_date | None = None


class SignalSyncResponse(Contract):
    received: int
    inserted: int
    duplicates: int


class SleepEstimateRequest(Contract):
    target_date: dt_date
    timezone: ZoneName


class SleepEstimateResponse(Contract):
    status: Literal["estimated", "existing", "insufficient_data"]
    created: bool
    session: SleepSessionRead | None = None


# --- Recovery Schemas ---

StrategyType = Literal["POMODORO", "CUSTOM"]


class RecoveryStrategyCreateOrUpdate(Contract):
    strategy_type: StrategyType = "POMODORO"
    work_duration_minutes: int | None = Field(default=None, strict=True)
    break_duration_minutes: int | None = Field(default=None, strict=True)
    long_break_duration_minutes: int | None = Field(default=None, strict=True)
    long_break_after_sessions: int | None = Field(default=None, strict=True)
    is_active: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def validate_strategy(self):
        if self.strategy_type == "POMODORO":
            defaults = (25, 5, 20, 4)
            provided = (
                self.work_duration_minutes,
                self.break_duration_minutes,
                self.long_break_duration_minutes,
                self.long_break_after_sessions,
            )
            for val, dflt, name in zip(provided, defaults, (
                "work_duration_minutes",
                "break_duration_minutes",
                "long_break_duration_minutes",
                "long_break_after_sessions",
            )):
                if val is not None and val != dflt:
                    raise ValueError(f"POMODORO strategy {name} must be {dflt} if provided, got {val}")
            self.work_duration_minutes = 25
            self.break_duration_minutes = 5
            self.long_break_duration_minutes = 20
            self.long_break_after_sessions = 4
        elif self.strategy_type == "CUSTOM":
            if self.work_duration_minutes is None:
                raise ValueError("work_duration_minutes is required for CUSTOM strategy")
            if self.break_duration_minutes is None:
                raise ValueError("break_duration_minutes is required for CUSTOM strategy")
            if self.long_break_duration_minutes is None:
                raise ValueError("long_break_duration_minutes is required for CUSTOM strategy")
            if self.long_break_after_sessions is None:
                raise ValueError("long_break_after_sessions is required for CUSTOM strategy")

        if not (1 <= self.work_duration_minutes <= 240):
            raise ValueError("work_duration_minutes must be between 1 and 240")
        if not (1 <= self.break_duration_minutes <= 120):
            raise ValueError("break_duration_minutes must be between 1 and 120")
        if not (1 <= self.long_break_duration_minutes <= 240):
            raise ValueError("long_break_duration_minutes must be between 1 and 240")
        if not (1 <= self.long_break_after_sessions <= 12):
            raise ValueError("long_break_after_sessions must be between 1 and 12")

        try:
            StrategyParams(
                work_minutes=self.work_duration_minutes,
                short_break_minutes=self.break_duration_minutes,
                long_break_minutes=self.long_break_duration_minutes,
                long_break_after_sessions=self.long_break_after_sessions,
            )
        except Exception as e:
            raise ValueError(str(e)) from e

        return self


class RecoveryStrategyRead(Contract):
    id: UUID
    strategy_type: str
    work_duration_minutes: int
    break_duration_minutes: int
    long_break_duration_minutes: int
    long_break_after_sessions: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FocusSessionStartRequest(Contract):
    started_at: Instant | None = None


FocusSessionStatus = Literal["IN_PROGRESS", "COMPLETED", "CANCELLED", "ABANDONED"]
FinishStatus = Literal["COMPLETED", "CANCELLED", "ABANDONED"]


class FocusSessionFinishRequest(Contract):
    status: FinishStatus
    ended_at: Instant | None = None


class FocusSessionRead(Contract):
    id: UUID
    start_time: datetime
    end_time: datetime | None = None
    duration_minutes: int | None = None
    status: FocusSessionStatus
    created_at: datetime
    updated_at: datetime


RecoveryPeriodSource = Literal["FOCUS_TIMER", "DETECTED", "MANUAL", "PLANNED"]
RecoveryPeriodType = Literal["SHORT_BREAK", "LONG_BREAK", "CUSTOM_BREAK", "REST"]


class RecoveryPeriodRead(Contract):
    id: UUID
    start_time: datetime
    end_time: datetime
    duration_minutes: int
    source: RecoveryPeriodSource
    recovery_type: RecoveryPeriodType
    created_at: datetime
    updated_at: datetime


class RecoveryDetectRequest(Contract):
    date: dt_date
    timezone: ZoneName
    workload_level: Literal["LOW", "MEDIUM", "HIGH"] | None = None


class ObservedBreakRead(Contract):
    index: int
    block_break_index: int
    start: datetime
    end: datetime
    raw_gap_minutes: float
    credited_recovery_minutes: float
    capped_recovery_minutes: float
    planned_duration_minutes: int
    classification: Literal["MISSED", "SHORTENED", "TAKEN", "EXTENDED"]
    planned_break_kind: Literal["SHORT_BREAK", "LONG_BREAK"]


class RecoveryScoreRead(Contract):
    score: float | None = None
    level: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    deficit_minutes: int | None = None
    status: Literal["ok", "insufficient_data"]


class RecoveryDetectResponse(Contract):
    data_status: Literal["UNKNOWN", "PARTIAL", "OBSERVED"]
    breaks: list[ObservedBreakRead]
    observed_recovery_minutes: float
    planned_recovery_minutes: float
    missed_breaks: int
    shortened_breaks: int
    extended_breaks: int
    taken_breaks: int
    completion_ratio: float | None = None
    score: RecoveryScoreRead
    truncated: bool


class RecoverySummaryResponse(Contract):
    date: dt_date
    timezone: str
    focus_sessions_completed: int
    total_focus_minutes: int
    in_progress: bool
    recovery_minutes_by_source: dict[str, int]
    total_recovery_minutes: int
    periods_count: int


# --- Energy Score Schemas ---

class EnergyCalculateRequest(Contract):
    target_date: dt_date
    timezone: ZoneName


class InsightDetailRead(Contract):
    code: str
    message: str


class ComponentRead(Contract):
    name: str
    score: float
    weight_used: float
    contribution: float


class EnergyScoreRead(Contract):
    id: UUID
    target_date: dt_date
    status: Literal["ok", "insufficient_data"]
    energy_score: float | None = None
    energy_level: Literal["Low", "Medium", "High"] | None = None
    confidence: float | None = None
    sleep_duration: float | None = None
    sleep_quality: int | None = None
    workload_level: Literal["Low", "Medium", "High"] | None = None
    recovery_level: Literal["Low", "Medium", "High"] | None = None
    insights: list[str] = Field(default_factory=list)
    insight_details: list[InsightDetailRead] = Field(default_factory=list)
    calculated_at: datetime


class EnergyExplanationRead(Contract):
    id: UUID
    target_date: dt_date
    status: Literal["ok", "insufficient_data"]
    energy_score: float | None = None
    energy_level: Literal["Low", "Medium", "High"] | None = None
    confidence: float | None = None
    sleep_duration: float | None = None
    sleep_quality: int | None = None
    workload_level: Literal["Low", "Medium", "High"] | None = None
    recovery_level: Literal["Low", "Medium", "High"] | None = None
    insights: list[str] = Field(default_factory=list)
    insight_details: list[InsightDetailRead] = Field(default_factory=list)
    components: list[ComponentRead] = Field(default_factory=list)
    confidence_breakdown: dict = Field(default_factory=dict)
    calculated_at: datetime


class SleepDaySummary(Contract):
    status: str | None = None
    duration_minutes: int | None = None
    quality_rating: int | None = None
    quality_score: float | None = None


class WorkloadDaySummary(Contract):
    total_task_duration: int
    completed_task_duration: int | None = None
    fixed_event_duration: int
    high_priority_tasks: int
    level: Literal["Low", "Medium", "High"] | None = None
    has_data: bool


class DailySummaryResponse(Contract):
    date: dt_date
    timezone: str
    energy_score: EnergyScoreRead | None = None
    sleep: SleepDaySummary | None = None
    workload: WorkloadDaySummary
    recovery: RecoverySummaryResponse




