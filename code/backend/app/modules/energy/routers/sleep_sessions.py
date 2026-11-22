"""Sleep sessions and sleep quality endpoints under /energy/sleep."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES
from app.modules.energy.schemas import (
    Page,
    SignalSyncRequest,
    SignalSyncResponse,
    SleepEstimateRequest,
    SleepEstimateResponse,
    SleepQualityCreate,
    SleepQualityHistoryItem,
    SleepQualityRead,
    SleepQualityUpdate,
    SleepSessionConfirm,
    SleepSessionCreate,
    SleepSessionRead,
)
from app.modules.energy.services import sleep_sessions as service
from app.modules.energy.services import sleep_signals as signals_service

router = APIRouter(prefix="/sleep", tags=["Energy - Sleep"], responses=ERROR_RESPONSES)



@router.post(
    "/sessions",
    response_model=SleepSessionRead,
    status_code=201,
    summary="Create manual sleep session",
    description="Record a manual sleep session. User ID and extra fields forbidden. Status is confirmed, source is manual.",
)
def create_sleep_session(payload: SleepSessionCreate, session: SessionDep):
    user_id = session.info["user_id"]
    return SleepSessionRead.model_validate(service.create_manual_session(session, user_id, payload))


@router.get(
    "/sessions",
    response_model=Page[SleepSessionRead],
    summary="List own sleep sessions",
    description="List caller's sleep sessions, newest first. Optional status and date bounds. Includes quality rating if rated.",
)
def list_sleep_sessions(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
    status: Literal["pending_confirmation", "confirmed", "rejected"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    user_id = session.info["user_id"]
    data = service.list_sessions(
        session,
        user_id,
        limit=limit,
        offset=offset,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    return Page[SleepSessionRead](
        items=[SleepSessionRead.model_validate(item) for item in data["items"]],
        limit=data["limit"],
        offset=data["offset"],
        has_more=data["has_more"],
    )


@router.get(
    "/pending-confirmation",
    response_model=list[SleepSessionRead],
    summary="List pending confirmation sleep sessions",
    description="List caller's sleep sessions awaiting user confirmation, newest first.",
)
def list_pending_sessions(session: SessionDep):
    user_id = session.info["user_id"]
    items = service.list_pending_confirmation(session, user_id)
    return [SleepSessionRead.model_validate(item) for item in items]


@router.get(
    "/sessions/{id}",
    response_model=SleepSessionRead,
    summary="Retrieve own sleep session",
    description="Retrieve caller's sleep session by ID. Returns 404 for missing or other users' rows.",
)
def get_sleep_session(id: UUID, session: SessionDep):
    user_id = session.info["user_id"]
    return SleepSessionRead.model_validate(service.get_session(session, user_id, id))


@router.patch(
    "/sessions/{id}/confirm",
    response_model=SleepSessionRead,
    summary="Confirm, reject, or correct sleep session",
    description="Confirm, reject, or edit caller's sleep session. Corrections only allowed when confirming.",
)
def confirm_or_update_sleep_session(id: UUID, payload: SleepSessionConfirm, session: SessionDep):
    user_id = session.info["user_id"]
    return SleepSessionRead.model_validate(service.confirm_or_update_session(session, user_id, id, payload))


@router.delete(
    "/sessions/{id}",
    status_code=204,
    summary="Delete own sleep session",
    description="Permanently delete caller's sleep session and cascading ratings. Returns 404 if not found.",
)
def delete_sleep_session(id: UUID, session: SessionDep):
    user_id = session.info["user_id"]
    service.delete_session(session, user_id, id)
    return Response(status_code=204)


@router.post(
    "/sessions/{id}/quality",
    response_model=SleepQualityRead,
    status_code=201,
    summary="Rate sleep session quality",
    description="Rate a confirmed sleep session (1-5 strict int). Session must be confirmed; returns 409 if already rated.",
)
def create_sleep_quality(id: UUID, payload: SleepQualityCreate, session: SessionDep):
    user_id = session.info["user_id"]
    return SleepQualityRead.model_validate(service.create_quality_rating(session, user_id, id, payload))


@router.patch(
    "/sessions/{id}/quality",
    response_model=SleepQualityRead,
    summary="Update sleep session quality rating",
    description="Update the 1-5 rating of caller's sleep session. Returns 404 if session or rating does not exist.",
)
def patch_sleep_quality(id: UUID, payload: SleepQualityUpdate, session: SessionDep):
    user_id = session.info["user_id"]
    return SleepQualityRead.model_validate(service.update_quality_rating(session, user_id, id, payload))


@router.get(
    "/quality/history",
    response_model=Page[SleepQualityHistoryItem],
    summary="List sleep quality rating history",
    description="Paginated list of sleep quality ratings, newest first. Optional date_from and date_to filters.",
)
def list_quality_history(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
    date_from: date | None = None,
    date_to: date | None = None,
):
    user_id = session.info["user_id"]
    data = service.list_quality_history(
        session,
        user_id,
        limit=limit,
        offset=offset,
        date_from=date_from,
        date_to=date_to,
    )
    return Page[SleepQualityHistoryItem](
        items=[SleepQualityHistoryItem.model_validate(item) for item in data["items"]],
        limit=data["limit"],
        offset=data["offset"],
        has_more=data["has_more"],
    )


@router.post(
    "/signals/sync",
    response_model=SignalSyncResponse,
    status_code=200,
    summary="Sync screen usage signals",
    description="Ingest batch of screen usage events. Idempotent on (user_id, event_type, event_timestamp). Max 5000 events.",
)
def sync_sleep_signals(payload: SignalSyncRequest, session: SessionDep):
    user_id = session.info["user_id"]
    return SignalSyncResponse.model_validate(signals_service.sync_screen_signals(session, user_id, payload))


@router.post(
    "/estimate",
    response_model=SleepEstimateResponse,
    summary="Estimate sleep candidate for local date",
    description="Estimate sleep candidate from device signals for user's local wake-up date. Returns 201 if estimated, 200 if existing or insufficient data.",
)
def estimate_sleep(payload: SleepEstimateRequest, session: SessionDep, response: Response):
    user_id = session.info["user_id"]
    status_code, data = signals_service.estimate_sleep_session(session, user_id, payload)
    response.status_code = status_code
    return SleepEstimateResponse.model_validate(data)

