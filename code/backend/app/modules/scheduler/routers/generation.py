from datetime import timedelta

from fastapi import APIRouter

from app.core.dependencies import SessionDep
from app.core.errors import APIError, ERROR_RESPONSES
from app.modules.scheduler.generation_schemas import GenerateRequest, GeneratedRead, SchedulePreviewRead
from app.modules.scheduler.services.generation_service import generate_preview
from app.modules.scheduler.services.persistence_service import create_schedule

router = APIRouter(prefix="/scheduler/generate", tags=["Scheduler generation"], responses=ERROR_RESPONSES)
regenerate_router = APIRouter(prefix="/scheduler/regenerate", tags=["Scheduler generation"], responses=ERROR_RESPONSES)

MAX_RANGE = timedelta(days=31)

RULES = ("FIXED tasks and events keep their times and are locked, FLEXIBLE tasks are placed in free time "
         "earliest-deadline-first (ties by priority), nothing is scheduled in the past, and tasks whose latest outcome "
         "is COMPLETED or SKIPPED are not planned again. Tasks that cannot be placed are listed with a reason. "
         "The range is limited to 31 days.")


def _check(payload: GenerateRequest):
    if payload.end <= payload.start:
        raise APIError(422, "invalid_request", "end must be after start")
    if payload.end - payload.start > MAX_RANGE:
        raise APIError(422, "invalid_request", "The range cannot exceed 31 days")


@router.post("/preview", response_model=SchedulePreviewRead, summary="Preview a generated schedule without saving",
    description="Requires bearer token. Builds a draft schedule for [start, end) without saving anything. " + RULES + " Validation issues are returned; is_publishable is false when any ERROR exists.")
def preview_schedule(payload: GenerateRequest, session: SessionDep):
    _check(payload)
    return generate_preview(session, payload.start, payload.end)


@router.post("", response_model=GeneratedRead, status_code=201, summary="Generate and publish the first schedule",
    description="Requires bearer token. Creates the user's first schedule version for [start, end) and publishes it. " + RULES + " Returns 409 if a schedule already exists (use POST /scheduler/regenerate) or if blocking conflicts exist (inspect them with preview).")
def generate_schedule(payload: GenerateRequest, session: SessionDep):
    _check(payload)
    return create_schedule(session, payload.start, payload.end, regenerate=False)


@regenerate_router.post("", response_model=GeneratedRead, status_code=201, summary="Regenerate the schedule as a new version",
    description="Requires bearer token. Builds a new version from the current one for [start, end) and publishes it. Locked placements and completed tasks are kept unchanged; everything else is planned again. " + RULES + " Returns 409 if there is no current schedule.")
def regenerate_schedule(payload: GenerateRequest, session: SessionDep):
    _check(payload)
    return create_schedule(session, payload.start, payload.end, regenerate=True)