"""Recovery strategy, focus sessions, recovery periods, detection and summary service functions.

Scheduler integration limitation:
fixed_events and app_activity are passed as empty/None to the recovery detector for now.
Full scheduler integration comes with the workload service in the Energy Score phase.
"""

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.core.timezones import resolve_unambiguous_local_time, validate_timezone_name
from app.modules.energy.algorithms.recovery.recovery_detector import (
    RecoveryDetection,
    detect_actual_recovery,
)
from app.modules.energy.algorithms.recovery.recovery_planner import (
    StrategyParams,
    calculate_planned_recovery,
)
from app.modules.energy.algorithms.recovery.recovery_scorer import (
    RecoveryScore,
    calculate_recovery_score,
)
from app.modules.energy.algorithms.sleep.sleep_detector import calculate_duration_minutes
from app.modules.energy.models import FocusSession, RecoveryPeriod, RecoveryStrategy
from app.modules.energy.schemas import (
    FocusSessionFinishRequest,
    FocusSessionStartRequest,
    RecoveryDetectRequest,
    RecoveryStrategyCreateOrUpdate,
)


def _map_integrity_error(error: IntegrityError) -> None:
    orig = getattr(error, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)

    if constraint == "uq_focus_sessions_user_in_progress":
        raise APIError(409, "conflict", "A focus session is already in progress")
    if constraint == "uq_recovery_strategies_user_id":
        raise APIError(409, "conflict", "A recovery strategy already exists for this user")
    if sqlstate == "23505":
        raise APIError(409, "conflict", "Resource conflicts with existing data")
    raise error


def _precheck_in_progress(session: Session, user_id: UUID) -> None:
    stmt = sa.select(FocusSession.id).where(
        FocusSession.user_id == user_id,
        FocusSession.status == "IN_PROGRESS",
    )
    if session.scalar(stmt) is not None:
        raise APIError(409, "conflict", "A focus session is already in progress")


def get_local_day_bounds(target_date: date, zone_name: str) -> tuple[datetime, datetime]:
    """Calculate DST-safe UTC interval [local midnight(d), local midnight(d+1))."""
    try:
        valid_zone = validate_timezone_name(zone_name)
    except ValueError as exc:
        raise APIError(422, "invalid_request", str(exc)) from exc

    tz = ZoneInfo(valid_zone)

    def _get_midnight(d: date) -> datetime:
        naive_m = datetime.combine(d, time.min)
        try:
            return resolve_unambiguous_local_time(naive_m, valid_zone)
        except ValueError:
            # Local midnight falls in a DST gap (e.g. Egypt spring-forward where 00:00 does not exist);
            # fold=0 advances past the gap to the day's first valid local instant.
            return naive_m.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)

    start_utc = _get_midnight(target_date)
    end_utc = _get_midnight(target_date + timedelta(days=1))
    return start_utc, end_utc


def upsert_strategy(
    session: Session,
    user_id: UUID,
    payload: RecoveryStrategyCreateOrUpdate,
) -> RecoveryStrategy:
    stmt = (
        insert(RecoveryStrategy)
        .values(
            user_id=user_id,
            strategy_type=payload.strategy_type,
            work_duration_minutes=payload.work_duration_minutes,
            break_duration_minutes=payload.break_duration_minutes,
            long_break_duration_minutes=payload.long_break_duration_minutes,
            long_break_after_sessions=payload.long_break_after_sessions,
            is_active=True,
        )
        .on_conflict_do_update(
            constraint="uq_recovery_strategies_user_id",
            set_={
                "strategy_type": payload.strategy_type,
                "work_duration_minutes": payload.work_duration_minutes,
                "break_duration_minutes": payload.break_duration_minutes,
                "long_break_duration_minutes": payload.long_break_duration_minutes,
                "long_break_after_sessions": payload.long_break_after_sessions,
                "is_active": True,
            },
        )
        .returning(RecoveryStrategy.id)
    )
    strategy_id = session.scalar(stmt)
    session.flush()

    row = session.scalar(
        sa.select(RecoveryStrategy).where(
            RecoveryStrategy.id == strategy_id,
            RecoveryStrategy.user_id == user_id,
        )
    )
    session.refresh(row)
    return row


def get_strategy(session: Session, user_id: UUID) -> RecoveryStrategy:
    row = session.scalar(
        sa.select(RecoveryStrategy).where(RecoveryStrategy.user_id == user_id)
    )
    if row is None:
        raise APIError(404, "not_found", "Recovery strategy not found")
    return row


