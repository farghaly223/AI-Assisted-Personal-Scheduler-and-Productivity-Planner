from fastapi import APIRouter

from app.routers import authentication, events, profiles, tasks

api_router = APIRouter()
for router in (authentication.router, profiles.router, tasks.router, events.router):
    api_router.include_router(router)
