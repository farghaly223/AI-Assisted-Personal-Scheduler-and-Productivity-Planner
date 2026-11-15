"""Consistent public errors without request inputs, SQL, passwords or tokens."""

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError
from starlette.exceptions import HTTPException

from app.core.security import AuthenticationError
from app.core.config import ConfigurationError
from app.core.database import UnsafeDatabaseRole
from app.core.schemas import ErrorResponse


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, *, headers=None):
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}
        super().__init__(message)


ERROR_RESPONSES = {status: {"model": ErrorResponse, "description": description} for status, description in {
    400: "Authentication provider rejected registration",
    401: "Missing, invalid or expired access token", 404: "Resource absent or owned by another user",
    409: "Conflict with existing data or missing profile", 422: "Invalid request or combined record",
    429: "Authentication provider rate limit", 502: "Authentication provider failure",
    503: "Required backend dependency unavailable", 500: "Unexpected backend error",
}.items()}


def error_response(status, code, message, *, details=None, headers=None):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details or []}}, headers=headers)


def install_error_handlers(app):
    @app.exception_handler(APIError)
    async def api_error(request: Request, error: APIError):
        return error_response(error.status, error.code, error.message, headers=error.headers)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(error.status_code, "http_error", str(error.detail), headers=error.headers)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request, error):
        details = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]} for item in error.errors()]
        return error_response(422, "invalid_request", "Request validation failed", details=details)

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request, error):
        return error_response(401, "invalid_token", "Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(ConfigurationError)
    @app.exception_handler(UnsafeDatabaseRole)
    async def configuration_error(request, error):
        return error_response(503, "backend_unavailable", "Backend configuration or runtime role is unavailable")

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, error):
        state = getattr(error.orig, "sqlstate", None)
        if state == "23505":
            return error_response(409, "conflict", "Resource conflicts with existing data")
        if state == "23503":
            return error_response(409, "profile_required", "A verified application profile is required")
        return error_response(422, "invalid_request", "Database validation rejected the input")

    @app.exception_handler(DBAPIError)
    async def database_error(request, error):
        return error_response(503, "database_unavailable", "Database operation is unavailable")

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        logging.getLogger("scheduler").error("Unhandled backend error type=%s", type(error).__name__)
        return error_response(500, "internal_error", "Unexpected backend error", headers={"Cache-Control": "no-store"})
