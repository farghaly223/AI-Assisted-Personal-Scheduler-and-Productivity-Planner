from datetime import date as Date

from fastapi import APIRouter, Query

from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES
from app.modules.scheduler.schedule_schemas import CurrentScheduleRead, DayScheduleRead, WeekScheduleRead
from app.modules.scheduler.services.schedule_read_service import current_schedule_payload, day_payload, week_payload

router = APIRouter(prefix="/scheduler", tags=["Scheduler schedule"], responses=ERROR_RESPONSES)


@router.get("/current", response_model=CurrentScheduleRead, summary="Read the current published schedule",
    description="Requires bearer token. Returns the user's current published schedule version with all its items and the tasks that could not be placed (with reasons). Returns 404 when no schedule has been generated yet.")
def read_current(session: SessionDep):
    return current_schedule_payload(session)


@router.get("/daily", response_model=DayScheduleRead, summary="Read one day of the current schedule",
    description="Requires bearer token. Items of the current version that overlap the given calendar date in the user's timezone (from preferences, UTC by default), ordered by start time. An empty list is returned when there is no schedule.")
def read_daily(session: SessionDep, day: Date = Query(..., alias="date")):
    return day_payload(session, day)


@router.get("/weekly", response_model=WeekScheduleRead, summary="Read seven days of the current schedule",
    description="Requires bearer token. Seven consecutive calendar days starting at week_start in the user's timezone. An item that crosses midnight appears in both days.")
def read_weekly(session: SessionDep, week_start: Date):
    return week_payload(session, week_start)