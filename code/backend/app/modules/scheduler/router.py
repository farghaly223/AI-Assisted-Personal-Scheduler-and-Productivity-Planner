from fastapi import APIRouter

from app.modules.scheduler.routers import availability, events, tasks

router = APIRouter()
for child_router in (tasks.router, events.router, availability.router):
    router.include_router(child_router)