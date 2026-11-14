"""Own-profile onboarding and preferences. Supabase identity is never duplicated."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.models import Profile, UserPreferences
from app.errors import APIError
from app.schemas import PreferencesValues


def ensure_profile(session: Session, *, username=None, full_name=None) -> Profile:
    user_id = session.info["user_id"]
    profile = session.scalar(sa.select(Profile).where(Profile.id == user_id))
    if profile is not None:
        return profile
    # Same-user concurrency is safe; a conflicting username still fails normally.
    def insert_profile(name):
        session.execute(insert(Profile).values(id=user_id, username=name, full_name=full_name)
                        .on_conflict_do_nothing(index_elements=[Profile.id]))
    if username is not None:
        try:
            with session.begin_nested():
                insert_profile(username)
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) != "23505" or getattr(error.orig.diag, "constraint_name", None) != "uq_profiles_username":
                raise
            # Auth has already succeeded. Preserve a usable account and default
            # preferences; the caller can choose a unique username with PATCH.
            insert_profile(None)
    else:
        insert_profile(None)
    return session.scalars(sa.select(Profile).where(Profile.id == user_id)).one()


def own_preferences(session: Session) -> UserPreferences:
    ensure_profile(session)
    preferences = session.scalar(sa.select(UserPreferences).where(UserPreferences.user_id == session.info["user_id"]))
    if preferences is None:
        raise APIError(503, "preferences_unavailable", "Default preferences have not been provisioned")
    return preferences


def update_preferences(session: Session, patch) -> UserPreferences:
    row = own_preferences(session)
    merged = {name: getattr(row, name) for name in PreferencesValues.model_fields}
    changes = patch.model_dump(exclude_unset=True)
    PreferencesValues.model_validate(merged | changes)
    for name, value in changes.items():
        setattr(row, name, value)
    session.flush()
    return row
