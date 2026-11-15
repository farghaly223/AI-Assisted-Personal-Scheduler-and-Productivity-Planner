"""Verify Supabase asymmetric access tokens before any database identity is set."""

import os
import math
from dataclasses import dataclass
from uuid import UUID

import jwt

from app.core.config import ConfigurationError, load_environment, validate_supabase_origin


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedIdentity:
    user_id: UUID


class SupabaseJWTVerifier:
    ALGORITHMS = ("ES256", "RS256")

    def __init__(self, supabase_url: str, audience: str = "authenticated", *, allow_local_http: bool = False):
        supabase_url = validate_supabase_origin(supabase_url, allow_local_http=allow_local_http)
        if audience != "authenticated":
            raise ConfigurationError("Only authenticated Supabase access tokens are supported")
        self.issuer = supabase_url.rstrip("/") + "/auth/v1"
        self.audience = audience
        # This URL is trusted configuration, never the token's jku/x5u/iss value.
        self._jwks = jwt.PyJWKClient(self.issuer + "/.well-known/jwks.json", cache_jwk_set=True, lifespan=300, timeout=5)

    @classmethod
    def from_environment(cls) -> "SupabaseJWTVerifier":
        load_environment()
        return cls(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_JWT_AUDIENCE", "authenticated"))

    def verify(self, token: str) -> VerifiedIdentity:
        try:
            if not isinstance(token, str) or not token or len(token) > 32768:
                raise AuthenticationError()
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in self.ALGORITHMS or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise AuthenticationError()
            key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=list(self.ALGORITHMS),
                audience=self.audience, issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss", "role"]},
            )
            if claims["role"] != "authenticated":
                raise AuthenticationError()
            for name in ("exp", "iat", "nbf"):
                if name in claims and (isinstance(claims[name], bool) or not isinstance(claims[name], (int, float)) or not math.isfinite(claims[name])):
                    raise AuthenticationError()
            user_id = UUID(claims["sub"])
            if user_id.int == 0:
                raise AuthenticationError()
            return VerifiedIdentity(user_id)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            raise AuthenticationError("Invalid, expired, or unverifiable Supabase access token") from None
