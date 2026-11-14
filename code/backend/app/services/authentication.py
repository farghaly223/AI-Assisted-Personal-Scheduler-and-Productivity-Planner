"""Supabase Auth protocol adapter. No passwords/users are persisted here."""

import httpx
from pydantic import ValidationError

from app.config import ConfigurationError, Settings
from app.errors import APIError
from app.schemas import AuthResponse, AuthUser, ProfileUpdate, TokenSession


class SupabaseAuthService:
    def __init__(self, settings: Settings, client: httpx.Client):
        if not settings.supabase_url or not settings.supabase_key:
            raise ConfigurationError("Supabase public Auth configuration is required")
        self.origin, self.key, self.client = settings.supabase_url.rstrip("/"), settings.supabase_key, client

    def authenticate(self, operation: str, body: dict) -> AuthResponse:
        path = "/auth/v1/signup" if operation == "register" else "/auth/v1/token"
        params = None if operation == "register" else {"grant_type": "refresh_token" if operation == "refresh" else "password"}
        try:
            response = self.client.post(self.origin + path, params=params,
                headers={"apikey": self.key, "Content-Type": "application/json"}, json=body)
        except httpx.RequestError:
            raise APIError(503, "auth_unavailable", "Authentication provider is unavailable") from None
        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "")
            headers = {"Retry-After": retry} if retry.isdigit() and 0 < int(retry) <= 3600 else {}
            raise APIError(429, "auth_rate_limited", "Authentication rate limit exceeded", headers=headers)
        if response.status_code >= 400:
            if operation != "register" and response.status_code in (400, 401, 403, 422):
                raise APIError(401, "invalid_credentials", "Credentials or refresh token are invalid")
            if operation == "register" and response.status_code in (400, 409, 422):
                raise APIError(400, "registration_failed", "Registration could not be completed")
            raise APIError(502, "auth_provider_error", "Authentication provider could not complete the request")
        try:
            data = response.json()
            user_data = data.get("user") or data
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
