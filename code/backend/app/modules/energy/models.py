"""Energy module models using the shared Base and profile ownership."""

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIME, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, owner_fk
from app.modules.attention.focus import FocusSession  # Compatibility reexport; one entity.


class SleepSchedule(Base):
    __tablename__ = "sleep_schedules"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_sleep_schedules"),
        sa.UniqueConstraint("user_id", name="uq_sleep_schedules_user_id"),
        sa.CheckConstraint(
            "bedtime < TIME '24:00:00' AND wake_time < TIME '24:00:00' AND bedtime <> wake_time",
            name="ck_sleep_schedules_times",
        ),
        sa.CheckConstraint("reminder_minutes_before >= 0", name="ck_sleep_schedules_reminder"),
        sa.CheckConstraint(
            "repeat_days <@ ARRAY[0,1,2,3,4,5,6]::smallint[] AND array_ndims(repeat_days) = 1 AND cardinality(repeat_days) > 0",
            name="ck_sleep_schedules_repeat_days",
        ),
        sa.CheckConstraint(
            "timezone = btrim(timezone) AND timezone ~ '[^[:space:]]'",
            name="ck_sleep_schedules_timezone_nonblank",
        ),
        sa.Index("ix_sleep_schedules_user_id", "user_id"),
        sa.CheckConstraint("public.dayli_valid_repeat_days(repeat_days)", name="ck_sleep_schedules_days_unique"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), owner_fk("fk_sleep_schedules_profile"), nullable=False
    )
    bedtime: Mapped[time] = mapped_column(TIME(precision=0, timezone=False), nullable=False)
    wake_time: Mapped[time] = mapped_column(TIME(precision=0, timezone=False), nullable=False)
    timezone: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    repeat_days: Mapped[list[int]] = mapped_column(
        ARRAY(sa.SmallInteger), nullable=False, server_default=sa.text("'{0,1,2,3,4,5,6}'::smallint[]")
    )
    bedtime_reminder_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    reminder_minutes_before: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("15")
    )
    wake_alarm_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
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


