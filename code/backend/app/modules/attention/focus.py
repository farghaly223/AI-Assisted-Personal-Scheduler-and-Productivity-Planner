"""The single shared Focus Session entity is owned by Attention."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, owner_fk


class FocusSession(Base):
    __tablename__ = "focus_sessions"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_focus_sessions"),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'ABANDONED')",
            name="ck_focus_sessions_status",
        ),
        sa.CheckConstraint(
            "(status = 'PLANNED' AND start_time IS NULL AND end_time IS NULL AND planned_duration_minutes IS NOT NULL) OR "
            "(status = 'CANCELLED' AND start_time IS NULL AND end_time IS NULL AND planned_duration_minutes IS NOT NULL) OR "
            "(status = 'IN_PROGRESS' AND start_time IS NOT NULL AND isfinite(start_time) AND end_time IS NULL) OR "
            "(status IN ('COMPLETED','CANCELLED','ABANDONED') AND start_time IS NOT NULL AND end_time IS NOT NULL AND isfinite(start_time) AND isfinite(end_time) AND end_time > start_time)",
            name="ck_focus_sessions_times",
        ),
        sa.CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name="ck_focus_sessions_duration",
        ),
        sa.Index(
            "uq_focus_sessions_user_in_progress",
            "user_id",
            unique=True,
            postgresql_where=sa.text("status = 'IN_PROGRESS'"),
        ),
        sa.Index("ix_focus_sessions_user_start", "user_id", "start_time"),
        sa.UniqueConstraint("user_id", "client_session_key", name="uq_focus_sessions_client"),
        sa.CheckConstraint("planned_duration_minutes IS NULL OR planned_duration_minutes > 0", name="ck_focus_sessions_planned_duration"),
        sa.Index("ix_focus_sessions_task", "task_id"),
        sa.Index("ix_focus_sessions_item", "schedule_item_id"),
        {"info": {"owner_module": "attention", "purpose": "The single shared focus-session entity; Energy consumes its observations."}},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), owner_fk("fk_focus_sessions_profile"), nullable=False
    )
    start_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    planned_duration_minutes: Mapped[int | None] = mapped_column(sa.Integer)
    task_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.tasks.task_id", name="fk_focus_sessions_task", ondelete="SET NULL", onupdate="RESTRICT"))
    schedule_item_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_items.id", name="fk_focus_sessions_item", ondelete="SET NULL", onupdate="RESTRICT"))
    client_session_key: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'IN_PROGRESS'")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        server_onupdate=sa.FetchedValue(),
    )

