"""Sleep session and sleep quality service functions with owner filtering and concurrency safety."""

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.modules.energy.algorithms.sleep.sleep_detector import calculate_duration_minutes
from app.modules.energy.algorithms.sleep.sleep_quality import normalize_sleep_quality
from app.modules.energy.models import SleepQualityRating, SleepSession
from app.modules.energy.schemas import (
    SleepQualityCreate,
    SleepQualityUpdate,
    SleepSessionConfirm,
    SleepSessionCreate,
)


def _map_integrity_error(error: IntegrityError) -> None:
    orig = getattr(error, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)

    if constraint == "uq_sleep_sessions_user_interval":
        raise APIError(409, "conflict", "A sleep session already exists with this exact start and end time")
    if constraint == "uq_sleep_sessions_user_sleep_date":
        raise APIError(409, "conflict", "A sleep session already exists for this sleep date")
    if constraint == "uq_sleep_quality_ratings_session_id":
        raise APIError(409, "conflict", "A quality rating already exists for this session")
    if sqlstate == "23505":
        raise APIError(409, "conflict", "Resource conflicts with existing data")
    raise error


def _precheck_session_conflicts(
    session: Session,
    user_id: UUID,
    start_time: datetime,
    end_time: datetime,
    sleep_date: date,
    exclude_id: UUID | None = None,
) -> None:
    # 1. Interval conflict check
    stmt_interval = sa.select(SleepSession.id).where(
        SleepSession.user_id == user_id,
        SleepSession.start_time == start_time,
        SleepSession.end_time == end_time,
    )
    if exclude_id is not None:
        stmt_interval = stmt_interval.where(SleepSession.id != exclude_id)
    if session.scalar(stmt_interval) is not None:
        raise APIError(409, "conflict", "A sleep session already exists with this exact start and end time")

    # 2. Non-rejected session on same sleep_date check
    stmt_date = sa.select(SleepSession.id).where(
        SleepSession.user_id == user_id,
        SleepSession.sleep_date == sleep_date,
        SleepSession.status != "rejected",
    )
    if exclude_id is not None:
        stmt_date = stmt_date.where(SleepSession.id != exclude_id)
    if session.scalar(stmt_date) is not None:
        raise APIError(409, "conflict", "A sleep session already exists for this sleep date")


def _precheck_rating_exists(session: Session, session_id: UUID) -> None:
    stmt = sa.select(SleepQualityRating.id).where(SleepQualityRating.session_id == session_id)
    if session.scalar(stmt) is not None:
        raise APIError(409, "conflict", "A quality rating already exists for this session")


def _to_session_dict(row: SleepSession, quality_rating: int | None = None) -> dict:
    return {
        "id": row.id,
        "start_time": row.start_time,
        "end_time": row.end_time,
        "duration_minutes": row.duration_minutes,
        "timezone": row.timezone,
        "sleep_date": row.sleep_date,
        "source": row.source,
        "confidence": row.confidence,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "quality_rating": quality_rating,
        "quality_score": normalize_sleep_quality(quality_rating) if quality_rating is not None else None,
    }


def create_manual_session(session: Session, user_id: UUID, payload: SleepSessionCreate) -> dict:
    start_utc = payload.start_time
    end_utc = payload.end_time
    tz_name = payload.timezone
    tz = ZoneInfo(tz_name)
    sleep_date = end_utc.astimezone(tz).date()
    duration = calculate_duration_minutes(start_utc, end_utc)

    _precheck_session_conflicts(session, user_id, start_utc, end_utc, sleep_date)

    row = SleepSession(
        user_id=user_id,
        start_time=start_utc,
        end_time=end_utc,
        timezone=tz_name,
        sleep_date=sleep_date,
        duration_minutes=duration,
        source="manual",
        confidence=1.0,
        status="confirmed",
    )
    session.add(row)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        _map_integrity_error(exc)

    session.refresh(row)
    return _to_session_dict(row, quality_rating=None)


