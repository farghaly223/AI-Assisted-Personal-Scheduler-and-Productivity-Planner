"""Attention observations, instrument history and versioned score results."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, owner_fk
from app.modules.attention.focus import FocusSession


class UsageSyncBatch(Base):
    __tablename__ = "usage_sync_batches"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_usage_sync_batches"),
        sa.UniqueConstraint("user_id", "device_id", "client_batch_id", name="uq_usage_batches_client"),
        sa.UniqueConstraint("user_id", "device_id", "local_date", "timezone", "revision", name="uq_usage_batches_revision"),
        sa.CheckConstraint("revision > 0 AND isfinite(local_date)", name="ck_usage_batches_revision"),
        sa.CheckConstraint("isfinite(window_start_at) AND isfinite(window_end_at) AND window_end_at > window_start_at", name="ck_usage_batches_window"),
        sa.CheckConstraint("screen_active_seconds IS NULL OR screen_active_seconds >= 0", name="ck_usage_batches_screen"),
        sa.CheckConstraint("app_switch_count IS NULL OR app_switch_count >= 0", name="ck_usage_batches_switches"),
        sa.CheckConstraint("(status='PROCESSED' AND processed_at IS NOT NULL AND isfinite(processed_at)) OR (status IN ('RECEIVED','FAILED') AND processed_at IS NULL)", name="ck_usage_batches_status"),
        sa.CheckConstraint("isfinite(expires_at) AND expires_at >= created_at AND expires_at <= created_at + INTERVAL '90 days'", name="ck_usage_batches_expiry"),
        sa.Index("ix_usage_batches_user_latest", "user_id", "device_id", "local_date", "revision"),
        sa.Index("ix_usage_batches_expiry", "expires_at"),
        {"info": {"owner_module": "attention", "purpose": "Idempotent cumulative device/day usage snapshots; aggregate only the latest processed revision.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_usage_batches_profile"))
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    client_batch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    local_date: Mapped[date] = mapped_column(sa.Date)
    timezone: Mapped[str] = mapped_column(sa.String(100))
    revision: Mapped[int] = mapped_column(sa.Integer)
    window_start_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    window_end_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    screen_active_seconds: Mapped[int | None] = mapped_column(sa.Integer)
    app_switch_count: Mapped[int | None] = mapped_column(sa.Integer)
    status: Mapped[str] = mapped_column(sa.String(9), server_default=sa.text("'RECEIVED'"))
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now() + INTERVAL '90 days'"))


class AppClassification(Base):
    __tablename__ = "app_classifications"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_app_classifications"),
        sa.UniqueConstraint("user_id", "app_package", name="uq_app_classifications_package"),
        sa.CheckConstraint("app_package=btrim(app_package) AND app_package ~ '^[A-Za-z0-9_.]+$'", name="ck_app_classifications_package"),
        sa.CheckConstraint("classification IN ('PRODUCTIVE','NEUTRAL','DISTRACTING')", name="ck_app_classifications_kind"),
        {"info": {"owner_module": "attention", "purpose": "User-specific app classification overrides; absence uses the module's versioned default.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_app_classifications_profile"))
    app_package: Mapped[str] = mapped_column(sa.String(255))
    classification: Mapped[str] = mapped_column(sa.String(11))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"), server_onupdate=sa.FetchedValue())


class AppUsageSummary(Base):
    __tablename__ = "app_usage_summaries"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_app_usage_summaries"),
        sa.UniqueConstraint("batch_id", "app_package", name="uq_app_usage_summaries_package"),
        sa.CheckConstraint("foreground_seconds >= 0 AND (foreground_transitions IS NULL OR foreground_transitions >= 0)", name="ck_app_usage_summaries_metrics"),
        sa.CheckConstraint("app_package=btrim(app_package) AND app_package ~ '^[A-Za-z0-9_.]+$'", name="ck_app_usage_summaries_package"),
        sa.Index("ix_app_usage_summaries_user", "user_id"),
        {"info": {"owner_module": "attention", "purpose": "Minimal per-app cumulative summary belonging to one device/day revision; no app content.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_app_usage_summaries_profile"))
    batch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.usage_sync_batches.id", name="fk_app_usage_summaries_batch", ondelete="CASCADE", onupdate="RESTRICT"))
    app_package: Mapped[str] = mapped_column(sa.String(255))
    foreground_seconds: Mapped[int] = mapped_column(sa.Integer)
    foreground_transitions: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))


class AttentionAssessment(Base):
    __tablename__ = "attention_assessments"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_attention_assessments"),
        sa.CheckConstraint("questionnaire_version ~ '[^[:space:]]'", name="ck_attention_assessments_version"),
        sa.CheckConstraint("jsonb_typeof(questionnaire_snapshot)='object' AND COALESCE(jsonb_typeof(questionnaire_snapshot->'questions')='array' AND jsonb_array_length(questionnaire_snapshot->'questions')>0,false) AND octet_length(questionnaire_snapshot::text)<=65536", name="ck_attention_assessments_instrument"),
        sa.CheckConstraint("result_score IS NULL OR result_score BETWEEN 0 AND 100", name="ck_attention_assessments_score"),
        sa.CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_attention_assessments_confidence"),
        sa.CheckConstraint("(status='DRAFT' AND completed_at IS NULL) OR (status='COMPLETED' AND completed_at IS NOT NULL AND isfinite(completed_at) AND calculation_version IS NOT NULL AND calculation_version ~ '[^[:space:]]')", name="ck_attention_assessments_status"),
        sa.Index("ix_attention_assessments_user_created", "user_id", "created_at", "id"),
        {"info": {"owner_module": "attention", "purpose": "Versioned questionnaire instrument, completion and result snapshot; no invented questions/weights.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_attention_assessments_profile"))
    questionnaire_version: Mapped[str] = mapped_column(sa.String(100))
    questionnaire_snapshot: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(sa.String(9), server_default=sa.text("'DRAFT'"))
    result_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    result_summary: Mapped[str | None] = mapped_column(sa.String(4000))
    confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 4))
    calculation_version: Mapped[str | None] = mapped_column(sa.String(100))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))


class AttentionQuestionnaireResponse(Base):
    __tablename__ = "attention_questionnaire_responses"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_attention_questionnaire_responses"),
        sa.UniqueConstraint("assessment_id", "question_key", name="uq_attention_responses_question"),
        sa.CheckConstraint("question_key ~ '[^[:space:]]' AND answer_value ~ '[^[:space:]]'", name="ck_attention_responses_value"),
        sa.Index("ix_attention_responses_user", "user_id"),
        {"info": {"owner_module": "attention", "purpose": "Canonical answer strings tied to question keys in the captured instrument definition.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_attention_responses_profile"))
    assessment_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.attention_assessments.id", name="fk_attention_responses_assessment", ondelete="CASCADE", onupdate="RESTRICT"))
    question_key: Mapped[str] = mapped_column(sa.String(100))
    answer_value: Mapped[str] = mapped_column(sa.String(2000))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))


class DistractionEvent(Base):
    __tablename__ = "distraction_events"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_distraction_events"),
        sa.UniqueConstraint("user_id", "client_key", name="uq_distraction_events_client"),
        sa.CheckConstraint("isfinite(start_at) AND isfinite(end_at) AND end_at > start_at", name="ck_distraction_events_interval"),
        sa.CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_distraction_events_confidence"),
        sa.CheckConstraint("detection_source ~ '[^[:space:]]' AND detector_version ~ '[^[:space:]]'", name="ck_distraction_events_source"),
        sa.CheckConstraint("(status='PROBABLE' AND reviewed_at IS NULL) OR (status IN ('CONFIRMED','IGNORED') AND reviewed_at IS NOT NULL AND isfinite(reviewed_at))", name="ck_distraction_events_status"),
        sa.Index("ix_distraction_events_focus", "focus_session_id", "start_at"),
        sa.Index("ix_distraction_events_user", "user_id", "start_at", "id"),
        {"info": {"owner_module": "attention", "purpose": "Inferred focus-context distractions with confidence and explicit user acceptance/rejection.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_distraction_events_profile"))
    focus_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.focus_sessions.id", name="fk_distraction_events_focus", ondelete="CASCADE", onupdate="RESTRICT"))
    start_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    end_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    app_package: Mapped[str | None] = mapped_column(sa.String(255))
    detection_source: Mapped[str] = mapped_column(sa.String(100))
    detector_version: Mapped[str] = mapped_column(sa.String(100))
    confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 4))
    status: Mapped[str] = mapped_column(sa.String(9), server_default=sa.text("'PROBABLE'"))
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    client_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))


class AttentionScore(Base):
    __tablename__ = "attention_scores"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_attention_scores"),
        sa.UniqueConstraint("user_id", "calculation_key", name="uq_attention_scores_calculation"),
        sa.CheckConstraint("isfinite(target_date)", name="ck_attention_scores_date"),
        sa.CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="ck_attention_scores_range"),
        sa.CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_attention_scores_confidence"),
        sa.CheckConstraint("attention_level IS NULL OR attention_level IN ('LOW','MEDIUM','HIGH')", name="ck_attention_scores_level"),
        sa.CheckConstraint("(status='ok' AND score IS NOT NULL AND attention_level IS NOT NULL) OR (status='insufficient_data' AND score IS NULL AND attention_level IS NULL)", name="ck_attention_scores_status"),
        sa.CheckConstraint("calculation_version ~ '[^[:space:]]' AND explanation ~ '[^[:space:]]' AND input_schema_version > 0", name="ck_attention_scores_version"),
        sa.CheckConstraint("jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536", name="ck_attention_scores_snapshot"),
        sa.CheckConstraint("(average_focus_seconds IS NULL OR (average_focus_seconds>=0 AND average_focus_seconds<'NaN'::numeric)) AND (distraction_count IS NULL OR distraction_count>=0) AND (total_distraction_seconds IS NULL OR total_distraction_seconds>=0) AND (focus_completion_rate IS NULL OR focus_completion_rate BETWEEN 0 AND 1)", name="ck_attention_scores_metrics"),
        sa.CheckConstraint("trend IS NULL OR trend IN ('IMPROVING','STABLE','DECLINING')", name="ck_attention_scores_trend"),
        sa.Index("ix_attention_scores_user_date", "user_id", "target_date", "calculated_at", "id"),
        sa.Index("ix_attention_scores_assessment", "assessment_id"),
        {"info": {"owner_module": "attention", "purpose": "Immutable versioned score results and historical focus/distraction metrics; NULL is missing.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_attention_scores_profile"))
    target_date: Mapped[date] = mapped_column(sa.Date)
    timezone: Mapped[str] = mapped_column(sa.String(100))
    status: Mapped[str] = mapped_column(sa.String(20))
    score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    attention_level: Mapped[str | None] = mapped_column(sa.String(6))
    confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 4))
    average_focus_seconds: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 3))
    distraction_count: Mapped[int | None] = mapped_column(sa.Integer)
    total_distraction_seconds: Mapped[int | None] = mapped_column(sa.BigInteger)
    focus_completion_rate: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 4))
    trend: Mapped[str | None] = mapped_column(sa.String(9))
    assessment_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.attention_assessments.id", name="fk_attention_scores_assessment", ondelete="SET NULL", onupdate="RESTRICT"))
    calculation_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    calculation_version: Mapped[str] = mapped_column(sa.String(100))
    input_schema_version: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("1"))
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(sa.String(4000))
    calculated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
