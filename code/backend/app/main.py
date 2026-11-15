"""Run with: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000."""

from contextlib import asynccontextmanager
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.auth import SupabaseJWTVerifier
from app.config import Settings
from app.db.session import Database, create_database_engine
from app.db.registry import module_routers
from app.dependencies import get_database
from app.errors import ERROR_RESPONSES, install_error_handlers
from app.routers import api_router
from app.openapi import build_openapi


def create_app(*, settings: Settings | None = None, database: Database | None = None,
               verifier: SupabaseJWTVerifier | None = None, auth_client: httpx.Client | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    extra_routers = list(module_routers(settings.modules))
    owns_database = database is None and settings.runtime_url is not None
    if owns_database:
        database = Database(create_database_engine(settings.runtime_url),
                            allow_admin_runtime=settings.allow_admin_database_runtime)
        if settings.allow_admin_database_runtime:
            logging.getLogger("scheduler").warning(
                "Development administrative database access enabled: RLS isolation is not enforced for this connection."
            )
    if verifier is None and settings.supabase_url:
        verifier = SupabaseJWTVerifier(settings.supabase_url, allow_local_http=settings.environment == "development")
    owns_auth_client = auth_client is None
    auth_client = auth_client or httpx.Client(timeout=5, follow_redirects=False, trust_env=False,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            if owns_database and database:
                database.dispose()
            if owns_auth_client:
                auth_client.close()

    application = FastAPI(title="AI-Assisted Personal Scheduler Backend", version="1.0.0",
        description="Sprint 1 user foundation and scheduling input APIs. All user-owned data requires a verified Supabase bearer token.",
        lifespan=lifespan)
    application.state.settings = settings
    application.state.database = database
    application.state.verifier = verifier
    application.state.auth_client = auth_client
    install_error_handlers(application)
    if settings.cors_origins:
        application.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @application.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.get("/api/v1/health", tags=["Health"], summary="Process liveness",
                     responses=ERROR_RESPONSES)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/v1/health/ready", tags=["Health"], summary="Runtime database readiness",
                     responses=ERROR_RESPONSES)
    def readiness(request: Request) -> dict[str, str]:
        get_database(request).check_connection()
        return {"status": "ready"}

    application.include_router(api_router, prefix="/api/v1")
    for router in extra_routers:
        application.include_router(router, prefix="/api/v1")
    application.openapi = lambda: build_openapi(application)
    return application


app = create_app()
