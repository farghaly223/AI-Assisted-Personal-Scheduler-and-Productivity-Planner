from fastapi import APIRouter

from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES
from app.modules.user.schemas import PreferencesRead, PreferencesUpdate, ProfileRead, ProfileUpdate
from app.modules.user.services.profiles import ensure_profile, own_preferences, update_preferences

router = APIRouter(tags=["User foundation"], responses=ERROR_RESPONSES)


@router.get("/profiles/me", response_model=ProfileRead, summary="Retrieve own profile",
    description="Requires bearer token. Idempotently provisions a missing own profile and trigger-created preferences. No user ID is accepted. Example response includes id, username, full_name, created_at and updated_at.")
def get_profile(session: SessionDep):
    return ProfileRead.model_validate(ensure_profile(session))


@router.patch("/profiles/me", response_model=ProfileRead, summary="Update own profile",
    description="Requires bearer token. Only username (case-sensitive unique, max 100) and full_name (max 200) are editable; null clears either. Example: {\"full_name\": \"Student Name\"}. IDs and timestamps are server-managed.")
def patch_profile(payload: ProfileUpdate, session: SessionDep):
    row = ensure_profile(session)
    for name, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    session.flush()
    return ProfileRead.model_validate(row)


@router.get("/preferences/me", response_model=PreferencesRead, summary="Retrieve own preferences",
    description="Requires bearer token. Defaults are UTC, notifications disabled, and null work hours. The profile trigger guarantees one preferences record per profile.")
def get_preferences(session: SessionDep):
    return PreferencesRead.model_validate(own_preferences(session))


@router.patch("/preferences/me", response_model=PreferencesRead, summary="Update own preferences",
    description="Requires bearer token. Validate IANA timezone and boolean notifications_enabled. Work endpoints must both be null or both supplied and unequal; earlier end means overnight. Example: {\"timezone\": \"Africa/Cairo\", \"preferred_work_start\": \"09:00:00\", \"preferred_work_end\": \"17:00:00\"}.")
def patch_preferences(payload: PreferencesUpdate, session: SessionDep):
    return PreferencesRead.model_validate(update_preferences(session, payload))