def start_focus_session(
    session: Session,
    user_id: UUID,
    payload: FocusSessionStartRequest | None = None,
    *,
    now: datetime | None = None,
) -> FocusSession:
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    if payload is not None and payload.started_at is not None:
        started_at = payload.started_at.astimezone(timezone.utc)
        if started_at > now + timedelta(minutes=5):
            raise APIError(422, "invalid_request", "started_at cannot be in the future beyond 5 minutes clock skew")
        if started_at < now - timedelta(hours=24):
            raise APIError(422, "invalid_request", "started_at cannot be older than 24 hours")
        start_time = started_at
    else:
        start_time = now

    _precheck_in_progress(session, user_id)

    row = FocusSession(
        user_id=user_id,
        start_time=start_time,
        end_time=None,
        duration_minutes=None,
        status="IN_PROGRESS",
    )
    session.add(row)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        _map_integrity_error(exc)

    session.refresh(row)
    return row


def finish_focus_session(
    session: Session,
    user_id: UUID,
    session_id: UUID,
    payload: FocusSessionFinishRequest,
    *,
    now: datetime | None = None,
) -> FocusSession:
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    existing = session.execute(
        sa.select(FocusSession.start_time, FocusSession.status).where(
            FocusSession.id == session_id,
            FocusSession.user_id == user_id,
        )
    ).first()
    if existing is None:
        raise APIError(404, "not_found", "Focus session not found")

    if existing.status != "IN_PROGRESS":
        raise APIError(409, "conflict", "Only in-progress sessions can be finished")

    if payload.ended_at is not None:
        end_time = payload.ended_at.astimezone(timezone.utc)
    else:
        end_time = now

    if end_time <= existing.start_time:
        raise APIError(422, "invalid_request", "ended_at must be strictly greater than start_time")
    if end_time > now + timedelta(minutes=5):
        raise APIError(422, "invalid_request", "ended_at cannot be in the future beyond 5 minutes clock skew")
    if (end_time - existing.start_time) > timedelta(hours=24):
        raise APIError(422, "invalid_request", "Focus session duration cannot exceed 24 hours")

    duration = calculate_duration_minutes(existing.start_time, end_time)
    if duration <= 0:
        duration = 1

    # Atomic compare-and-set: updates only if still IN_PROGRESS
    stmt = (
        sa.update(FocusSession)
        .where(
            FocusSession.id == session_id,
            FocusSession.user_id == user_id,
            FocusSession.status == "IN_PROGRESS",
        )
        .values(
            status=payload.status,
            end_time=end_time,
            duration_minutes=duration,
        )
        .returning(FocusSession.id)
    )
    updated_id = session.scalar(stmt)
    if updated_id is None:
        current_status = session.scalar(
            sa.select(FocusSession.status).where(
                FocusSession.id == session_id,
                FocusSession.user_id == user_id,
            )
        )
        if current_status is None:
            raise APIError(404, "not_found", "Focus session not found")
        raise APIError(409, "conflict", "Only in-progress sessions can be finished")

    session.flush()
    row = session.scalar(
        sa.select(FocusSession).where(
            FocusSession.id == session_id,
            FocusSession.user_id == user_id,
        )
    )
    session.refresh(row)
    return row


def list_focus_sessions(
    session: Session,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone_name: str = "UTC",
) -> dict:
    try:
        valid_zone = validate_timezone_name(timezone_name)
    except ValueError as exc:
        raise APIError(422, "invalid_request", str(exc)) from exc

    if date_from and date_to and date_to < date_from:
        raise APIError(422, "invalid_request", "date_to cannot precede date_from")

    query = sa.select(FocusSession).where(FocusSession.user_id == user_id)

    if status is not None:
        query = query.where(FocusSession.status == status)

    if date_from is not None:
        dt_from_utc, _ = get_local_day_bounds(date_from, valid_zone)
        query = query.where(FocusSession.start_time >= dt_from_utc)

    if date_to is not None:
        _, dt_to_utc = get_local_day_bounds(date_to, valid_zone)
        query = query.where(FocusSession.start_time < dt_to_utc)

    query = query.order_by(
        FocusSession.start_time.desc(),
        FocusSession.id.desc(),
    ).limit(limit + 1).offset(offset)

    results = session.scalars(query).all()
    items = list(results[:limit])
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "has_more": len(results) > limit,
    }


def list_recovery_periods(
    session: Session,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
    recovery_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone_name: str = "UTC",
) -> dict:
    try:
        valid_zone = validate_timezone_name(timezone_name)
    except ValueError as exc:
        raise APIError(422, "invalid_request", str(exc)) from exc

    if date_from and date_to and date_to < date_from:
        raise APIError(422, "invalid_request", "date_to cannot precede date_from")

    query = sa.select(RecoveryPeriod).where(RecoveryPeriod.user_id == user_id)

    if source is not None:
        query = query.where(RecoveryPeriod.source == source)

    if recovery_type is not None:
        query = query.where(RecoveryPeriod.recovery_type == recovery_type)

    if date_from is not None:
        dt_from_utc, _ = get_local_day_bounds(date_from, valid_zone)
        query = query.where(RecoveryPeriod.start_time >= dt_from_utc)

    if date_to is not None:
        _, dt_to_utc = get_local_day_bounds(date_to, valid_zone)
        query = query.where(RecoveryPeriod.start_time < dt_to_utc)

    query = query.order_by(
        RecoveryPeriod.start_time.desc(),
        RecoveryPeriod.id.desc(),
    ).limit(limit + 1).offset(offset)

    results = session.scalars(query).all()
    items = list(results[:limit])
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "has_more": len(results) > limit,
    }


