"""Energy module router."""

from fastapi import APIRouter

from app.core.errors import ERROR_RESPONSES
from app.modules.energy.routers import energy_score, recovery, sleep_schedule, sleep_sessions

router = APIRouter(
    prefix="/energy",
    tags=["Energy"],
    responses=ERROR_RESPONSES,
)
router.include_router(energy_score.router)
router.include_router(sleep_schedule.router)
router.include_router(sleep_sessions.router)
router.include_router(recovery.router)

