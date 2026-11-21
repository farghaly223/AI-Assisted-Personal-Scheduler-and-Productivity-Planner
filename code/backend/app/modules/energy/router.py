"""Energy module router."""

from fastapi import APIRouter

from app.core.errors import ERROR_RESPONSES
from app.modules.energy.routers import sleep_schedule

router = APIRouter(
    prefix="/energy",
    tags=["Energy"],
    responses=ERROR_RESPONSES,
)
router.include_router(sleep_schedule.router)