def compute_recovery_for_day(
    session: Session,
    user_id: UUID,
    target_date: date,
    timezone_name: str,
    workload_level: str | None = None,
    strategy_row: RecoveryStrategy | None = None,
) -> tuple[RecoveryDetection, RecoveryScore, bool] | None:
    """Compute recovery detection and score for a user on a given local date without writing to DB.

    Returns (detection, score, truncated) or None if no active recovery strategy is configured.
    """
    if strategy_row is None:
        strategy_row = session.scalar(
            sa.select(RecoveryStrategy).where(
                RecoveryStrategy.user_id == user_id,
                RecoveryStrategy.is_active == True,
            )
        )
        if strategy_row is None:
            return None

    # Local day bounds in UTC
    day_start_utc, day_end_utc = get_local_day_bounds(target_date, timezone_name)

    # Load COMPLETED focus sessions starting on that local day
    stmt = (
        sa.select(FocusSession)
        .where(
            FocusSession.user_id == user_id,
            FocusSession.status == "COMPLETED",
            FocusSession.start_time >= day_start_utc,
            FocusSession.start_time < day_end_utc,
        )
        .order_by(FocusSession.start_time.asc(), FocusSession.id.asc())
        .limit(25)
    )
    all_sessions = session.scalars(stmt).all()
    truncated = len(all_sessions) > 24
    sessions = all_sessions[:24]

    # If < 2 sessions: UNKNOWN status
    if len(sessions) < 2:
        dummy_detection = RecoveryDetection(
            breaks=(),
            observed_recovery_minutes=0.0,
            capped_recovery_minutes=0.0,
            planned_recovery_minutes=0.0,
            missed_breaks=0,
            shortened_breaks=0,
            extended_breaks=0,
            taken_breaks=0,
            completion_ratio=None,
            data_status="UNKNOWN",
            consecutive_focus_sessions_without_long_break=len(sessions),
            long_break_after_sessions=strategy_row.long_break_after_sessions,
        )
        score_obj = calculate_recovery_score(
            detection=dummy_detection,
            workload_level=workload_level,
        )
        return dummy_detection, score_obj, truncated

    # Build planned schedule and detect actual recovery
    strategy_params = StrategyParams(
        work_minutes=strategy_row.work_duration_minutes,
        short_break_minutes=strategy_row.break_duration_minutes,
        long_break_minutes=strategy_row.long_break_duration_minutes,
        long_break_after_sessions=strategy_row.long_break_after_sessions,
    )
    planned = calculate_planned_recovery(
        strategy=strategy_params,
        start=sessions[0].start_time,
        sessions_count=len(sessions),
    )

    focus_tuples = [(s.start_time, s.end_time, s.status) for s in sessions]
    detection = detect_actual_recovery(
        planned=planned,
        focus_sessions=focus_tuples,
        fixed_events=(),
        app_activity=None,
    )
    score = calculate_recovery_score(
        detection=detection,
        workload_level=workload_level,
    )
    return detection, score, truncated


