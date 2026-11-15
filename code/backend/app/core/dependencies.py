"""Shared verified authentication and transaction dependencies for every module."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import AuthenticationError, SupabaseJWTVerifier
from app.core.database import Database
from app.core.config import ConfigurationError

bearer = HTTPBearer(auto_error=False, description="Supabase access token returned by login or refresh")


def get_database(request: Request) -> Database:
    database = request.app.state.database
    if database is None:
        raise ConfigurationError("Runtime database is not configured")
    return database


def get_verifier(request: Request) -> SupabaseJWTVerifier:
    verifier = request.app.state.verifier
    if verifier is None:
        raise ConfigurationError("Supabase access-token verification is not configured")
    return verifier


def get_session(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Iterator[Session]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Bearer access token required", headers={"WWW-Authenticate": "Bearer"})
    verifier = get_verifier(request)
    # Database.user_session verifies before acquiring any database connection.
    with get_database(request).user_session(credentials.credentials, verifier) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session, scope="function")]


def session_dependency(database: Database, verifier: SupabaseJWTVerifier):
    bearer = HTTPBearer(auto_error=False)

    def dependency(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Iterator[Session]:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(401, "Bearer access token required", headers={"WWW-Authenticate": "Bearer"})
        try:
            with database.user_session(credentials.credentials, verifier) as session:
                yield session
        except AuthenticationError:
            raise HTTPException(401, "Invalid access token", headers={"WWW-Authenticate": "Bearer"}) from None

    return dependency
