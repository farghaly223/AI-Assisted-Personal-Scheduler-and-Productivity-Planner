"""Shared API contracts and validation helpers."""

from datetime import time
from typing import Annotated, ClassVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.core.timezones import validate_timezone_name


def nonblank(value: str) -> str:
    no_null_character(value)
    if not value.strip():
        raise ValueError("Must contain non-whitespace characters")
    return value


def no_null_character(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Null characters are not permitted")
    return value


def wall_time(value: time) -> time:
    if value.tzinfo is not None or value.microsecond:
        raise ValueError("Use local time with whole seconds and no UTC offset")
    return value


WallTime = Annotated[time, AfterValidator(wall_time)]


ZoneName = Annotated[str, Field(max_length=100), AfterValidator(validate_timezone_name)]


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


class ErrorDetail(Contract):
    field: str
    message: str


class ErrorBody(Contract):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(Contract):
    error: ErrorBody
