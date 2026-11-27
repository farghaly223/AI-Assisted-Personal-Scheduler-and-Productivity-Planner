"""Opportunistic retention housekeeping for energy signals and score revisions."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.energy.models import (
    EnergyScoreRevision,
    SleepSession,
    SleepUsageSignal,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionConfig:
    """Provisional retention policy thresholds and batch limits.

    PROVISIONAL: These intervals and batch limits are initial engineering
    defaults and have NOT been finalized by product or privacy review.
    """

    signal_max_age: timedelta = timedelta(days=14)
    linked_signal_max_age: timedelta = timedelta(hours=48)
    signal_purge_batch_limit: int = 20_000
    revision_max_age: timedelta = timedelta(days=90)
    revision_purge_batch_limit: int = 20_000

    def __post_init__(self) -> None:
        if self.signal_max_age <= timedelta(0):
            raise ValueError("signal_max_age must be positive")
        if self.linked_signal_max_age <= timedelta(0):
            raise ValueError("linked_signal_max_age must be positive")
        if self.revision_max_age <= timedelta(0):
            raise ValueError("revision_max_age must be positive")
        if self.signal_purge_batch_limit <= 0:
            raise ValueError("signal_purge_batch_limit must be positive")
        if self.revision_purge_batch_limit <= 0:
            raise ValueError("revision_purge_batch_limit must be positive")


DEFAULT_RETENTION_CONFIG = RetentionConfig()


def normalize_utc(now: datetime | None = None) -> datetime:
    """Normalize an optional datetime to timezone-aware UTC."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def purge_sleep_usage_signals(
    session: Session,
    user_id: UUID,
    *,
    now: datetime | None = None,
    config: RetentionConfig = DEFAULT_RETENTION_CONFIG,
) -> int:
    """Delete up to batch_limit expired sleep_usage_signals for user_id in one DELETE.

    Deletes signals belonging to user_id that are:
    (a) older than config.signal_max_age by event_timestamp, or
    (b) linked to a sleep_session owned by user_id with status in
        ('confirmed', 'rejected') and updated_at older than config.linked_signal_max_age.
    """
    now_utc = normalize_utc(now)
    signal_cutoff = now_utc - config.signal_max_age
    session_cutoff = now_utc - config.linked_signal_max_age

    candidate_ids = (
        sa.select(SleepUsageSignal.id)
        .outerjoin(
            SleepSession,
            sa.and_(
                SleepSession.user_id == SleepUsageSignal.user_id,
                SleepSession.id == SleepUsageSignal.session_id,
            ),
        )
        .where(
            SleepUsageSignal.user_id == user_id,
            sa.or_(
                SleepUsageSignal.event_timestamp < signal_cutoff,
                sa.and_(
                    SleepSession.user_id == user_id,
                    SleepSession.status.in_(("confirmed", "rejected")),
                    SleepSession.updated_at < session_cutoff,
                ),
            ),
        )
        .order_by(SleepUsageSignal.event_timestamp.asc(), SleepUsageSignal.id.asc())
        .limit(config.signal_purge_batch_limit)
    )
    stmt = sa.delete(SleepUsageSignal).where(
        SleepUsageSignal.user_id == user_id,
        SleepUsageSignal.id.in_(candidate_ids),
    )
    result = session.execute(stmt)
    session.flush()
    return int(result.rowcount or 0)


def purge_energy_score_revisions(
    session: Session,
    user_id: UUID,
    *,
    now: datetime | None = None,
    config: RetentionConfig = DEFAULT_RETENTION_CONFIG,
) -> int:
    """Delete up to batch_limit expired energy_score_revisions for user_id in one DELETE."""
    now_utc = normalize_utc(now)
    revision_cutoff = now_utc - config.revision_max_age

    candidate_ids = (
        sa.select(EnergyScoreRevision.id)
        .where(
            EnergyScoreRevision.user_id == user_id,
            EnergyScoreRevision.calculated_at < revision_cutoff,
        )
        .order_by(EnergyScoreRevision.calculated_at.asc(), EnergyScoreRevision.id.asc())
        .limit(config.revision_purge_batch_limit)
    )
    stmt = sa.delete(EnergyScoreRevision).where(
        EnergyScoreRevision.user_id == user_id,
        EnergyScoreRevision.id.in_(candidate_ids),
    )
    result = session.execute(stmt)
    session.flush()
    return int(result.rowcount or 0)


def run_purge_safely(
    session: Session,
    purge_fn: Callable[..., int],
    *args: Any,
    **kwargs: Any,
) -> int:
    """Execute housekeeping inside a savepoint so failures never abort the main request."""
    try:
        with session.begin_nested():
            return purge_fn(session, *args, **kwargs)
    except Exception as exc:
        logger.warning("Energy retention purge failed error_type=%s", type(exc).__name__)
        return 0
