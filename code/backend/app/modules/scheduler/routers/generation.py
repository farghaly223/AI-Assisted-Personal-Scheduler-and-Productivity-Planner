from datetime import timedelta

from fastapi import APIRouter

from app.core.dependencies import SessionDep
from app.core.errors import APIError, ERROR_RESPONSES
from app.modules.scheduler.generation_schemas import GenerateRequest, SchedulePreviewRead
from app.modules.scheduler.services.generation_service import generate_preview

router = APIRouter(prefix="/scheduler/generate", tags=["Scheduler generation"], responses=ERROR_RESPONSES)

MAX_RANGE = timedelta(days=31)


@router.post("/preview", response_model=SchedulePreviewRead, summary="Preview a generated schedule without saving",
    description="Requires bearer token. Builds a draft schedule for [start, end) from the caller's availability windows, events and tasks: FIXED tasks and events keep their times, FLEXIBLE tasks are placed in free time earliest-deadline-first (ties by priority), nothing is scheduled in the past, and tasks whose latest outcome is COMPLETED or SKIPPED are ignored. Tasks that cannot be placed are listed with a reason. Validation issues (overlaps, deadline violations) are returned; is_publishable is false when any ERROR exists. Nothing is saved. The range is limited to 31 days.")
def preview_schedule(payload: GenerateRequest, session: SessionDep):
    if payload.end <= payload.start:
        raise APIError(422, "invalid_request", "end must be after start")
    if payload.end - payload.start > MAX_RANGE:
        raise APIError(422, "invalid_request", "The range cannot exceed 31 days")
    return generate_preview(session, payload.start, payload.end)