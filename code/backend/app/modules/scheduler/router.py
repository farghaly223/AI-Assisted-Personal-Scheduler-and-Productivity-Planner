from fastapi import APIRouter

from app.modules.scheduler.routers import tasks, events

router = APIRouter()
for child_router in (tasks.router, events.router):
    router.include_router(child_router)