class SleepSession(Base):
    __tablename__ = "sleep_sessions"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_sleep_sessions"),
        sa.UniqueConstraint("user_id", "id", name="uq_sleep_sessions_user_id_id"),
        sa.UniqueConstraint("user_id", "start_time", "end_time", name="uq_sleep_sessions_user_interval"),
        sa.CheckConstraint(
            "isfinite(start_time) AND isfinite(end_time) AND end_time > start_time",
            name="ck_sleep_sessions_interval",
        ),
        sa.CheckConstraint("duration_minutes > 0", name="ck_sleep_sessions_duration_positive"),
        sa.CheckConstraint(
            "duration_minutes = ROUND(EXTRACT(EPOCH FROM (end_time - start_time)) / 60)::integer",
            name="ck_sleep_sessions_duration_rounded_minutes",
        ),
        sa.CheckConstraint(
            "status IN ('pending_confirmation', 'confirmed', 'corrected', 'rejected')",
            name="ck_sleep_sessions_status",
        ),
        sa.CheckConstraint(
            "source IN ('android_usage', 'manual')",
            name="ck_sleep_sessions_source",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_sleep_sessions_confidence",
        ),
        sa.CheckConstraint(
            "timezone = btrim(timezone) AND timezone ~ '[^[:space:]]'",
            name="ck_sleep_sessions_timezone_nonblank",
        ),
        sa.Index(
            "uq_sleep_sessions_user_sleep_date",
            "user_id",
            "sleep_date",
            unique=True,
            postgresql_where=sa.text("status <> 'rejected'"),
        ),
        sa.Index("ix_sleep_sessions_user_start", "user_id", "start_time"),
        sa.Index(
            "ix_sleep_sessions_user_pending",
            "user_id",
            "status",
            postgresql_where=sa.text("status = 'pending_confirmation'"),
        ),
        sa.CheckConstraint("isfinite(sleep_date)", name="ck_sleep_sessions_date_finite"),
        sa.CheckConstraint("(confirmed_start_at IS NULL AND confirmed_end_at IS NULL) OR (confirmed_start_at IS NOT NULL AND confirmed_end_at IS NOT NULL AND isfinite(confirmed_start_at) AND isfinite(confirmed_end_at) AND confirmed_end_at > confirmed_start_at)", name="ck_sleep_sessions_confirmed_interval"),
        sa.CheckConstraint("(status='pending_confirmation' AND confirmed_start_at IS NULL AND confirmed_end_at IS NULL AND confirmed_at IS NULL) OR (status IN ('confirmed','corrected') AND confirmed_start_at IS NOT NULL AND confirmed_end_at IS NOT NULL AND confirmed_at IS NOT NULL AND isfinite(confirmed_at)) OR status='rejected'", name="ck_sleep_sessions_confirmation"),
        sa.CheckConstraint("confirmation_method IS NULL OR confirmation_method IN ('USER_ACCEPTED','USER_CORRECTED','MANUAL','LEGACY')", name="ck_sleep_sessions_confirmation_method"),
        sa.Index("ix_sleep_sessions_batch", "batch_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), owner_fk("fk_sleep_sessions_profile"), nullable=False
    )
    sleep_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    start_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    timezone: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    source: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'android_usage'")
    )
    confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    confirmed_start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    confirmed_end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    confirmation_method: Mapped[str | None] = mapped_column(sa.String(20))
    detection_version: Mapped[str | None] = mapped_column(sa.String(100))
    batch_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.usage_sync_batches.id", name="fk_sleep_sessions_batch", ondelete="SET NULL", onupdate="RESTRICT"))
    status: Mapped[str] = mapped_column(
        sa.String(30), nullable=False, server_default=sa.text("'pending_confirmation'")
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


class SleepUsageSignal(Base):
    __tablename__ = "sleep_usage_signals"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_sleep_usage_signals"),
        sa.ForeignKeyConstraint(
            ["user_id", "session_id"],
            ["public.sleep_sessions.user_id", "public.sleep_sessions.id"],
            name="fk_sleep_usage_signals_session",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        sa.UniqueConstraint("user_id", "device_id", "client_event_id", name="uq_sleep_usage_signals_client"),
        sa.CheckConstraint(
            "event_type = btrim(event_type) AND event_type ~ '[^[:space:]]'",
            name="ck_sleep_usage_signals_type_nonblank",
        ),
        sa.CheckConstraint("isfinite(event_timestamp)", name="ck_sleep_usage_signals_timestamp_finite"),
        sa.Index("ix_sleep_usage_signals_user_time", "user_id", "event_timestamp"),
        sa.Index("ix_sleep_usage_signals_session", "session_id"),
        sa.Index("ix_sleep_usage_signals_batch", "batch_id"),
        sa.Index("ix_sleep_usage_signals_expiry", "expires_at"),
        sa.CheckConstraint("isfinite(expires_at) AND expires_at >= created_at AND expires_at <= created_at + INTERVAL '7 days'", name="ck_sleep_usage_signals_expiry"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), owner_fk("fk_sleep_usage_signals_profile"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    client_event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    batch_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.usage_sync_batches.id", name="fk_sleep_usage_signals_batch", ondelete="SET NULL", onupdate="RESTRICT"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now() + INTERVAL '7 days'"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class SleepQualityRating(Base):
    __tablename__ = "sleep_quality_ratings"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_sleep_quality_ratings"),
        sa.UniqueConstraint("session_id", name="uq_sleep_quality_ratings_session_id"),
        sa.ForeignKeyConstraint(
            ["user_id", "session_id"],
            ["public.sleep_sessions.user_id", "public.sleep_sessions.id"],
            name="fk_sleep_quality_ratings_session",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_sleep_quality_ratings_range"),
        sa.Index("ix_sleep_quality_ratings_user", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), owner_fk("fk_sleep_quality_ratings_profile"), nullable=False
    )
    rating: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    notes: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        server_onupdate=sa.FetchedValue(),
    )


class RecoveryStrategy(Base):
    __tablename__ = "recovery_strategies"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_recovery_strategies"),
        sa.UniqueConstraint("user_id", name="uq_recovery_strategies_user_id"),
        sa.CheckConstraint("strategy_type IN ('POMODORO', 'CUSTOM')", name="ck_recovery_strategies_type"),
        sa.CheckConstraint(
            "work_duration_minutes > 0 AND break_duration_minutes > 0 AND long_break_duration_minutes > 0 AND long_break_after_sessions > 0",
            name="ck_recovery_strategies_durations",
        ),
        sa.Index("ix_recovery_strategies_user", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), owner_fk("fk_recovery_strategies_profile"), nullable=False
    )
    strategy_type: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'POMODORO'")
    )
    work_duration_minutes: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("25")
    )
    break_duration_minutes: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("5")
    )
    long_break_duration_minutes: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("20")
    )
    long_break_after_sessions: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("4")
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
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




