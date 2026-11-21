from datetime import timedelta

from fastapi import APIRouter, Query

from app.core.dependencies import SessionDep
from app.core.errors import APIError, ERROR_RESPONSES
from app.modules.scheduler.availability_schemas import AvailabilityRead
from app.modules.scheduler.schemas import Instant
from app.modules.scheduler.services.availability_service import get_availability

router = APIRouter(prefix="/scheduler/availability", tags=["Scheduler availability"], responses=ERROR_RESPONSES)

MAX_RANGE = timedelta(days=31)


@router.get("", response_model=AvailabilityRead, summary="Calculate own available time slots",
    description="Requires bearer token. Returns the free intervals inside [start, end): the user's availability windows minus events (ONCE and expanded WEEKLY occurrences) and FIXED tasks. Timestamps must carry a UTC offset (use Z). The range is limited to 31 days. Optional minimum_minutes hides gaps shorter than that.")
def read_availability(session: SessionDep, start: Instant, end: Instant,
                      minimum_minutes: int = Query(1, ge=1, le=1440)):
    if end <= start:
        raise APIError(422, "invalid_request", "end must be after start")
    if end - start > MAX_RANGE:
        raise APIError(422, "invalid_request", "The range cannot exceed 31 days")
    return get_availability(session, start, end, minimum_minutes)