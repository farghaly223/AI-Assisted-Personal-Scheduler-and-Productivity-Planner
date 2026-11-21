from fastapi import APIRouter

from app.modules.scheduler.routers import (
    analysis, availability, availability_windows, events, generation, schedule, schedule_items, tasks)

router = APIRouter()
for child_router in (tasks.router, events.router, availability.router, availability_windows.router,
                     generation.router, generation.regenerate_router, schedule.router,
                     schedule_items.router, analysis.router):
    router.include_router(child_router)