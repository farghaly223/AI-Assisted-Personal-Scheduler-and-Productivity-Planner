"""Public API contracts. Ownership and server-managed fields are read-only."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, ClassVar, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, computed_field, field_validator, model_validator

from app.timezones import resolve_unambiguous_local_time, validate_timezone_name


def nonblank(value: str) -> str:
    no_null_character(value)
    if not value.strip():
        raise ValueError("Must contain non-whitespace characters")
    return value


def no_null_character(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Null characters are not permitted")
    return value


def instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timestamp with UTC offset is required")
    try:
        return value.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("Timestamp exceeds supported UTC calendar dates") from None


def wall_time(value: time) -> time:
    if value.tzinfo is not None or value.microsecond:
        raise ValueError("Use local time with whole seconds and no UTC offset")
    return value


Title = Annotated[str, Field(max_length=500), AfterValidator(nonblank)]
Category = Literal["STUDY", "WORK", "PERSONAL", "HEALTH", "MEETING"]
Priority = Annotated[int, Field(strict=True, ge=1, le=5)]
Duration = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
Instant = Annotated[datetime, AfterValidator(instant)]
WallTime = Annotated[time, AfterValidator(wall_time)]
ZoneName = Annotated[str, Field(max_length=100), AfterValidator(validate_timezone_name)]
Text = Annotated[str, Field(max_length=500), AfterValidator(no_null_character)]
Username = Annotated[str, Field(max_length=100), AfterValidator(nonblank)]
FullName = Annotated[str, Field(max_length=200), AfterValidator(nonblank)]
DayOffset = Annotated[int, Field(strict=True, ge=0, le=32767)]
Weekday = Annotated[int, Field(strict=True, ge=1, le=7)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Patch(Contract):
    required_values: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def reject_required_nulls(cls, data):
        if isinstance(data, dict) and any(key in data and data[key] is None for key in cls.required_values):
            raise ValueError("Required values cannot be null")
        return data


class ProfileUpdate(Patch):
    username: Username | None = None
    full_name: FullName | None = None

    @field_validator("username")
    @classmethod
    def username_not_padded(cls, value):
        if value is not None and value != value.strip():
            raise ValueError("Username must not have leading or trailing whitespace")
        return value


class ProfileRead(ProfileUpdate):
    id: UUID
    created_at: datetime
    updated_at: datetime


class PreferencesUpdate(Patch):
    required_values = frozenset({"timezone", "notifications_enabled"})
    timezone: ZoneName | None = None
    notifications_enabled: Annotated[bool, Field(strict=True)] | None = None
    preferred_work_start: WallTime | None = None
    preferred_work_end: WallTime | None = None


class PreferencesValues(Contract):
    timezone: ZoneName = "UTC"
    notifications_enabled: Annotated[bool, Field(strict=True)] = False
    preferred_work_start: WallTime | None = None
    preferred_work_end: WallTime | None = None

    @model_validator(mode="after")
    def paired_hours(self):
        start, end = self.preferred_work_start, self.preferred_work_end
        if (start is None) != (end is None) or (start is not None and start == end):
            raise ValueError("Work hours must both be null or both supplied and unequal")
        return self


class PreferencesRead(PreferencesValues):
    preference_id: UUID
    user_id: UUID
    updated_at: datetime


class TaskCreate(Contract):
    title: Title
    task_type: Literal["FIXED", "FLEXIBLE"]
    estimated_duration_minutes: Duration
    category: Category
    priority: Priority
    deadline: Instant | None = None
    fixed_start_at: Instant | None = None
    fixed_end_at: Instant | None = None
    context: Text | None = None

    @model_validator(mode="after")
    def valid_interval(self):
        start, end = self.fixed_start_at, self.fixed_end_at
        if self.task_type == "FLEXIBLE":
            if start is not None or end is not None:
                raise ValueError("Flexible tasks cannot have fixed endpoints")
        elif start is None or end is None or end <= start:
            raise ValueError("Fixed tasks require an end after their start")
        return self


class TaskUpdate(Patch):
    required_values = frozenset({"title", "task_type", "estimated_duration_minutes", "category", "priority"})
    title: Title | None = None
    task_type: Literal["FIXED", "FLEXIBLE"] | None = None
    estimated_duration_minutes: Duration | None = None
    category: Category | None = None
    priority: Priority | None = None
    deadline: Instant | None = None
    fixed_start_at: Instant | None = None
    fixed_end_at: Instant | None = None
    context: Text | None = None


class TaskRead(TaskCreate):
    task_id: UUID
    user_id: UUID
    status: Literal["UNSCHEDULED"]
    created_at: datetime
    updated_at: datetime


class EventFields(Contract):
    title: Title
    category: Category
    location: Text | None = None
    recurrence_type: Literal["ONCE", "WEEKLY"] = "ONCE"
    start_at: Instant | None = None
    end_at: Instant | None = None
    recurrence_first_date: date | None = None
    recurrence_until_date: date | None = None
    recurrence_start_time: WallTime | None = None
    recurrence_end_time: WallTime | None = None
    recurrence_end_day_offset: DayOffset | None = None
    recurrence_timezone: ZoneName | None = None


class EventCreate(EventFields):
    weekday: Weekday | None = Field(default=None, description="ISO weekday 1=Monday..7=Sunday; must match recurrence_first_date. Not stored.")

    @model_validator(mode="after")
    def valid_recurrence(self):
        weekly_fields = (self.recurrence_first_date, self.recurrence_until_date,
                         self.recurrence_start_time, self.recurrence_end_time,
                         self.recurrence_end_day_offset, self.recurrence_timezone, self.weekday)
        if self.recurrence_type == "ONCE":
            if any(value is not None for value in weekly_fields):
                raise ValueError("One-time events cannot contain weekly fields")
            if self.start_at is None or self.end_at is None or self.end_at <= self.start_at:
                raise ValueError("One-time events require an end after their start")
            return self
        if self.start_at is not None or self.end_at is not None:
            raise ValueError("Weekly rules cannot contain one-time endpoints")
        first, until = self.recurrence_first_date, self.recurrence_until_date
        if any(value is None for value in (first, self.recurrence_start_time, self.recurrence_end_time,
                                          self.recurrence_end_day_offset, self.recurrence_timezone)):
            raise ValueError("Weekly rules require first date, local endpoints, day offset and timezone")
        if until is not None and until < first:
            raise ValueError("Recurrence until date cannot precede first date")
        if self.weekday is not None and self.weekday != first.isoweekday():
            raise ValueError("Weekday must match the first occurrence date")
        if self.recurrence_end_day_offset == 0 and self.recurrence_end_time <= self.recurrence_start_time:
            raise ValueError("Same-day recurrence must end after its start")
        # Validate anchor and bounded final occurrence, without creating occurrences.
        dates = {first}
        if until is not None:
            dates.add(first + timedelta(days=((until - first).days // 7) * 7))
        try:
            for start_date in dates:
                start = resolve_unambiguous_local_time(datetime.combine(start_date, self.recurrence_start_time), self.recurrence_timezone)
                end_date = start_date + timedelta(days=self.recurrence_end_day_offset)
                end = resolve_unambiguous_local_time(datetime.combine(end_date, self.recurrence_end_time), self.recurrence_timezone)
                if end <= start:
                    raise ValueError("Resolved recurrence must end after its start")
        except OverflowError:
            raise ValueError("Recurrence endpoint exceeds supported calendar dates") from None
        return self


class EventUpdate(Patch):
    required_values = frozenset({"title", "category", "recurrence_type"})
    title: Title | None = None
    category: Category | None = None
    location: Text | None = None
    recurrence_type: Literal["ONCE", "WEEKLY"] | None = None
    start_at: Instant | None = None
    end_at: Instant | None = None
    recurrence_first_date: date | None = None
    recurrence_until_date: date | None = None
    recurrence_start_time: WallTime | None = None
    recurrence_end_time: WallTime | None = None
    recurrence_end_day_offset: DayOffset | None = None
    recurrence_timezone: ZoneName | None = None
    weekday: Weekday | None = None


class EventRead(EventFields):
    event_id: UUID
    user_id: UUID
    source: Literal["MANUAL"]
    status: Literal["SAVED"]
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def weekday(self) -> int | None:
        return self.recurrence_first_date.isoweekday() if self.recurrence_first_date else None


Item = TypeVar("Item")


class Page(Contract, Generic[Item]):
    items: list[Item]
    limit: int
    offset: int
    has_more: bool


class LoginRequest(Contract):
    email: Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]
    password: SecretStr = Field(min_length=1, max_length=1024)


class RegisterRequest(LoginRequest):
    password: SecretStr = Field(min_length=8, max_length=1024)
    username: Username | None = None
    full_name: FullName | None = None

    @field_validator("username")
    @classmethod
    def username_not_padded(cls, value):
        return ProfileUpdate.username_not_padded(value)


class RefreshRequest(Contract):
    refresh_token: SecretStr = Field(min_length=1, max_length=4096)


class AuthUser(Contract):
    id: UUID
    email: str | None = None


class TokenSession(Contract):
    access_token: Annotated[str, Field(min_length=1, max_length=32768)]
    refresh_token: Annotated[str, Field(min_length=1, max_length=4096)]
    token_type: Literal["bearer"] = "bearer"
    expires_in: Annotated[int, Field(strict=True, gt=0)]
    expires_at: int | None = None


class AuthResponse(Contract):
    user: AuthUser
    session: TokenSession | None = None
    confirmation_required: bool = False
    _profile_values: dict[str, str | None] = PrivateAttr(default_factory=dict)


class ErrorDetail(Contract):
    field: str
    message: str


class ErrorBody(Contract):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(Contract):
    error: ErrorBody
