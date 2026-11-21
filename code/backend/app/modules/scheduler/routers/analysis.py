from fastapi import APIRouter

from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES
from app.modules.scheduler.analysis_schemas import (
    ConflictsRead, ProposalRequest, ProposalResultRead, ScheduleStatisticsRead)
from app.modules.scheduler.services.analysis_service import read_conflicts, read_statistics, validate_proposal

router = APIRouter(prefix="/scheduler", tags=["Scheduler analysis"], responses=ERROR_RESPONSES)


@router.post("/validate", response_model=ProposalResultRead, summary="Validate a proposed schedule",
    description="Requires bearer token. Checks up to 500 proposed placements without saving: overlaps between them, overlaps with the user's real events and fixed tasks, deadline violations, fixed tasks moved from their time, and flexible tasks outside configured availability. is_valid is false when any ERROR exists.")
def validate(payload: ProposalRequest, session: SessionDep):
    return validate_proposal(session, payload.items)


@router.get("/conflicts", response_model=ConflictsRead, summary="Check whether the current schedule is still valid",
    description="Requires bearer token. Compares the current published schedule with today's events, fixed tasks, deadlines and availability: overlaps, deadline or availability violations, commitments that changed or were removed (COMMITMENT_CHANGED) and commitments added since generation (NEW_COMMITMENT). Returns 404 when there is no current schedule.")
def conflicts(session: SessionDep):
    return read_conflicts(session)


@router.get("/statistics", response_model=ScheduleStatisticsRead, summary="Statistics of the current schedule",
    description="Requires bearer token. Counts and minutes of the current version: placed and unscheduled tasks, flexible minutes, commitment minutes, availability inside the version window, unused availability, locked items and completion ratio. Returns 404 when there is no current schedule.")
def statistics(session: SessionDep):
    return read_statistics(session)