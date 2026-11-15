"""Profile and preferences tables; Supabase Auth remains external."""

from datetime import datetime, time
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIME, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, owner_fk

# A resolved external FK target in SEPARATE metadata. It is not a mapped model,
# is not in Base.metadata, and is never passed to Alembic/create_all/drop_all.


_external_metadata = sa.MetaData()


_auth_users_reference = sa.Table(
    "users", _external_metadata, sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
    schema="auth", info={"external": True},
)


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_profiles"),
        sa.UniqueConstraint("username", name="uq_profiles_username"),
        sa.CheckConstraint("username IS NULL OR (username = btrim(username) AND username ~ '[^[:space:]]')", name="ck_profiles_username_nonblank"),
        sa.CheckConstraint("full_name IS NULL OR full_name ~ '[^[:space:]]'", name="ck_profiles_full_name_nonblank"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey(_auth_users_reference.c.id, name="fk_profiles_auth_user", ondelete="CASCADE", onupdate="RESTRICT"), primary_key=True)
    username: Mapped[str | None] = mapped_column(sa.String(100))
    full_name: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"), server_onupdate=sa.FetchedValue())
    preferences: Mapped["UserPreferences"] = relationship(back_populates="profile", passive_deletes="all")
    tasks: Mapped[list["Task"]] = relationship(back_populates="profile", passive_deletes="all")
    events: Mapped[list["Event"]] = relationship(back_populates="profile", passive_deletes="all")


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (
        sa.PrimaryKeyConstraint("preference_id", name="pk_user_preferences"),
        sa.UniqueConstraint("user_id", name="uq_user_preferences_user_id"),
        sa.CheckConstraint("timezone = btrim(timezone) AND timezone ~ '[^[:space:]]'", name="ck_user_preferences_timezone_nonblank"),
        sa.CheckConstraint("""
            (preferred_work_start IS NULL AND preferred_work_end IS NULL)
            OR (preferred_work_start IS NOT NULL AND preferred_work_end IS NOT NULL
                AND preferred_work_start < TIME '24:00:00'
                AND preferred_work_end < TIME '24:00:00'
                AND preferred_work_start <> preferred_work_end)
        """, name="ck_user_preferences_work_hours"),
    )
    preference_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_user_preferences_profile"))
    timezone: Mapped[str] = mapped_column(sa.String(100), server_default=sa.text("'UTC'"))
    notifications_enabled: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.false())
    preferred_work_start: Mapped[time | None] = mapped_column(TIME(precision=0, timezone=False))
    preferred_work_end: Mapped[time | None] = mapped_column(TIME(precision=0, timezone=False))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"), server_onupdate=sa.FetchedValue())
    profile: Mapped[Profile] = relationship(back_populates="preferences")
