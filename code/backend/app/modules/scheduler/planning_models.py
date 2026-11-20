"""Versioned schedule outputs and task extensions; existing input APIs stay frozen."""

from datetime import date, datetime, time
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ExcludeConstraint, JSONB, TIME, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, owner_fk


class TaskRequirement(Base):
    __tablename__ = "task_requirements"
    __table_args__ = (
        sa.PrimaryKeyConstraint("task_id", name="pk_task_requirements"),
        sa.CheckConstraint("minimum_session_minutes IS NULL OR (can_split AND minimum_session_minutes > 0)", name="ck_task_requirements_minimum"),
        sa.CheckConstraint("(preferred_start_time IS NULL AND preferred_end_time IS NULL AND preferred_timezone IS NULL) OR (preferred_start_time IS NOT NULL AND preferred_end_time IS NOT NULL AND preferred_timezone IS NOT NULL AND preferred_start_time < TIME '24:00' AND preferred_end_time < TIME '24:00' AND preferred_start_time <> preferred_end_time)", name="ck_task_requirements_preference"),
        sa.CheckConstraint("required_energy_level IS NULL OR required_energy_level IN ('LOW','MEDIUM','HIGH')", name="ck_task_requirements_energy"),
        sa.CheckConstraint("required_attention_level IS NULL OR required_attention_level IN ('LOW','MEDIUM','HIGH')", name="ck_task_requirements_attention"),
        sa.Index("ix_task_requirements_user", "user_id"),
        {"info": {"owner_module": "scheduler", "purpose": "One optional task planning extension; no duplicate task entity or importance.", "introduced_in": "0003"}},
    )
    task_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.tasks.task_id", name="fk_task_requirements_task", ondelete="CASCADE", onupdate="RESTRICT"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_task_requirements_profile"))
    can_split: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.false())
    minimum_session_minutes: Mapped[int | None] = mapped_column(sa.Integer)
    preferred_start_time: Mapped[time | None] = mapped_column(TIME(precision=0))
    preferred_end_time: Mapped[time | None] = mapped_column(TIME(precision=0))
    preferred_timezone: Mapped[str | None] = mapped_column(sa.String(100))
    required_energy_level: Mapped[str | None] = mapped_column(sa.String(6))
    required_attention_level: Mapped[str | None] = mapped_column(sa.String(6))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"), server_onupdate=sa.FetchedValue())


class TaskOutcome(Base):
    __tablename__ = "task_outcomes"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_task_outcomes"),
        sa.UniqueConstraint("user_id", "client_key", name="uq_task_outcomes_client"),
        sa.CheckConstraint("outcome IN ('COMPLETED','SKIPPED','REOPENED')", name="ck_task_outcomes_kind"),
        sa.CheckConstraint("isfinite(occurred_at)", name="ck_task_outcomes_time"),
        sa.CheckConstraint("jsonb_typeof(task_snapshot)='object' AND octet_length(task_snapshot::text)<=65536", name="ck_task_outcomes_snapshot"),
        sa.Index("ix_task_outcomes_user_time", "user_id", "occurred_at", "id"),
        sa.Index("ix_task_outcomes_task_time", "task_id", "occurred_at", "id"),
        sa.Index("ix_task_outcomes_item", "schedule_item_id"),
        {"info": {"owner_module": "scheduler", "purpose": "Append-only task completion/skip/reopen feedback; current state is projected.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_task_outcomes_profile"))
    task_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.tasks.task_id", name="fk_task_outcomes_task", ondelete="SET NULL", onupdate="RESTRICT"))
    schedule_item_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_items.id", name="fk_task_outcomes_item", ondelete="SET NULL", onupdate="RESTRICT"))
    outcome: Mapped[str] = mapped_column(sa.String(9))
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    client_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    task_snapshot: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    note: Mapped[str | None] = mapped_column(sa.String(2000))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_schedules"),
        sa.UniqueConstraint("user_id", name="uq_schedules_user"),
        sa.Index("ix_schedules_current", "current_version_id"),
        {"info": {"owner_module": "scheduler", "purpose": "One canonical plan per user; daily/weekly/monthly views share its current version.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_schedules_profile"))
    current_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_versions.id", name="fk_schedules_current", ondelete="SET NULL", onupdate="RESTRICT", use_alter=True, deferrable=True, initially="DEFERRED"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"), server_onupdate=sa.FetchedValue())