def detect_recovery(
    session: Session,
    user_id: UUID,
    payload: RecoveryDetectRequest,
) -> dict:
    # 1. Lock the user's recovery_strategies row to serialize concurrent detects per user
    strategy_row = session.scalar(
        sa.select(RecoveryStrategy)
        .where(
            RecoveryStrategy.user_id == user_id,
            RecoveryStrategy.is_active == True,
        )
        .with_for_update()
    )
    if strategy_row is None:
        raise APIError(409, "recovery_strategy_not_configured", "Recovery strategy is not configured")

    result = compute_recovery_for_day(
        session,
        user_id,
        payload.date,
        payload.timezone,
        workload_level=payload.workload_level,
        strategy_row=strategy_row,
    )
    assert result is not None
    detection, score, truncated = result

    # 4. If < 2 sessions: UNKNOWN status, delete nothing, insert nothing
    if len(detection.breaks) == 0 and detection.data_status == "UNKNOWN":
        return {
            "data_status": "UNKNOWN",
            "breaks": [],
            "observed_recovery_minutes": 0.0,
            "planned_recovery_minutes": 0.0,
            "missed_breaks": 0,
            "shortened_breaks": 0,
            "extended_breaks": 0,
            "taken_breaks": 0,
            "completion_ratio": None,
            "score": {
                "score": score.score,
                "level": score.level,
                "deficit_minutes": score.deficit_minutes,
                "status": score.status,
            },
            "truncated": truncated,
        }

    # 6. Persist idempotently inside a savepoint:
    # Delete existing DETECTED periods on that local day for this user, then insert new DETECTED periods
    day_start_utc, day_end_utc = get_local_day_bounds(payload.date, payload.timezone)
    with session.begin_nested():
        session.execute(
            sa.delete(RecoveryPeriod).where(
                RecoveryPeriod.user_id == user_id,
                RecoveryPeriod.source == "DETECTED",
                RecoveryPeriod.start_time >= day_start_utc,
                RecoveryPeriod.start_time < day_end_utc,
            )
        )
        for b in detection.breaks:
            if b.classification != "MISSED" and b.credited_recovery_minutes > 0:
                dur = calculate_duration_minutes(b.start, b.end)
                if dur > 0:
                    session.add(
                        RecoveryPeriod(
                            user_id=user_id,
                            start_time=b.start,
                            end_time=b.end,
                            duration_minutes=dur,
                            source="DETECTED",
                            recovery_type=b.planned_break_kind,
                        )
                    )
        session.flush()

    breaks_read = [
        {
            "index": b.index,
            "block_break_index": b.block_break_index,
            "start": b.start,
            "end": b.end,
            "raw_gap_minutes": b.raw_gap_minutes,
            "credited_recovery_minutes": b.credited_recovery_minutes,
            "capped_recovery_minutes": b.capped_recovery_minutes,
            "planned_duration_minutes": b.planned_duration_minutes,
            "classification": b.classification,
            "planned_break_kind": b.planned_break_kind,
        }
        for b in detection.breaks
    ]

    return {
        "data_status": detection.data_status,
        "breaks": breaks_read,
        "observed_recovery_minutes": detection.observed_recovery_minutes,
        "planned_recovery_minutes": detection.planned_recovery_minutes,
        "missed_breaks": detection.missed_breaks,
        "shortened_breaks": detection.shortened_breaks,
        "extended_breaks": detection.extended_breaks,
        "taken_breaks": detection.taken_breaks,
        "completion_ratio": detection.completion_ratio,
        "score": {
            "score": score.score,
            "level": score.level,
            "deficit_minutes": score.deficit_minutes,
            "status": score.status,
        },
        "truncated": truncated,
    }


def get_recovery_summary(
    session: Session,
    user_id: UUID,
    target_date: date,
    timezone_name: str = "UTC",
) -> dict:
    day_start_utc, day_end_utc = get_local_day_bounds(target_date, timezone_name)

    # 1. Aggregation on focus sessions
    focus_agg = session.execute(
        sa.select(
            sa.func.count().filter(FocusSession.status == "COMPLETED"),
            sa.func.coalesce(sa.func.sum(FocusSession.duration_minutes).filter(FocusSession.status == "COMPLETED"), 0),
            sa.func.coalesce(sa.func.bool_or(FocusSession.status == "IN_PROGRESS"), False),
        )
        .where(
            FocusSession.user_id == user_id,
            FocusSession.start_time >= day_start_utc,
            FocusSession.start_time < day_end_utc,
        )
    ).one()

    completed_count = int(focus_agg[0])
    total_focus_min = int(focus_agg[1])
    in_progress = bool(focus_agg[2])

    # 2. Aggregation on recovery periods
    periods_agg = session.execute(
        sa.select(
            RecoveryPeriod.source,
            sa.func.coalesce(sa.func.sum(RecoveryPeriod.duration_minutes), 0),
            sa.func.count(RecoveryPeriod.id),
        )
        .where(
            RecoveryPeriod.user_id == user_id,
            RecoveryPeriod.start_time >= day_start_utc,
            RecoveryPeriod.start_time < day_end_utc,
        )
        .group_by(RecoveryPeriod.source)
    ).all()

    recovery_by_source = {
        "FOCUS_TIMER": 0,
        "DETECTED": 0,
        "MANUAL": 0,
        "PLANNED": 0,
    }
    total_rec_min = 0
    total_periods = 0

    for source_val, dur_sum, cnt in periods_agg:
        recovery_by_source[source_val] = int(dur_sum)
        total_rec_min += int(dur_sum)
        total_periods += int(cnt)

    return {
        "date": target_date,
        "timezone": timezone_name,
        "focus_sessions_completed": completed_count,
        "total_focus_minutes": total_focus_min,
        "in_progress": in_progress,
        "recovery_minutes_by_source": recovery_by_source,
        "total_recovery_minutes": total_rec_min,
        "periods_count": total_periods,
    }