class RecoveryPeriod(Base):
    __tablename__ = "recovery_periods"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_recovery_periods"),
        sa.CheckConstraint(
            "isfinite(start_time) AND isfinite(end_time) AND end_time > start_time",
            name="ck_recovery_periods_times",
        ),
        sa.CheckConstraint("duration_minutes > 0", name="ck_recovery_periods_duration_positive"),
        sa.CheckConstraint(
            "duration_minutes = ROUND(EXTRACT(EPOCH FROM (end_time - start_time)) / 60)::integer",
            name="ck_recovery_periods_duration_rounded_minutes",
        ),
        sa.CheckConstraint(
            "source IN ('FOCUS_TIMER', 'DETECTED', 'MANUAL', 'PLANNED')",
            name="ck_recovery_periods_source",
        ),
        sa.CheckConstraint(
            "recovery_type IN ('SHORT_BREAK', 'LONG_BREAK', 'CUSTOM_BREAK', 'REST')",
            name="ck_recovery_periods_type",
        ),
        sa.Index("ix_recovery_periods_user_start", "user_id", "start_time"),
        sa.UniqueConstraint("user_id", "client_key", name="uq_recovery_periods_client"),
        sa.CheckConstraint("(planned_start_at IS NULL AND planned_end_at IS NULL) OR (planned_start_at IS NOT NULL AND planned_end_at IS NOT NULL AND isfinite(planned_start_at) AND isfinite(planned_end_at) AND planned_end_at > planned_start_at)", name="ck_recovery_periods_planned"),
        sa.CheckConstraint("(observed_start_at IS NULL AND observed_end_at IS NULL) OR (observed_start_at IS NOT NULL AND observed_end_at IS NOT NULL AND isfinite(observed_start_at) AND isfinite(observed_end_at) AND observed_end_at > observed_start_at)", name="ck_recovery_periods_observed"),
        sa.CheckConstraint("(status='TAKEN' AND observed_start_at IS NOT NULL AND observed_end_at IS NOT NULL) OR (status IN ('PLANNED','MISSED') AND observed_start_at IS NULL AND observed_end_at IS NULL AND (planned_start_at IS NOT NULL OR schedule_item_id IS NOT NULL))", name="ck_recovery_periods_status"),
        sa.Index("ix_recovery_periods_focus", "focus_session_id"),
        sa.Index("ix_recovery_periods_strategy", "strategy_id"),
        sa.Index("ix_recovery_periods_item", "schedule_item_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), owner_fk("fk_recovery_periods_profile"), nullable=False
    )
    start_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    planned_start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    planned_end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    observed_start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    observed_end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(sa.String(7), server_default=sa.text("'TAKEN'"))
    focus_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.focus_sessions.id", name="fk_recovery_periods_focus", ondelete="SET NULL", onupdate="RESTRICT"))
    strategy_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.recovery_strategies.id", name="fk_recovery_periods_strategy", ondelete="SET NULL", onupdate="RESTRICT"))
    schedule_item_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_items.id", name="fk_recovery_periods_item", ondelete="NO ACTION", onupdate="RESTRICT", deferrable=True, initially="DEFERRED"))
    client_key: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'FOCUS_TIMER'")
    )
    recovery_type: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'SHORT_BREAK'")
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


