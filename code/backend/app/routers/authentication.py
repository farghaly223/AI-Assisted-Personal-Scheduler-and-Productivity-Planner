from fastapi import APIRouter, Request

from app.auth import AuthenticationError
from app.dependencies import get_database, get_verifier
from app.errors import APIError, ERROR_RESPONSES
from app.schemas import AuthResponse, LoginRequest, RefreshRequest, RegisterRequest, ResendSignupCodeRequest, ResendSignupCodeResponse, SignupVerificationRequest
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
    description="Public endpoint. Rejects existing confirmed or unconfirmed email accounts with 409 after a server-side Auth Admin lookup. That lookup requires SUPABASE_SERVICE_ROLE_KEY; ordinary Auth requests use the public key. With email confirmation enabled and the DAYLI Confirm signup template installed, sends a six-digit signup code. A pending confirmation returns session null; call /auth/verify-otp with the email and code. No profile is created until successful verification or verified login. Never persists passwords.")
def register(payload: RegisterRequest, request: Request):
    metadata = {name: getattr(payload, name) for name in ("username", "full_name") if getattr(payload, name) is not None}
    body = {"email": payload.email, "password": payload.password.get_secret_value(), "data": metadata}
    return complete_auth(request, "register", body, profile_values=metadata)


@router.post("/verify-otp", response_model=AuthResponse, summary="Verify a six-digit signup email code",
    description="Public endpoint; no bearer token required. Accepts email and a six-digit ASCII token string, including leading zeros. Calls Supabase verifyOtp with type signup. Returns a verified session and provisions the own profile/default preferences through the existing transaction. Invalid, expired or reused codes return 401; malformed input returns 422; provider rate limits return 429. Never stores or logs the code.")
def verify_signup_otp(payload: SignupVerificationRequest, request: Request):
    return complete_auth(request, "verify_signup", {
        "email": payload.email,
        "token": payload.token.get_secret_value(),
        "type": "signup",
    })


@router.post("/resend-otp", response_model=ResendSignupCodeResponse, status_code=202,
    summary="Resend a signup email verification code",
    description="Public endpoint; accepts email only, without a bearer token. Uses Supabase resend with type signup and the existing Confirm signup email template. Returns a generic accepted response for unconfirmed, confirmed or absent accounts; only unconfirmed accounts receive mail. Does not create an account, profile or session. Provider rate limits return 429 with Retry-After when available; invalid input returns 422, provider rejection 400, and provider/connectivity failures 502/503.")
def resend_signup_otp(payload: ResendSignupCodeRequest, request: Request):
    return SupabaseAuthService(request.app.state.settings, request.app.state.auth_client).resend_signup_code(payload.email)


@router.post("/login", response_model=AuthResponse, summary="Log in through Supabase Auth",
    description="Public endpoint. Exchanges email/password for a verified access token and refresh token. Invalid credentials use a generic 401 response. Passwords are never stored.")
def login(payload: LoginRequest, request: Request):
    return complete_auth(request, "login", {"email": payload.email, "password": payload.password.get_secret_value()})


@router.post("/refresh", response_model=AuthResponse, summary="Refresh a Supabase session",
    description="Public endpoint accepting the private refresh token. Returns the rotated session; clients must replace the previous refresh token. The returned access token is verified before use.")
def refresh(payload: RefreshRequest, request: Request):
    return complete_auth(request, "refresh", {"refresh_token": payload.refresh_token.get_secret_value()})
