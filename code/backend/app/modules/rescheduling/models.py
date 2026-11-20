"""Rescheduling request and affected-item history with explicit version links."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, owner_fk


class ReschedulingRun(Base):
    __tablename__ = "rescheduling_runs"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_rescheduling_runs"),
        sa.UniqueConstraint("user_id", "request_key", name="uq_rescheduling_runs_key"),
        sa.CheckConstraint("trigger_type IN ('TASK_MISSED','TASK_DELAYED','AVAILABILITY_CHANGED','ENERGY_CHANGED','ATTENTION_CHANGED','TASK_ADDED','EVENT_ADDED','INPUT_CHANGED','CONFLICT','MANUAL')", name="ck_rescheduling_runs_trigger"),
        sa.CheckConstraint("reason ~ '[^[:space:]]'", name="ck_rescheduling_runs_reason"),
        sa.CheckConstraint("isfinite(requested_at) AND ((status='PENDING' AND finished_at IS NULL) OR (status='PROPOSED' AND new_version_id IS NOT NULL AND finished_at IS NULL) OR (status IN ('APPLIED','REJECTED') AND new_version_id IS NOT NULL AND finished_at IS NOT NULL AND isfinite(finished_at) AND finished_at >= requested_at) OR (status='FAILED' AND finished_at IS NOT NULL AND isfinite(finished_at) AND finished_at >= requested_at))", name="ck_rescheduling_runs_status"),
        sa.CheckConstraint("new_version_id IS NULL OR new_version_id <> previous_version_id", name="ck_rescheduling_runs_versions"),
        sa.Index("ix_rescheduling_runs_user_time", "user_id", "requested_at", "id"),
        sa.Index("ix_rescheduling_runs_previous", "previous_version_id"),
        sa.Index("ix_rescheduling_runs_new", "new_version_id"),
        sa.Index("ix_rescheduling_runs_decision", "decision_execution_id"),
        {"info": {"owner_module": "rescheduling", "purpose": "Previous/proposed/applied version history with reasons, decisions and user acceptance state.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_rescheduling_runs_profile"))
    request_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    previous_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_versions.id", name="fk_rescheduling_runs_previous", ondelete="NO ACTION", onupdate="RESTRICT", deferrable=True, initially="DEFERRED"))
    new_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_versions.id", name="fk_rescheduling_runs_new", ondelete="NO ACTION", onupdate="RESTRICT", deferrable=True, initially="DEFERRED"))
    decision_execution_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.decision_executions.id", name="fk_rescheduling_runs_decision", ondelete="SET NULL", onupdate="RESTRICT"))
    trigger_type: Mapped[str] = mapped_column(sa.String(32))
    reason: Mapped[str] = mapped_column(sa.String(4000))
    status: Mapped[str] = mapped_column(sa.String(8), server_default=sa.text("'PENDING'"))
    requested_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class ReschedulingChange(Base):
    __tablename__ = "rescheduling_changes"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_rescheduling_changes"),
        sa.CheckConstraint("(change_type='ADDED' AND previous_item_id IS NULL AND new_item_id IS NOT NULL) OR (change_type='REMOVED' AND previous_item_id IS NOT NULL AND new_item_id IS NULL) OR (change_type='MOVED' AND previous_item_id IS NOT NULL AND new_item_id IS NOT NULL)", name="ck_rescheduling_changes_shape"),
        sa.Index("ix_rescheduling_changes_run", "rescheduling_run_id"),
        sa.Index("ix_rescheduling_changes_user", "user_id"),
        sa.Index("ix_rescheduling_changes_previous", "previous_item_id"),
        sa.Index("ix_rescheduling_changes_new", "new_item_id"),
        {"info": {"owner_module": "rescheduling", "purpose": "Affected old/new item references; immutable schedule versions provide interval history.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_rescheduling_changes_profile"))
    rescheduling_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.rescheduling_runs.id", name="fk_rescheduling_changes_run", ondelete="CASCADE", onupdate="RESTRICT"))
    previous_item_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_items.id", name="fk_rescheduling_changes_previous", ondelete="NO ACTION", onupdate="RESTRICT", deferrable=True, initially="DEFERRED"))
    new_item_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_items.id", name="fk_rescheduling_changes_new", ondelete="NO ACTION", onupdate="RESTRICT", deferrable=True, initially="DEFERRED"))
    change_type: Mapped[str] = mapped_column(sa.String(7))
    explanation: Mapped[str | None] = mapped_column(sa.String(2000))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