def list_sessions(
    session: Session,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    if date_from and date_to and date_to < date_from:
        raise APIError(422, "invalid_request", "date_to cannot precede date_from")

    query = (
        sa.select(SleepSession, SleepQualityRating.rating)
        .outerjoin(
            SleepQualityRating,
            sa.and_(
                SleepQualityRating.session_id == SleepSession.id,
                SleepQualityRating.user_id == SleepSession.user_id,
            ),
        )
        .where(SleepSession.user_id == user_id)
    )

    if status is not None:
        query = query.where(SleepSession.status == status)
    if date_from is not None:
        query = query.where(SleepSession.sleep_date >= date_from)
    if date_to is not None:
        query = query.where(SleepSession.sleep_date <= date_to)

    query = query.order_by(
        SleepSession.sleep_date.desc(),
        SleepSession.start_time.desc(),
        SleepSession.id.desc(),
    ).limit(limit + 1).offset(offset)

    results = session.execute(query).all()
    items = [_to_session_dict(row[0], quality_rating=row[1]) for row in results[:limit]]
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "has_more": len(results) > limit,
    }


def list_pending_confirmation(session: Session, user_id: UUID) -> list[dict]:
    query = (
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
            SleepSession.status == "pending_confirmation",
        )
        .order_by(
            SleepSession.sleep_date.desc(),
            SleepSession.start_time.desc(),
            SleepSession.id.desc(),
        )
    )
    results = session.execute(query).all()
    return [_to_session_dict(row[0], quality_rating=row[1]) for row in results]


def get_session(session: Session, user_id: UUID, session_id: UUID) -> dict:
    query = (
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
            SleepSession.id == session_id,
        )
    )
    result = session.execute(query).first()
    if result is None:
        raise APIError(404, "not_found", "Sleep session was not found")
    return _to_session_dict(result[0], quality_rating=result[1])


def confirm_or_update_session(
    session: Session,
    user_id: UUID,
    session_id: UUID,
    payload: SleepSessionConfirm,
) -> dict:
    row = session.scalar(
        sa.select(SleepSession).where(
            SleepSession.user_id == user_id,
            SleepSession.id == session_id,
        )
    )
    if row is None:
        raise APIError(404, "not_found", "Sleep session was not found")

    current_status = row.status
    target_status = payload.status

    if current_status == "rejected":
        raise APIError(409, "conflict", "Rejected sessions cannot be modified or confirmed")

    if current_status == "confirmed" and target_status == "rejected":
        raise APIError(409, "conflict", "Confirmed sessions cannot be rejected")

    if target_status == "rejected":
        row.status = "rejected"
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError as exc:
            _map_integrity_error(exc)
        session.refresh(row)
        return _to_session_dict(row, quality_rating=None)

    # target_status is "confirmed"
    merged_start = payload.start_time if payload.start_time is not None else row.start_time
    merged_end = payload.end_time if payload.end_time is not None else row.end_time
    merged_tz = payload.timezone if payload.timezone is not None else row.timezone

    if merged_end <= merged_start:
        raise APIError(422, "invalid_request", "End time must be after start time")

    duration = calculate_duration_minutes(merged_start, merged_end)
    if duration < 30 or duration > 1440:
        raise APIError(422, "invalid_request", "Duration must be between 30 minutes and 24 hours")

    tz = ZoneInfo(merged_tz)
    sleep_date = merged_end.astimezone(tz).date()

    _precheck_session_conflicts(session, user_id, merged_start, merged_end, sleep_date, exclude_id=row.id)

    row.status = "confirmed"
    row.start_time = merged_start
    row.end_time = merged_end
    row.timezone = merged_tz
    row.duration_minutes = duration
    row.sleep_date = sleep_date

    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        _map_integrity_error(exc)

    session.refresh(row)
    rating = session.scalar(
        sa.select(SleepQualityRating.rating).where(
            SleepQualityRating.session_id == row.id,
            SleepQualityRating.user_id == user_id,
        )
    )
    return _to_session_dict(row, quality_rating=rating)