class ScheduleVersion(Base):
    __tablename__ = "schedule_versions"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_schedule_versions"),
        sa.UniqueConstraint("schedule_id", "version_number", name="uq_schedule_versions_number"),
        sa.CheckConstraint("version_number > 0", name="ck_schedule_versions_number"),
        sa.CheckConstraint("isfinite(window_start_at) AND isfinite(window_end_at) AND window_end_at > window_start_at", name="ck_schedule_versions_window"),
        sa.CheckConstraint("origin IN ('GENERATED','MANUAL','RESCHEDULED')", name="ck_schedule_versions_origin"),
        sa.CheckConstraint("(status='DRAFT' AND published_at IS NULL) OR (status='PUBLISHED' AND published_at IS NOT NULL AND isfinite(published_at))", name="ck_schedule_versions_status"),
        sa.CheckConstraint("timezone=btrim(timezone) AND timezone ~ '[^[:space:]]'", name="ck_schedule_versions_timezone"),
        sa.CheckConstraint("jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536", name="ck_schedule_versions_snapshot"),
        sa.Index("ix_schedule_versions_user_created", "user_id", "created_at", "id"),
        sa.Index("ix_schedule_versions_parent", "parent_version_id"),
        sa.Index("ix_schedule_versions_decision", "decision_execution_id"),
        {"info": {"owner_module": "scheduler", "purpose": "Immutable published schedule versions with timezone and planning-window snapshots.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_schedule_versions_profile"))
    schedule_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedules.id", name="fk_schedule_versions_schedule", ondelete="CASCADE", onupdate="RESTRICT"))
    version_number: Mapped[int] = mapped_column(sa.Integer)
    parent_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_versions.id", name="fk_schedule_versions_parent", ondelete="NO ACTION", onupdate="RESTRICT", deferrable=True, initially="DEFERRED"))
    decision_execution_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.decision_executions.id", name="fk_schedule_versions_decision", ondelete="SET NULL", onupdate="RESTRICT", use_alter=True))
    window_start_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    window_end_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    timezone: Mapped[str] = mapped_column(sa.String(100))
    origin: Mapped[str] = mapped_column(sa.String(11), server_default=sa.text("'GENERATED'"))
    status: Mapped[str] = mapped_column(sa.String(9), server_default=sa.text("'DRAFT'"))
    input_snapshot: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    explanation: Mapped[str | None] = mapped_column(sa.String(4000))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class ScheduleItem(Base):
    __tablename__ = "schedule_items"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_schedule_items"),
        sa.UniqueConstraint("schedule_version_id", "logical_key", name="uq_schedule_items_key"),
        sa.CheckConstraint("isfinite(start_at) AND isfinite(end_at) AND end_at > start_at", name="ck_schedule_items_interval"),
        sa.CheckConstraint("(kind='TASK' AND event_id IS NULL AND event_occurrence_date IS NULL AND label IS NULL) OR (kind='EVENT' AND task_id IS NULL AND label IS NULL) OR (kind='BREAK' AND task_id IS NULL AND event_id IS NULL AND event_occurrence_date IS NULL AND label IS NOT NULL AND label ~ '[^[:space:]]')", name="ck_schedule_items_kind"),
        sa.CheckConstraint("event_occurrence_date IS NULL OR isfinite(event_occurrence_date)", name="ck_schedule_items_date"),
        sa.CheckConstraint("jsonb_typeof(source_snapshot)='object' AND octet_length(source_snapshot::text)<=65536", name="ck_schedule_items_snapshot"),
        ExcludeConstraint(("schedule_version_id", "="), (sa.func.tstzrange(sa.column("start_at"), sa.column("end_at"), "[)"), "&&"), name="ex_schedule_items_overlap", using="gist"),
        sa.Index("ix_schedule_items_version_start", "schedule_version_id", "start_at", "id"),
        sa.Index("ix_schedule_items_user_start", "user_id", "start_at", "id"),
        sa.Index("ix_schedule_items_task", "task_id"),
        sa.Index("ix_schedule_items_event", "event_id"),
        sa.Index("uq_schedule_items_once", "schedule_version_id", "event_id", unique=True, postgresql_where=sa.text("kind='EVENT' AND event_occurrence_date IS NULL")),
        sa.Index("uq_schedule_items_weekly", "schedule_version_id", "event_id", "event_occurrence_date", unique=True, postgresql_where=sa.text("kind='EVENT' AND event_occurrence_date IS NOT NULL")),
        {"info": {"owner_module": "scheduler", "purpose": "Concrete versioned task/event/break placements, never a second Task or Event.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_schedule_items_profile"))
    schedule_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_versions.id", name="fk_schedule_items_version", ondelete="CASCADE", onupdate="RESTRICT"))
    logical_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"))
    kind: Mapped[str] = mapped_column(sa.String(5))
    task_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.tasks.task_id", name="fk_schedule_items_task", ondelete="SET NULL", onupdate="RESTRICT"))
    event_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.events.event_id", name="fk_schedule_items_event", ondelete="SET NULL", onupdate="RESTRICT"))
    event_occurrence_date: Mapped[date | None] = mapped_column(sa.Date)
    label: Mapped[str | None] = mapped_column(sa.String(500))
    start_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    end_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    is_locked: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.false())
    source_snapshot: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))


