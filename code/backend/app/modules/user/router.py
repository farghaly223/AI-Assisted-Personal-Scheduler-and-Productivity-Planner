from fastapi import APIRouter

from app.modules.user.routers import authentication, profiles

router = APIRouter()
for child_router in (authentication.router, profiles.router):
    router.include_router(child_router)