def delete_session(session: Session, user_id: UUID, session_id: UUID) -> None:
    row = session.scalar(
        sa.select(SleepSession).where(
            SleepSession.user_id == user_id,
            SleepSession.id == session_id,
        )
    )
    if row is None:
        raise APIError(404, "not_found", "Sleep session was not found")
    session.delete(row)
    session.flush()


def create_quality_rating(
    session: Session,
    user_id: UUID,
    session_id: UUID,
    payload: SleepQualityCreate,
) -> dict:
    sleep_sess = session.scalar(
        sa.select(SleepSession).where(
            SleepSession.user_id == user_id,
            SleepSession.id == session_id,
        )
    )
    if sleep_sess is None:
        raise APIError(404, "not_found", "Sleep session was not found")

    if sleep_sess.status != "confirmed":
        raise APIError(409, "conflict", "Quality ratings can only be added to confirmed sleep sessions")

    _precheck_rating_exists(session, session_id)

    rating_row = SleepQualityRating(
        user_id=user_id,
        session_id=session_id,
        rating=payload.rating,
        notes=None,
    )
    session.add(rating_row)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        _map_integrity_error(exc)

    session.refresh(rating_row)
    score = normalize_sleep_quality(rating_row.rating)
    return {
        "session_id": rating_row.session_id,
        "rating": rating_row.rating,
        "quality_score": score,
        "created_at": rating_row.created_at,
        "updated_at": rating_row.updated_at,
    }


def update_quality_rating(
    session: Session,
    user_id: UUID,
    session_id: UUID,
    payload: SleepQualityUpdate,
) -> dict:
    sleep_sess = session.scalar(
        sa.select(SleepSession).where(
            SleepSession.user_id == user_id,
            SleepSession.id == session_id,
        )
    )
    if sleep_sess is None:
        raise APIError(404, "not_found", "Sleep session was not found")

    rating_row = session.scalar(
        sa.select(SleepQualityRating).where(
            SleepQualityRating.user_id == user_id,
            SleepQualityRating.session_id == session_id,
        )
    )
    if rating_row is None:
        raise APIError(404, "not_found", "Quality rating was not found")

    rating_row.rating = payload.rating
    session.flush()
    session.refresh(rating_row)
    score = normalize_sleep_quality(rating_row.rating)
    return {
        "session_id": rating_row.session_id,
        "rating": rating_row.rating,
        "quality_score": score,
        "created_at": rating_row.created_at,
        "updated_at": rating_row.updated_at,
    }


def list_quality_history(
    session: Session,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    if date_from and date_to and date_to < date_from:
        raise APIError(422, "invalid_request", "date_to cannot precede date_from")

    query = (
        sa.select(SleepQualityRating, SleepSession.sleep_date)
        .join(
            SleepSession,
            sa.and_(
                SleepSession.id == SleepQualityRating.session_id,
                SleepSession.user_id == SleepQualityRating.user_id,
            ),
        )
        .where(SleepQualityRating.user_id == user_id)
    )

    if date_from is not None:
        query = query.where(SleepSession.sleep_date >= date_from)
    if date_to is not None:
        query = query.where(SleepSession.sleep_date <= date_to)

    query = query.order_by(
        SleepSession.sleep_date.desc(),
        SleepQualityRating.created_at.desc(),
        SleepQualityRating.id.desc(),
    ).limit(limit + 1).offset(offset)

    results = session.execute(query).all()
    items = [
        {
            "session_id": row[0].session_id,
            "sleep_date": row[1],
            "rating": row[0].rating,
            "quality_score": normalize_sleep_quality(row[0].rating),
        }
        for row in results[:limit]
    ]
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "has_more": len(results) > limit,
    }
