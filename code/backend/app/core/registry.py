"""Explicit module discovery shared by the application and Alembic."""

import importlib
import os
import re

from fastapi import APIRouter

from app.core.config import ConfigurationError, load_environment
from app.core.models import APPLICATION_TABLES, Base


def configured_modules() -> tuple[str, ...]:
    load_environment()
    return tuple(value.strip() for value in os.environ.get("BACKEND_MODULES", "").split(",") if value.strip())


def import_module_part(package: str, part: str):
    if not re.fullmatch(r"app\.modules\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", package):
        raise ConfigurationError("Modules must be named packages under app.modules")
    try:
        return importlib.import_module(package + "." + part)
    except ImportError:
        raise ConfigurationError("Configured backend module could not be imported") from None


def managed_table_names() -> frozenset[str]:
    return frozenset(table.name for table in Base.metadata.tables.values()
                     if table.schema == "public" and not table.info.get("external"))


def load_module_models(packages: tuple[str, ...]) -> None:
    for package in dict.fromkeys(packages):
        import_module_part(package, "models")
    for table in Base.metadata.tables.values():
        if table.schema != "public" or table.info.get("external"):
            raise ConfigurationError("Shared Base must contain application public tables only")
        if table.name not in APPLICATION_TABLES:
            owner = table.c.get("user_id")
            if owner is None or owner.nullable or not any(fk.target_fullname == "public.profiles.id" for fk in owner.foreign_keys):
                raise ConfigurationError("Module tables require a non-null user_id foreign key to public.profiles.id")


def module_routers(packages: tuple[str, ...]):
    load_module_models(packages)
    for package in dict.fromkeys(packages):
        router = getattr(import_module_part(package, "router"), "router", None)
        if not isinstance(router, APIRouter):
            raise ConfigurationError("Module router.py must expose an APIRouter named router")
        yield router
