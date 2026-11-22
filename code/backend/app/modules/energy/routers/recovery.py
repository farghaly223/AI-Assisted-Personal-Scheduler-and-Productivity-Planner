"""Recovery endpoints under /energy/recovery."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES
from app.modules.energy.schemas import (
    FocusSessionFinishRequest,
    FocusSessionRead,
    FocusSessionStartRequest,
    Page,
    RecoveryDetectRequest,
    RecoveryDetectResponse,
    RecoveryPeriodRead,
    RecoveryPeriodSource,
    RecoveryPeriodType,
    RecoveryStrategyCreateOrUpdate,
    RecoveryStrategyRead,
    RecoverySummaryResponse,
)
from app.modules.energy.services import recovery as service

router = APIRouter(prefix="/recovery", tags=["Energy - Recovery"], responses=ERROR_RESPONSES)


@router.put(
    "/strategy",
    response_model=RecoveryStrategyRead,
    summary="Create or replace recovery strategy",
    description="Configure or update user focus/recovery strategy. Upserts safely; re-enables is_active. Extra fields forbidden.",
)
def upsert_recovery_strategy(payload: RecoveryStrategyCreateOrUpdate, session: SessionDep):
    user_id = session.info["user_id"]
    row = service.upsert_strategy(session, user_id, payload)
    return RecoveryStrategyRead.model_validate(row)


@router.get(
    "/strategy",
    response_model=RecoveryStrategyRead,
    summary="Retrieve own recovery strategy",
    description="Retrieve caller's recovery strategy. Returns 404 if no strategy configured.",
)
def get_recovery_strategy(session: SessionDep):
    user_id = session.info["user_id"]
    row = service.get_strategy(session, user_id)
    return RecoveryStrategyRead.model_validate(row)


@router.post(
    "/sessions/start",
    response_model=FocusSessionRead,
    status_code=201,
    summary="Start focus session",
    description="Start a new focus session in IN_PROGRESS state. Returns 409 if a session is already in progress.",
)
def start_focus_session(session: SessionDep, payload: FocusSessionStartRequest | None = None):
    user_id = session.info["user_id"]
    row = service.start_focus_session(session, user_id, payload)
    return FocusSessionRead.model_validate(row)


@router.post(
    "/sessions/{id}/finish",
    response_model=FocusSessionRead,
    status_code=200,
    summary="Finish focus session",
    description="Finish an IN_PROGRESS focus session with COMPLETED, CANCELLED, or ABANDONED status. Uses atomic compare-and-set.",
)
def finish_focus_session(id: UUID, payload: FocusSessionFinishRequest, session: SessionDep):
    user_id = session.info["user_id"]
    row = service.finish_focus_session(session, user_id, id, payload)
    return FocusSessionRead.model_validate(row)


@router.get(
    "/sessions",
    response_model=Page[FocusSessionRead],
    summary="List own focus sessions",
    description="Paginated list of caller's focus sessions, newest first. Optional status and local date bounds in given timezone.",
)
def list_focus_sessions(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
    status: Literal["IN_PROGRESS", "COMPLETED", "CANCELLED", "ABANDONED"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone: str = Query("UTC"),
):
    user_id = session.info["user_id"]
    data = service.list_focus_sessions(
        session,
        user_id,
        limit=limit,
        offset=offset,
        status=status,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone,
    )
    return Page[FocusSessionRead](
        items=[FocusSessionRead.model_validate(item) for item in data["items"]],
        limit=data["limit"],
        offset=data["offset"],
        has_more=data["has_more"],
    )


@router.get(
    "/periods",
    response_model=Page[RecoveryPeriodRead],
    summary="List own recovery periods",
    description="Paginated list of caller's recovery periods, newest first. Filterable by source, recovery_type, and local date bounds.",
)
def list_recovery_periods(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
    source: RecoveryPeriodSource | None = None,
    recovery_type: RecoveryPeriodType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone: str = Query("UTC"),
):
    user_id = session.info["user_id"]
    data = service.list_recovery_periods(
        session,
        user_id,
        limit=limit,
        offset=offset,
        source=source,
        recovery_type=recovery_type,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone,
    )
    return Page[RecoveryPeriodRead](
        items=[RecoveryPeriodRead.model_validate(item) for item in data["items"]],
        limit=data["limit"],
        offset=data["offset"],
        has_more=data["has_more"],
    )


@router.post(
    "/detect",
    response_model=RecoveryDetectResponse,
    summary="Detect recovery breaks and score",
    description="Analyze completed focus sessions for the local day, compute recovery score, and idempotently persist DETECTED periods.",
)
def detect_recovery(payload: RecoveryDetectRequest, session: SessionDep):
    user_id = session.info["user_id"]
    data = service.detect_recovery(session, user_id, payload)
    return RecoveryDetectResponse.model_validate(data)


@router.get(
    "/summary",
    response_model=RecoverySummaryResponse,
    summary="Get recovery summary for day",
    description="Aggregated focus and recovery metrics for caller on a specific local calendar day.",
)
def get_recovery_summary(
    session: SessionDep,
    date: date = Query(...),
    timezone: str = Query("UTC"),
):
    user_id = session.info["user_id"]
    data = service.get_recovery_summary(session, user_id, date, timezone)
    return RecoverySummaryResponse.model_validate(data)
