"""User authentication, profile, and preferences API contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, Field, PrivateAttr, SecretStr, field_validator, model_validator

from app.core.schemas import Contract, Patch, WallTime, ZoneName, nonblank


Username = Annotated[str, Field(max_length=100), AfterValidator(nonblank)]


FullName = Annotated[str, Field(max_length=200), AfterValidator(nonblank)]


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


EmailAddress = Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]


class LoginRequest(Contract):
    email: EmailAddress
    password: SecretStr = Field(min_length=1, max_length=1024)


class RegisterRequest(LoginRequest):
    password: SecretStr = Field(min_length=8, max_length=1024)
    username: Username | None = None
    full_name: FullName | None = None

    @field_validator("username")
    @classmethod
    def username_not_padded(cls, value):
        return ProfileUpdate.username_not_padded(value)


class ResendSignupCodeRequest(Contract):
    email: EmailAddress


class ResendSignupCodeResponse(Contract):
    status: Literal["accepted"] = "accepted"
    message: str = "If this email has an unconfirmed account, a verification code will be sent."


class SignupVerificationRequest(Contract):
    email: EmailAddress
    token: SecretStr = Field(min_length=6, max_length=6,
        description="Exactly six ASCII digits, sent as a string to preserve leading zeros.")

    @field_validator("token")
    @classmethod
    def six_digit_token(cls, value):
        code = value.get_secret_value()
        if not code.isascii() or not code.isdigit():
            raise ValueError("Verification code must contain exactly six ASCII digits")
        return value


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
