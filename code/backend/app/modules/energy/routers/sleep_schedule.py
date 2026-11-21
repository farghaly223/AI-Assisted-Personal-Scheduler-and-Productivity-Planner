"""Sleep schedule endpoints under /energy/sleep-schedule."""

from fastapi import APIRouter, Response

from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES
from app.modules.energy.schemas import (
    SleepScheduleCreate,
    SleepScheduleRead,
    SleepScheduleToggle,
)
from app.modules.energy.services import sleep_schedule as service

router = APIRouter(prefix="/sleep-schedule", tags=["Energy"], responses=ERROR_RESPONSES)


@router.put(
    "",
    response_model=SleepScheduleRead,
    summary="Create or replace own sleep schedule",
    description="Create or replace the caller's sleep schedule. Extra fields and user_id in body are forbidden. Always sets is_active=true.",
)
def put_sleep_schedule(payload: SleepScheduleCreate, session: SessionDep):
    user_id = session.info["user_id"]
    return SleepScheduleRead.model_validate(service.upsert_sleep_schedule(session, user_id, payload))


@router.get(
    "",
    response_model=SleepScheduleRead,
    summary="Retrieve own sleep schedule",
    description="Retrieve caller's sleep schedule. Returns 404 if no schedule exists.",
)
def get_sleep_schedule(session: SessionDep):
    user_id = session.info["user_id"]
    return SleepScheduleRead.model_validate(service.get_sleep_schedule(session, user_id))


@router.patch(
    "/toggle",
    response_model=SleepScheduleRead,
    summary="Toggle sleep schedule active state",
    description="Update active status of caller's sleep schedule. Returns 404 if no schedule exists.",
)
def patch_sleep_schedule_toggle(payload: SleepScheduleToggle, session: SessionDep):
    user_id = session.info["user_id"]
    return SleepScheduleRead.model_validate(service.toggle_sleep_schedule(session, user_id, payload.is_active))


@router.delete(
    "",
    status_code=204,
    summary="Disable sleep schedule",
    description="Disables caller's sleep schedule (sets is_active=false, preserves settings). Idempotent; returns 404 if no schedule exists.",
)
def delete_sleep_schedule(session: SessionDep):
    user_id = session.info["user_id"]
    service.disable_sleep_schedule(session, user_id)
    return Response(status_code=204)