class UnscheduledTask(Base):
    __tablename__ = "unscheduled_tasks"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_unscheduled_tasks"),
        sa.UniqueConstraint("schedule_version_id", "task_id", name="uq_unscheduled_tasks_task"),
        sa.CheckConstraint("reason_code=btrim(reason_code) AND reason_code ~ '[^[:space:]]'", name="ck_unscheduled_tasks_reason"),
        sa.CheckConstraint("jsonb_typeof(task_snapshot)='object' AND octet_length(task_snapshot::text)<=65536", name="ck_unscheduled_tasks_snapshot"),
        sa.Index("ix_unscheduled_tasks_user", "user_id", "created_at", "id"),
        sa.Index("ix_unscheduled_tasks_task", "task_id"),
        {"info": {"owner_module": "scheduler", "purpose": "Version-specific omissions/partial allocation reasons; remaining minutes are derived.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_unscheduled_tasks_profile"))
    schedule_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_versions.id", name="fk_unscheduled_tasks_version", ondelete="CASCADE", onupdate="RESTRICT"))
    task_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.tasks.task_id", name="fk_unscheduled_tasks_task", ondelete="SET NULL", onupdate="RESTRICT"))
    reason_code: Mapped[str] = mapped_column(sa.String(100))
    explanation: Mapped[str | None] = mapped_column(sa.String(2000))
    task_snapshot: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))


class ScheduleValidationIssue(Base):
    __tablename__ = "schedule_validation_issues"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_schedule_validation_issues"),
        sa.CheckConstraint("severity IN ('INFO','WARNING','ERROR')", name="ck_schedule_issues_severity"),
        sa.CheckConstraint("code ~ '[^[:space:]]' AND message ~ '[^[:space:]]'", name="ck_schedule_issues_message"),
        sa.CheckConstraint("jsonb_typeof(details)='object' AND octet_length(details::text)<=16384", name="ck_schedule_issues_details"),
        sa.Index("ix_schedule_issues_version", "schedule_version_id", "severity", "id"),
        sa.Index("ix_schedule_issues_user", "user_id"),
        sa.Index("ix_schedule_issues_item", "item_id"),
        sa.Index("ix_schedule_issues_related", "related_item_id"),
        {"info": {"owner_module": "scheduler", "purpose": "Validation/conflict findings with explicit item references and human-readable reasons.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_schedule_issues_profile"))
    schedule_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_versions.id", name="fk_schedule_issues_version", ondelete="CASCADE", onupdate="RESTRICT"))
    item_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_items.id", name="fk_schedule_issues_item", ondelete="SET NULL", onupdate="RESTRICT"))
    related_item_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("public.schedule_items.id", name="fk_schedule_issues_related", ondelete="SET NULL", onupdate="RESTRICT"))
    severity: Mapped[str] = mapped_column(sa.String(7))
    code: Mapped[str] = mapped_column(sa.String(100))
    message: Mapped[str] = mapped_column(sa.String(2000))
    details: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
