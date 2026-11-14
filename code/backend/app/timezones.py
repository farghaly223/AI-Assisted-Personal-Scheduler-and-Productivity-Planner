"""Input validation only; no recurrence expansion or scheduling engine."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone_name(name: str) -> str:
    if not isinstance(name, str) or not name or name != name.strip():
        raise ValueError("A supported IANA timezone name is required")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unsupported IANA timezone name") from None
    return name


def resolve_unambiguous_local_time(value: datetime, zone_name: str) -> datetime:
    """Validate one supplied endpoint; callers must not silently choose a DST fold."""
    if value.tzinfo is not None:
        raise ValueError("Supply a local date/time without an attached offset")
    zone = ZoneInfo(validate_timezone_name(zone_name))
    instants = set()
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == value:
            instants.add(candidate)
    if len(instants) != 1:
        raise ValueError("Local time is ambiguous or nonexistent because of a timezone transition")
    return instants.pop()
