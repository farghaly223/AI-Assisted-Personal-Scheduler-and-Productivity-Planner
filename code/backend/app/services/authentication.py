"""Supabase Auth protocol adapter. No passwords/users are persisted here."""

import httpx
from pydantic import ValidationError

from app.config import ConfigurationError, Settings
from app.errors import APIError
from app.schemas import AuthResponse, AuthUser, ProfileUpdate, ResendSignupCodeResponse, TokenSession


class SupabaseAuthService:
    def __init__(self, settings: Settings, client: httpx.Client):
        if not settings.supabase_url or not settings.supabase_key:
            raise ConfigurationError("Supabase public Auth configuration is required")
        self.origin, self.key, self.client = settings.supabase_url.rstrip("/"), settings.supabase_key, client
        self.admin_key = settings.auth_admin_key

    @staticmethod
    def check_rate_limit(response: httpx.Response) -> None:
        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "")
            headers = {"Retry-After": retry} if retry.isdigit() and 0 < int(retry) <= 3600 else {}
            raise APIError(429, "auth_rate_limited", "Authentication rate limit exceeded", headers=headers)

    def ensure_email_not_registered(self, email: str) -> None:
        # This credential is used ONLY for a read-only Auth Admin request.
        # Public signup can obscure existing users, including OAuth accounts.
        if not self.admin_key:
            raise APIError(503, "registration_check_unavailable", "Registration availability check is not configured")
        normalized = email.lower()
        # Supabase's filter uses SQL LIKE and also searches profile metadata.
        # Escape wildcard characters and always compare the returned email exactly.
        email_filter = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        headers = {"apikey": self.admin_key}
        if not self.admin_key.startswith("sb_secret_"):
            headers["Authorization"] = "Bearer " + self.admin_key
        try:
            response = self.client.get(self.origin + "/auth/v1/admin/users",
                params={"filter": email_filter, "page": 1, "per_page": 100}, headers=headers)
        except httpx.RequestError:
            raise APIError(503, "registration_check_unavailable", "Registration availability check is unavailable") from None
        self.check_rate_limit(response)
        if response.status_code != 200:
            raise APIError(503, "registration_check_unavailable", "Registration availability check is unavailable")
        try:
            users = response.json()["users"]
            if not isinstance(users, list) or any(not isinstance(user, dict) or (user.get("email") is not None and not isinstance(user["email"], str)) for user in users):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise APIError(502, "invalid_auth_response", "Authentication provider returned an invalid response") from None
        if any((user.get("email") or "").lower() == normalized for user in users):
            raise APIError(409, "email_already_registered", "Email is already registered. Log in or resend the verification code.")
        # Bound the lookup to one request; an ambiguous full page must never
        # permit a signup on the assumption that the email is absent.
        if len(users) >= 100:
            raise APIError(503, "registration_check_unavailable", "Registration availability check could not be completed")

    def resend_signup_code(self, email: str) -> ResendSignupCodeResponse:
        try:
            response = self.client.post(self.origin + "/auth/v1/resend",
                headers={"apikey": self.key, "Content-Type": "application/json"},
                json={"email": email.lower(), "type": "signup"})
        except httpx.RequestError:
            raise APIError(503, "auth_unavailable", "Authentication provider is unavailable") from None
        self.check_rate_limit(response)
        if response.status_code in (400, 401, 403, 422):
            raise APIError(400, "resend_failed", "Verification code request could not be completed")
        if response.status_code != 200:
            raise APIError(502, "auth_provider_error", "Authentication provider could not complete the request")
        try:
            if not isinstance(response.json(), dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise APIError(502, "invalid_auth_response", "Authentication provider returned an invalid response") from None
        # Supabase also returns success for absent/already-confirmed accounts.
        # Do not claim delivery or create a profile/session on this endpoint.
        return ResendSignupCodeResponse()

    def authenticate(self, operation: str, body: dict) -> AuthResponse:
        if operation == "register":
            self.ensure_email_not_registered(body["email"])
            body = body | {"email": body["email"].lower()}
        if operation == "verify_signup":
            # Supabase auth.verifyOtp uses this same HTTP endpoint.
            path, params = "/auth/v1/verify", None
        else:
            path = "/auth/v1/signup" if operation == "register" else "/auth/v1/token"
            params = None if operation == "register" else {"grant_type": "refresh_token" if operation == "refresh" else "password"}
        try:
            response = self.client.post(self.origin + path, params=params,
                headers={"apikey": self.key, "Content-Type": "application/json"}, json=body)
        except httpx.RequestError:
            raise APIError(503, "auth_unavailable", "Authentication provider is unavailable") from None
        self.check_rate_limit(response)
        if response.status_code >= 400:
            if operation == "register":
                try:
                    code = response.json().get("error_code")
                except (ValueError, AttributeError):
                    code = None
                if code in ("user_already_exists", "email_exists"):
                    raise APIError(409, "email_already_registered", "Email is already registered. Log in or resend the verification code.")
            if operation == "verify_signup" and response.status_code in (400, 401, 403, 422):
                raise APIError(401, "invalid_otp", "Verification code is invalid or expired")
            if operation != "register" and response.status_code in (400, 401, 403, 422):
                raise APIError(401, "invalid_credentials", "Credentials or refresh token are invalid")
            if operation == "register" and response.status_code in (400, 409, 422):
                raise APIError(400, "registration_failed", "Registration could not be completed")
            raise APIError(502, "auth_provider_error", "Authentication provider could not complete the request")
        try:
            data = response.json()
            user_data = data.get("user") or data
            if operation == "register" and user_data.get("identities") == []:
                raise APIError(409, "email_already_registered", "Email is already registered. Log in or resend the verification code.")
            user = AuthUser(id=user_data["id"], email=user_data.get("email"))
            token_data = data.get("session") or data
            session = None
            if "access_token" in token_data:
                session = TokenSession(**{name: token_data[name] for name in TokenSession.model_fields if name in token_data})
            if operation != "register" and session is None:
                raise ValueError()
            result = AuthResponse(user=user, session=session, confirmation_required=session is None)
            metadata = user_data.get("user_metadata")
            if isinstance(metadata, dict):
                for name in ("username", "full_name"):
                    if name in metadata:
                        try:
                            value = ProfileUpdate.model_validate({name: metadata[name]})
                            result._profile_values[name] = getattr(value, name)
                        except ValidationError:
                            # Never persist invalid external metadata or trust owner fields.
                            pass
            return result
        except (ValueError, TypeError, KeyError, AttributeError, ValidationError):
            raise APIError(502, "invalid_auth_response", "Authentication provider returned an invalid response") from None
