from fastapi import APIRouter, Request

from app.auth import AuthenticationError
from app.dependencies import get_database, get_verifier
from app.errors import APIError, ERROR_RESPONSES
from app.schemas import AuthResponse, LoginRequest, RefreshRequest, RegisterRequest
from app.services.authentication import SupabaseAuthService
from app.services.profiles import ensure_profile

router = APIRouter(prefix="/auth", tags=["Authentication"], responses=ERROR_RESPONSES)


def complete_auth(request: Request, operation: str, body: dict, *, profile_values=None):
    result = SupabaseAuthService(request.app.state.settings, request.app.state.auth_client).authenticate(operation, body)
    if result.session is not None:
        try:
            if operation == "refresh":
                identity = get_verifier(request).verify(result.session.access_token)
                if identity.user_id != result.user.id:
                    raise AuthenticationError("Authentication response identity mismatch")
                return result
            with get_database(request).user_session(result.session.access_token, get_verifier(request)) as session:
                if session.info["user_id"] != result.user.id:
                    raise AuthenticationError("Authentication response identity mismatch")
                ensure_profile(session, **(profile_values or result._profile_values))
        except AuthenticationError:
            raise APIError(502, "invalid_auth_response", "Authentication provider returned an unverifiable session") from None
    return result


@router.post("/register", response_model=AuthResponse, status_code=201, summary="Register through Supabase Auth",
    description="Public endpoint. Email confirmation may be required; in that case session is null and no application profile is created until verified login. Never persists passwords. Example: email student@example.com, password a strong private password, optional full_name.")
def register(payload: RegisterRequest, request: Request):
    metadata = {name: getattr(payload, name) for name in ("username", "full_name") if getattr(payload, name) is not None}
    body = {"email": payload.email, "password": payload.password.get_secret_value(), "data": metadata}
    return complete_auth(request, "register", body, profile_values=metadata)


@router.post("/login", response_model=AuthResponse, summary="Log in through Supabase Auth",
    description="Public endpoint. Exchanges email/password for a verified access token and refresh token. Invalid credentials use a generic 401 response. Passwords are never stored.")
def login(payload: LoginRequest, request: Request):
    return complete_auth(request, "login", {"email": payload.email, "password": payload.password.get_secret_value()})


@router.post("/refresh", response_model=AuthResponse, summary="Refresh a Supabase session",
    description="Public endpoint accepting the private refresh token. Returns the rotated session; clients must replace the previous refresh token. The returned access token is verified before use.")
def refresh(payload: RefreshRequest, request: Request):
    return complete_auth(request, "refresh", {"refresh_token": payload.refresh_token.get_secret_value()})
