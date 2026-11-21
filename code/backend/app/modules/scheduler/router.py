from fastapi import APIRouter

from app.modules.scheduler.routers import availability, events, generation, tasks

router = APIRouter()
for child_router in (tasks.router, events.router, availability.router, generation.router):
    router.include_router(child_router)