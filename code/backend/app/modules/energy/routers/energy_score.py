"""Energy score endpoints under /energy."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES
from app.modules.energy.schemas import (
    DailySummaryResponse,
    EnergyCalculateRequest,
    EnergyExplanationRead,
    EnergyScoreRead,
    Page,
)
from app.modules.energy.services import energy as service

router = APIRouter(prefix="", tags=["Energy - Score"], responses=ERROR_RESPONSES)


@router.post(
    "/calculate",
    response_model=EnergyScoreRead,
    status_code=200,
    summary="Calculate daily energy score",
    description="Calculate daily energy score from sleep, workload, and recovery, idempotently persisting the result.",
)
def calculate_energy(payload: EnergyCalculateRequest, session: SessionDep):
    user_id = session.info["user_id"]
    data = service.calculate_energy_for_date(
        session, user_id, payload.target_date, payload.timezone
    )
    return EnergyScoreRead.model_validate(data)


@router.get(
    "/current",
    response_model=EnergyScoreRead,
    summary="Get most recent energy score",
    description="Retrieve caller's most recent stored energy score without calculating. Returns 404 if no score exists.",
)
def get_current_energy(session: SessionDep):
    user_id = session.info["user_id"]
    data = service.get_current_energy_score(session, user_id)
    return EnergyScoreRead.model_validate(data)


@router.get(
    "/history",
    response_model=Page[EnergyScoreRead],
    summary="List energy score history",
    description="Paginated list of caller's stored energy scores, newest target_date first. Filterable by date range and status.",
)
def list_energy_history(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
    status: Literal["ok", "insufficient_data"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    user_id = session.info["user_id"]
    data = service.list_energy_history(
        session,
        user_id,
        limit=limit,
        offset=offset,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    return Page[EnergyScoreRead](
        items=[EnergyScoreRead.model_validate(item) for item in data["items"]],
        limit=data["limit"],
        offset=data["offset"],
        has_more=data["has_more"],
    )


@router.get(
    "/daily-summary",
    response_model=DailySummaryResponse,
    summary="Get daily summary",
    description="Read-only daily aggregation: stored energy score, sleep summary, live workload summary, and recovery summary.",
)
def get_daily_summary(
    session: SessionDep,
    date: date = Query(...),
    timezone: str = Query(...),
):
    user_id = session.info["user_id"]
    data = service.get_daily_summary(session, user_id, date, timezone)
    return DailySummaryResponse.model_validate(data)


@router.get(
    "/explanation/{id}",
    response_model=EnergyExplanationRead,
    summary="Get energy score explanation",
    description="Detailed component contribution breakdown and insights for a specific stored energy score. Returns 404 if not found or belongs to another user.",
)
def get_energy_explanation(
    id: UUID,
    session: SessionDep,
):
    user_id = session.info["user_id"]
    data = service.get_explanation(session, user_id, id)
    return EnergyExplanationRead.model_validate(data)
