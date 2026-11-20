"""Versioned decision executions and alternatives, not duplicated source entities."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, owner_fk


class DecisionExecution(Base):
    __tablename__ = "decision_executions"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_decision_executions"),
        sa.UniqueConstraint("user_id", "execution_key", name="uq_decision_executions_key"),
        sa.CheckConstraint("algorithm_version ~ '[^[:space:]]' AND input_schema_version > 0", name="ck_decision_executions_version"),
        sa.CheckConstraint("jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536", name="ck_decision_executions_snapshot"),
        sa.CheckConstraint("overall_schedule_score IS NULL OR overall_schedule_score BETWEEN 0 AND 100", name="ck_decision_executions_score"),
        sa.CheckConstraint("isfinite(started_at) AND ((status='RUNNING' AND finished_at IS NULL) OR (status IN ('SUCCEEDED','FAILED') AND finished_at IS NOT NULL AND isfinite(finished_at) AND finished_at >= started_at))", name="ck_decision_executions_status"),
        sa.CheckConstraint("status <> 'SUCCEEDED' OR (explanation IS NOT NULL AND explanation ~ '[^[:space:]]')", name="ck_decision_executions_explanation"),
        sa.Index("ix_decision_executions_user_time", "user_id", "started_at", "id"),
        sa.Index("ix_decision_executions_schedule", "input_schedule_version_id"),
        sa.Index("ix_decision_executions_energy", "energy_score_id"),
        sa.Index("ix_decision_executions_attention", "attention_score_id"),
        {"info": {"owner_module": "decision", "purpose": "Auditable algorithm runs with exact source-score references and versioned inputs.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_decision_executions_profile"))
    execution_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    input_schedule_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_versions.id", name="fk_decision_executions_schedule", ondelete="SET NULL", onupdate="RESTRICT"))
    energy_score_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.energy_scores.id", name="fk_decision_executions_energy", ondelete="SET NULL", onupdate="RESTRICT"))
    attention_score_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.attention_scores.id", name="fk_decision_executions_attention", ondelete="SET NULL", onupdate="RESTRICT"))
    algorithm_version: Mapped[str] = mapped_column(sa.String(100))
    input_schema_version: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("1"))
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(sa.String(9), server_default=sa.text("'RUNNING'"))
    overall_schedule_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    explanation: Mapped[str | None] = mapped_column(sa.String(4000))
    failure_code: Mapped[str | None] = mapped_column(sa.String(100))
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class DecisionCandidate(Base):
    __tablename__ = "decision_candidates"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_decision_candidates"),
        sa.UniqueConstraint("decision_execution_id", "candidate_key", name="uq_decision_candidates_key"),
        sa.UniqueConstraint("decision_execution_id", "rank", name="uq_decision_candidates_rank"),
        sa.CheckConstraint("kind IN ('TASK_SLOT','BREAK','POSTPONE') AND (rank IS NULL OR rank > 0)", name="ck_decision_candidates_kind"),
        sa.CheckConstraint("(start_at IS NULL AND end_at IS NULL) OR (start_at IS NOT NULL AND end_at IS NOT NULL AND isfinite(start_at) AND isfinite(end_at) AND end_at > start_at)", name="ck_decision_candidates_interval"),
        sa.CheckConstraint("kind <> 'TASK_SLOT' OR start_at IS NOT NULL", name="ck_decision_candidates_slot"),
        sa.CheckConstraint("selection_status IN ('CONSIDERED','SELECTED','REJECTED')", name="ck_decision_candidates_selection"),
        sa.CheckConstraint("(priority_score IS NULL OR priority_score BETWEEN 0 AND 100) AND (deadline_urgency_score IS NULL OR deadline_urgency_score BETWEEN 0 AND 100) AND (energy_match_score IS NULL OR energy_match_score BETWEEN 0 AND 100) AND (attention_match_score IS NULL OR attention_match_score BETWEEN 0 AND 100) AND (task_time_score IS NULL OR task_time_score BETWEEN 0 AND 100)", name="ck_decision_candidates_components"),
        sa.CheckConstraint("jsonb_typeof(task_snapshot)='object' AND octet_length(task_snapshot::text)<=65536", name="ck_decision_candidates_snapshot"),
        sa.Index("ix_decision_candidates_user", "user_id"),
        sa.Index("ix_decision_candidates_task", "task_id"),
        {"info": {"owner_module": "decision", "purpose": "Selected/rejected task-time, break and postponement alternatives with component scores and reasons.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_decision_candidates_profile"))
    decision_execution_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.decision_executions.id", name="fk_decision_candidates_execution", ondelete="CASCADE", onupdate="RESTRICT"))
    candidate_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    task_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.tasks.task_id", name="fk_decision_candidates_task", ondelete="SET NULL", onupdate="RESTRICT"))
    kind: Mapped[str] = mapped_column(sa.String(9))
    start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    rank: Mapped[int | None] = mapped_column(sa.Integer)
    selection_status: Mapped[str] = mapped_column(sa.String(10), server_default=sa.text("'CONSIDERED'"))
    priority_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    deadline_urgency_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    energy_match_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    attention_match_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    task_time_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    task_snapshot: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    explanation: Mapped[str | None] = mapped_column(sa.String(4000))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