class EnergyScore(Base):
    __tablename__ = "energy_scores"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_energy_scores"),
        sa.UniqueConstraint("user_id", "calculation_key", name="uq_energy_scores_calculation"),
        sa.CheckConstraint(
            "status IN ('ok', 'insufficient_data')",
            name="ck_energy_scores_status",
        ),
        sa.CheckConstraint(
            "((status = 'ok' AND score IS NOT NULL AND energy_level IS NOT NULL) OR "
            "(status = 'insufficient_data' AND score IS NULL AND energy_level IS NULL))",
            name="ck_energy_scores_status_score_level",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0.0 AND score <= 100.0)",
            name="ck_energy_scores_score_range",
        ),
        sa.CheckConstraint(
            "energy_level IS NULL OR energy_level IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_energy_scores_level",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_energy_scores_confidence",
        ),
        sa.CheckConstraint(
            "sleep_duration_minutes IS NULL OR sleep_duration_minutes > 0",
            name="ck_energy_scores_sleep_duration",
        ),
        sa.CheckConstraint(
            "sleep_quality_score IS NULL OR (sleep_quality_score >= 0.0 AND sleep_quality_score <= 100.0)",
            name="ck_energy_scores_sleep_quality_score",
        ),
        sa.CheckConstraint(
            "sleep_quality_rating IS NULL OR (sleep_quality_rating BETWEEN 1 AND 5)",
            name="ck_energy_scores_sleep_quality_rating",
        ),
        sa.CheckConstraint(
            "workload_score IS NULL OR (workload_score >= 0.0 AND workload_score <= 100.0)",
            name="ck_energy_scores_workload_score",
        ),
        sa.CheckConstraint(
            "workload_level IS NULL OR workload_level IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_energy_scores_workload_level",
        ),
        sa.CheckConstraint(
            "recovery_score IS NULL OR (recovery_score >= 0.0 AND recovery_score <= 100.0)",
            name="ck_energy_scores_recovery_score",
        ),
        sa.CheckConstraint(
            "recovery_level IS NULL OR recovery_level IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_energy_scores_recovery_level",
        ),
        sa.Index("ix_energy_scores_user_calculated", "user_id", "calculated_at"),
        sa.Index("ix_energy_scores_user_date", "user_id", "target_date"),
        sa.Index("ix_energy_scores_sleep", "sleep_session_id"),
        sa.Index("ix_energy_scores_history", "user_id", "target_date", "calculated_at", "id"),
        sa.CheckConstraint("isfinite(target_date) AND input_schema_version > 0 AND calculation_version ~ '[^[:space:]]'", name="ck_energy_scores_version"),
        sa.CheckConstraint("jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536", name="ck_energy_scores_snapshot"),
        sa.CheckConstraint("calculation_version='legacy_unversioned' OR (timezone IS NOT NULL AND explanation IS NOT NULL AND explanation ~ '[^[:space:]]')", name="ck_energy_scores_provenance"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), owner_fk("fk_energy_scores_profile"), nullable=False
    )
    target_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'ok'")
    )
    score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2), nullable=True)
    energy_level: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)
    confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    sleep_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.sleep_sessions.id", name="fk_energy_scores_sleep", ondelete="SET NULL", onupdate="RESTRICT"))
    timezone: Mapped[str | None] = mapped_column(sa.String(100))
    calculation_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    calculation_version: Mapped[str] = mapped_column(sa.String(100))
    input_schema_version: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("1"))
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    explanation: Mapped[str | None] = mapped_column(sa.String(4000))
    sleep_duration_minutes: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    sleep_quality_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2), nullable=True)
    sleep_quality_rating: Mapped[int | None] = mapped_column(sa.SmallInteger, nullable=True)
    workload_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2), nullable=True)
    workload_level: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)
    recovery_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2), nullable=True)
    recovery_level: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)
    insights: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
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
