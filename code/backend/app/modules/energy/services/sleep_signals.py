"""Screen signals sync and sleep candidate estimation service functions."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.modules.energy.algorithms.sleep.config import (
    DEFAULT_SLEEP_CONFIG,
    SleepDetectionConfig,
    UserSleepSchedule,
)
from app.modules.energy.algorithms.sleep.sleep_detector import (
    detect_sleep_candidate,
    resolve_local_time,
)
from app.modules.energy.models import (
    SleepQualityRating,
    SleepSchedule,
    SleepSession,
    SleepUsageSignal,
)
from app.modules.energy.schemas import SignalSyncRequest, SleepEstimateRequest
from app.modules.energy.services import retention as retention_service
from app.modules.energy.services.sleep_sessions import _map_integrity_error, _to_session_dict


def sync_screen_signals(
    session: Session,
    user_id: UUID,
    payload: SignalSyncRequest,
    *,
    now: datetime | None = None,
    retention_config: retention_service.RetentionConfig = retention_service.DEFAULT_RETENTION_CONFIG,
) -> dict:
    """Ingest screen usage signals idempotently.

    Validates timestamps against clock skew and signal retention age using injectable `now`.
    Runs opportunistic retention purge inside a savepoint before inserting.
    Deduplicates payload events after converting to UTC.
    Performs a single bulk insert with ON CONFLICT DO NOTHING.
    """
    now = retention_service.normalize_utc(now)

    future_limit = now + timedelta(minutes=5)
    past_limit = now - retention_config.signal_max_age
    max_age_days = retention_config.signal_max_age.days

    # 1. Validate timestamps: reject in future (>5m clock skew) or older than retention window
    for ev in payload.screen_events:
        ts = ev.timestamp
        if ts > future_limit:
            raise APIError(422, "invalid_request", "Timestamp cannot be in the future beyond 5 minutes clock skew")
        if ts < past_limit:
            raise APIError(422, "invalid_request", f"Timestamp cannot be older than {max_age_days} days")

    # 2. Deduplicate payload events AFTER converting timestamps to UTC
    unique_events: dict[tuple[str, datetime], None] = {}
    for ev in payload.screen_events:
        ts_utc = ev.timestamp.astimezone(timezone.utc)
        unique_events[(ev.event_type, ts_utc)] = None

    # 3. Opportunistic retention purge (isolated in savepoint; never breaks sync)
    retention_service.run_purge_safely(
        session,
        retention_service.purge_sleep_usage_signals,
        user_id,
        now=now,
        config=retention_config,
    )

    received = len(payload.screen_events)
    if not unique_events:
        return {"received": received, "inserted": 0, "duplicates": received}

    # 4. Single bulk insert with ON CONFLICT DO NOTHING
    stmt = (
        insert(SleepUsageSignal)
        .values([
            {
                "user_id": user_id,
                "event_type": etype,
                "event_timestamp": ts_utc,
                "session_id": None,
            }
            for (etype, ts_utc) in unique_events.keys()
        ])
        .on_conflict_do_nothing(constraint="uq_sleep_usage_signals_user_event")
        .returning(SleepUsageSignal.id)
    )
    inserted_ids = session.scalars(stmt).all()
    session.flush()

    inserted = len(inserted_ids)
    return {
        "received": received,
        "inserted": inserted,
        "duplicates": received - inserted,
    }


def _check_existing_session(session: Session, user_id: UUID, target_date: date):
    return session.execute(
        sa.select(SleepSession, SleepQualityRating.rating)
        .outerjoin(
            SleepQualityRating,
            sa.and_(
                SleepQualityRating.session_id == SleepSession.id,
                SleepQualityRating.user_id == SleepSession.user_id,
            ),
        )
        .where(
            SleepSession.user_id == user_id,
            SleepSession.sleep_date == target_date,
            SleepSession.status != "rejected",
        )
    ).first()


def estimate_sleep_session(
    session: Session,
    user_id: UUID,
    payload: SleepEstimateRequest,
    *,
    now: datetime | None = None,
    config: SleepDetectionConfig = DEFAULT_SLEEP_CONFIG,
    retention_config: retention_service.RetentionConfig = retention_service.DEFAULT_RETENTION_CONFIG,
) -> tuple[int, dict]:
    """Estimate sleep session candidate from device usage signals for target_date.

    Returns:
        (status_code, data_dict)
    """
    now = retention_service.normalize_utc(now)

    tz = ZoneInfo(payload.timezone)
    local_today = now.astimezone(tz).date()

    # 1. Validate estimate target_date: reject (422) if > local today + 1 day or older than signal retention window
    max_age_days = retention_config.signal_max_age.days
    if payload.target_date > local_today + timedelta(days=1):
        raise APIError(422, "invalid_request", "target_date cannot be more than 1 day in the future")
    if payload.target_date < local_today - retention_config.signal_max_age:
        raise APIError(422, "invalid_request", f"target_date cannot be older than {max_age_days} days")

    # 2. Opportunistic retention purge (isolated in savepoint; never breaks estimation)
    retention_service.run_purge_safely(
        session,
        retention_service.purge_sleep_usage_signals,
        user_id,
        now=now,
        config=retention_config,
    )

    # 3. Check if a non-rejected session already exists for that sleep_date
    existing_session = _check_existing_session(session, user_id, payload.target_date)

    if existing_session is not None:
        sess_dict = _to_session_dict(existing_session[0], quality_rating=existing_session[1])
        return 200, {
            "status": "existing",
            "created": False,
            "session": sess_dict,
        }

    # 3. Compute search window in UTC from target_date in local timezone
    # Window: previous day local 18:00 to target_date local 14:00
    window_start_naive = datetime.combine(
        payload.target_date - timedelta(days=1), config.search_window_start_time
    )
    window_end_naive = datetime.combine(payload.target_date, config.search_window_end_time)
    window_start_utc = resolve_local_time(window_start_naive, tz)
    window_end_utc = resolve_local_time(window_end_naive, tz)

    # Small margin around search window for fetching signals (2 hours)
    margin = timedelta(hours=2)
    query_start = window_start_utc - margin
    query_end = window_end_utc + margin

    # 4. Load signals for search window
    signals = session.execute(
        sa.select(SleepUsageSignal.event_type, SleepUsageSignal.event_timestamp)
        .where(
            SleepUsageSignal.user_id == user_id,
            SleepUsageSignal.event_timestamp >= query_start,
            SleepUsageSignal.event_timestamp <= query_end,
        )
        .order_by(SleepUsageSignal.event_timestamp.asc())
    ).all()

    events = [(row[0], row[1]) for row in signals]

    # 5. Load active sleep schedule
    schedule_row = session.scalar(
        sa.select(SleepSchedule).where(
            SleepSchedule.user_id == user_id,
            SleepSchedule.is_active == True,
        )
    )
    schedule = None
    if schedule_row is not None:
        schedule = UserSleepSchedule(
            bedtime=schedule_row.bedtime,
            wake_time=schedule_row.wake_time,
            repeat_days=tuple(schedule_row.repeat_days),
            is_active=schedule_row.is_active,
        )

    # 6. Load last 14 days of confirmed sleep sessions as history
    history_from = payload.target_date - timedelta(days=14)
    history_rows = session.execute(
        sa.select(SleepSession.start_time, SleepSession.end_time)
        .where(
            SleepSession.user_id == user_id,
            SleepSession.status == "confirmed",
            SleepSession.sleep_date >= history_from,
            SleepSession.sleep_date < payload.target_date,
        )
        .order_by(SleepSession.start_time.asc())
    ).all()
    history = [(row[0], row[1]) for row in history_rows]

    # 7. Call detect_sleep_candidate
    candidate = detect_sleep_candidate(
        events=events,
        timezone_name_or_zone=payload.timezone,
        target_date=payload.target_date,
        schedule=schedule,
        history=history,
        config=config,
    )

    if candidate is None:
        return 200, {
            "status": "insufficient_data",
            "created": False,
            "session": None,
        }

    # 8. Create sleep_session (source='android_usage', status='pending_confirmation')
    new_session = SleepSession(
        user_id=user_id,
        start_time=candidate.estimated_start,
        end_time=candidate.estimated_end,
        duration_minutes=candidate.duration_minutes,
        timezone=payload.timezone,
        sleep_date=payload.target_date,
        source="android_usage",
        confidence=candidate.confidence_score,
        status="pending_confirmation",
    )

    try:
        with session.begin_nested():
            session.add(new_session)
            session.flush()
    except IntegrityError as exc:
        orig = getattr(exc, "orig", None)
        diag = getattr(orig, "diag", None)
        constraint = getattr(diag, "constraint_name", None)
        if constraint in ("uq_sleep_sessions_user_sleep_date", "uq_sleep_sessions_user_interval"):
            # Concurrent estimate created session; return existing
            colliding = _check_existing_session(session, user_id, payload.target_date)
            if colliding is not None:
                return 200, {
                    "status": "existing",
                    "created": False,
                    "session": _to_session_dict(colliding[0], quality_rating=colliding[1]),
                }
        _map_integrity_error(exc)




    session.refresh(new_session)

    # 9. Link signals: window predicate UPDATE
    # Link rows where session_id IS NULL OR currently linked session is 'rejected'
    rejected_subquery = sa.select(SleepSession.id).where(
        SleepSession.user_id == user_id,
        SleepSession.status == "rejected",
    )
    session.execute(
        sa.update(SleepUsageSignal)
        .where(
            SleepUsageSignal.user_id == user_id,
            SleepUsageSignal.event_timestamp >= query_start,
            SleepUsageSignal.event_timestamp <= query_end,
            sa.or_(
                SleepUsageSignal.session_id.is_(None),
                SleepUsageSignal.session_id.in_(rejected_subquery),
            ),
        )
        .values(session_id=new_session.id)
    )
    session.flush()

    sess_dict = _to_session_dict(new_session, quality_rating=None)
    return 201, {
        "status": "estimated",
        "created": True,
        "session": sess_dict,
    }

