"""Environment configuration without logging credentials or connecting at import."""

import os
import json
import base64
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigurationError(ValueError):
    pass


def load_environment() -> None:
    load_dotenv(BACKEND_ROOT / ".env", override=False)


def database_url(variable: str) -> URL:
    load_environment()
    value = os.environ.get(variable)
    if not value:
        raise ConfigurationError(f"Set {variable} in the environment or backend/.env")
    return validate_database_url(value)


def validate_database_url(value: str | URL) -> URL:
    try:
        url = make_url(value)
    except Exception:
        raise ConfigurationError("Invalid PostgreSQL URL; credentials are not displayed") from None
    if url.drivername != "postgresql+psycopg" or not url.host or not url.database or not url.username:
        raise ConfigurationError("Use a complete postgresql+psycopg TCP connection URL")
    if set(url.query) - {"sslmode", "sslrootcert"}:
        raise ConfigurationError("Only sslmode/sslrootcert URL parameters are supported; endpoint overrides are forbidden")
    if url.port == 6543:
        raise ConfigurationError("Use Supabase direct or session-mode port 5432, not transaction-mode port 6543")
    if url.host not in LOCAL_HOSTS and url.query.get("sslmode") != "verify-full":
        raise ConfigurationError("Remote database connections require sslmode=verify-full with trusted certificates")
    return url


def require_local(url: URL) -> None:
    if url.host not in LOCAL_HOSTS:
        raise ConfigurationError("This command is restricted to a disposable local PostgreSQL instance")


def validate_supabase_origin(value: str, *, allow_local_http: bool = False) -> str:
    try:
        parsed = urlparse(value)
        valid_transport = parsed.scheme == "https" or (
            allow_local_http and parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS
        )
        if not valid_transport or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or value != value.strip():
            raise ValueError()
        _ = parsed.port
    except ValueError:
        raise ConfigurationError("Use a trusted HTTPS Supabase origin; loopback HTTP requires development mode") from None
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    environment: str = "production"
    runtime_url: str | None = field(default=None, repr=False)
    supabase_url: str | None = None
    supabase_key: str | None = field(default=None, repr=False)
    cors_origins: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    allow_admin_database_runtime: bool = False
    auth_admin_key: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.environment not in ("development", "production"):
            raise ConfigurationError("APP_ENV must be development or production")
        if self.allow_admin_database_runtime and self.environment != "development":
            raise ConfigurationError("ALLOW_ADMIN_DATABASE_RUNTIME is permitted only with APP_ENV=development")
        if self.runtime_url:
            validate_database_url(self.runtime_url)
        if self.supabase_url:
            validate_supabase_origin(self.supabase_url, allow_local_http=self.environment == "development")
        if self.supabase_key:
            if not self.supabase_key.startswith("sb_publishable_"):
                try:
                    part = self.supabase_key.split(".")[1]
                    claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
                    if claims.get("role") != "anon":
                        raise ValueError()
                except (ValueError, IndexError, TypeError, UnicodeError, AttributeError):
                    raise ConfigurationError("Use only a Supabase publishable or anon key, never a service-role key") from None
        if self.auth_admin_key and not self.auth_admin_key.startswith("sb_secret_"):
            try:
                part = self.auth_admin_key.split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
                if claims.get("role") != "service_role":
                    raise ValueError()
            except (ValueError, IndexError, TypeError, UnicodeError, AttributeError):
                raise ConfigurationError("SUPABASE_SERVICE_ROLE_KEY must be a backend secret or legacy service-role key") from None
        for origin in self.cors_origins:
            validate_supabase_origin(origin, allow_local_http=self.environment == "development")

    @classmethod
    def from_environment(cls, *, load_file: bool = True):
        if load_file:
            load_environment()
        allow_admin = os.environ.get("ALLOW_ADMIN_DATABASE_RUNTIME", "false").strip().lower()
        if allow_admin not in ("true", "false"):
            raise ConfigurationError("ALLOW_ADMIN_DATABASE_RUNTIME must be true or false")
        return cls(
            environment=os.environ.get("APP_ENV", "production"),
            runtime_url=os.environ.get("DATABASE_URL") or None,
            supabase_url=os.environ.get("SUPABASE_URL") or None,
            supabase_key=os.environ.get("SUPABASE_PUBLIC_KEY") or os.environ.get("SUPABASE_ANON_KEY") or None,
            cors_origins=tuple(value.strip() for value in os.environ.get("CORS_ORIGINS", "").split(",") if value.strip()),
            modules=tuple(value.strip() for value in os.environ.get("BACKEND_MODULES", "").split(",") if value.strip()),
            allow_admin_database_runtime=allow_admin == "true",
            auth_admin_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or None,
        )
