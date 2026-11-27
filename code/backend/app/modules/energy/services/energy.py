"""Energy score calculation, history, daily summary, and explanation service functions.

Persistence and Concurrency:
- Upsert into energy_scores is idempotent on (user_id, target_date).
  Re-calculating overwrites the stored daily score.
  Documented decision: History-vs-overwrite per date is a pending team decision.
- Query count is bounded and constant with respect to tasks, events, and sessions.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.core.timezones import validate_timezone_name
from app.modules.energy.algorithms.energy.config import (
    ALGORITHM_VERSION,
    DEFAULT_ENERGY_CONFIG,
)
from app.modules.energy.algorithms.energy.energy_calculator import (
    RecoveryInput,
    SleepInput,
    calculate_energy,
)
from app.modules.energy.algorithms.energy.workload_calculator import calculate_workload
from app.modules.energy.algorithms.sleep.sleep_quality import normalize_sleep_quality
from app.modules.energy.models import (
    EnergyScore,
    EnergyScoreRevision,
    SleepQualityRating,
    SleepSchedule,
    SleepSession,
)
from app.modules.energy.services.recovery import (
    compute_recovery_for_day,
    get_local_day_bounds,
    get_recovery_summary,
)
from app.modules.energy.services.scheduler_adapter import (
    EventIntervals,
    load_event_intervals,
    load_task_loads,
)


def to_energy_score_dict(row: EnergyScore) -> dict:
    """Format an EnergyScore model row into the API response dictionary.

    Enforces that whenever status == 'insufficient_data', energy_score,
    energy_level, and confidence are null.
    Energy, workload, and recovery levels are converted to Title case.
    """
    is_insufficient = row.status == "insufficient_data"
    insights_json = row.insights or {}
    if isinstance(insights_json, dict):
        raw_insights = insights_json.get("insights", [])
    elif isinstance(insights_json, list):
        raw_insights = insights_json
    else:
        raw_insights = []

    insight_details = [
        {"code": item["code"], "message": item["message"]}
        for item in raw_insights
        if isinstance(item, dict) and "code" in item and "message" in item
    ]
    insight_messages = [item["message"] for item in insight_details]

    return {
        "id": row.id,
        "target_date": row.target_date,
        "status": row.status,
        "energy_score": None if is_insufficient else (float(row.score) if row.score is not None else None),
        "energy_level": None if is_insufficient else (row.energy_level.title() if row.energy_level else None),
        "confidence": None if is_insufficient else (float(row.confidence) if row.confidence is not None else None),
        "sleep_duration": (
            float(round(Decimal(row.sleep_duration_minutes) / Decimal("60.0"), 2))
            if row.sleep_duration_minutes is not None
            else None
        ),
        "sleep_quality": row.sleep_quality_rating,
        "workload_level": row.workload_level.title() if row.workload_level else None,
        "recovery_level": row.recovery_level.title() if row.recovery_level else None,
        "insights": insight_messages,
        "insight_details": insight_details,
        "calculated_at": row.calculated_at,
    }


def to_energy_explanation_dict(row: EnergyScore) -> dict:
    """Format an EnergyScore model row into the explanation response dictionary."""
    base = to_energy_score_dict(row)
    insights_json = row.insights or {}
    components: list[dict] = []
    algorithm_version: str | None = None
    if isinstance(insights_json, dict):
        components = insights_json.get("components", [])
        raw_ver = insights_json.get("algorithm_version")
        if isinstance(raw_ver, str):
            algorithm_version = raw_ver

    confidence_breakdown = {
        "overall_confidence": base["confidence"],
        "status": base["status"],
    }

    base["components"] = components
    base["confidence_breakdown"] = confidence_breakdown
    base["algorithm_version"] = algorithm_version
    return base


def _resolve_available_minutes(
    session: Session,
    user_id: UUID,
    target_date: date,
) -> int:
    """Derive available waking minutes from the user's active sleep_schedule for target_date.

    Uses the schedule only when is_active is True AND target_date.weekday() is in repeat_days.
    Computes clamp(1440 - sleep_window_minutes, 600, 1200) (overnight-aware), falling back to
    DEFAULT_ENERGY_CONFIG.available_minutes (960) otherwise.
    """
    schedule = session.scalar(
        sa.select(SleepSchedule).where(
            SleepSchedule.user_id == user_id,
            SleepSchedule.is_active == True,
        )
    )
    if schedule is None or target_date.weekday() not in schedule.repeat_days:
        return DEFAULT_ENERGY_CONFIG.available_minutes

    bed_sec = schedule.bedtime.hour * 3600 + schedule.bedtime.minute * 60 + schedule.bedtime.second
    wake_sec = schedule.wake_time.hour * 3600 + schedule.wake_time.minute * 60 + schedule.wake_time.second
    if wake_sec > bed_sec:
        sleep_seconds = wake_sec - bed_sec
    else:
        sleep_seconds = 86400 - bed_sec + wake_sec

    sleep_window_minutes = int(round(sleep_seconds / 60.0))
    return max(600, min(1200, 1440 - sleep_window_minutes))


def _compute_workload_summary(
    session: Session,
    user_id: UUID,
    target_date: date,
    day_start_utc: datetime,
    day_end_utc: datetime,
):
    available_minutes = _resolve_available_minutes(session, user_id, target_date)
    task_loads = load_task_loads(
        session,
        user_id,
        day_start_utc,
        day_end_utc,
        high_priority_min=DEFAULT_ENERGY_CONFIG.high_priority_min,
    )
    event_intervals = load_event_intervals(
        session, user_id, day_start_utc, day_end_utc, target_date=target_date
    )
    combined_intervals = EventIntervals(
        [*task_loads.fixed_intervals, *event_intervals],
        truncated=task_loads.truncated or event_intervals.truncated,
    )
    return calculate_workload(
        task_loads,
        combined_intervals,
        day_start_utc,
        day_end_utc,
        available_minutes=available_minutes,
        extra_high_priority_tasks=task_loads.extra_high_priority_tasks,
        truncated=combined_intervals.truncated,
    )


def get_daily_workload(
    session: Session,
    user_id: UUID,
    target_date: date | None = None,
    timezone_name: str = "UTC",
    *,
    date: date | None = None,
    timezone: str | None = None,
) -> dict:
    """Internal interface (Design Doc section 8): Compute live daily workload metrics.

    FIXED tasks are converted to clipped intervals and unioned with calendar events so
    `fixed_event_duration` includes both FIXED tasks and events without double-counting,
    while `total_task_duration` and `completed_task_duration` cover FLEXIBLE tasks only.
    """
    the_date = target_date if target_date is not None else date
    if the_date is None:
        raise APIError(422, "invalid_request", "date or target_date is required")
    the_tz = timezone if timezone is not None else timezone_name

    day_start_utc, day_end_utc = get_local_day_bounds(the_date, the_tz)
    summary = _compute_workload_summary(session, user_id, the_date, day_start_utc, day_end_utc)

    return {
        "total_task_duration": summary.total_task_minutes,
        "completed_task_duration": summary.completed_task_minutes,
        "fixed_event_duration": summary.fixed_event_minutes,
        "high_priority_tasks": summary.high_priority_tasks,
        "level": summary.level.title() if summary.level else None,
        "has_data": summary.has_data,
    }


def get_current_energy(
    session: Session,
    user_id: UUID,
) -> dict | None:
    """Internal interface (Design Doc section 8): Latest stored energy score data or None."""
    row = session.scalar(
        sa.select(EnergyScore)
        .where(EnergyScore.user_id == user_id)
        .order_by(
            EnergyScore.target_date.desc(),
            EnergyScore.calculated_at.desc(),
            EnergyScore.id.desc(),
        )
    )
    if row is None:
        return None

    is_insufficient = row.status == "insufficient_data"
    return {
        "score": None if is_insufficient else (float(row.score) if row.score is not None else None),
        "level": None if is_insufficient else (row.energy_level.title() if row.energy_level else None),
        "confidence": None if is_insufficient else float(row.confidence),
        "calculated_at": row.calculated_at,
    }


def calculate_energy_for_date(
    session: Session,
    user_id: UUID,
    target_date: date,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Calculate daily energy score and idempotently persist in energy_scores.

    1. Validates target_date bounds against user's local today (today - 366d <= target_date <= today + 1d).
    2. Loads sleep session for sleep_date == target_date.
    3. Loads tasks and events via scheduler adapter and runs calculate_workload.
    4. Computes recovery via compute_recovery_for_day (read-only, no writes).
    5. Calculates energy score using calculate_energy.
    6. Upserts into energy_scores table (ON CONFLICT (user_id, target_date) DO UPDATE).
    """
    try:
        valid_zone = validate_timezone_name(timezone_name)
    except ValueError as exc:
        raise APIError(422, "invalid_request", str(exc)) from exc

    if now is None:
        now_utc = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now_utc = now.replace(tzinfo=timezone.utc)
    else:
        now_utc = now.astimezone(timezone.utc)

    tz = ZoneInfo(valid_zone)
    local_today = now_utc.astimezone(tz).date()

    if target_date > local_today + timedelta(days=1):
        raise APIError(422, "invalid_request", "target_date cannot be in the future beyond tomorrow")
    if target_date < local_today - timedelta(days=366):
        raise APIError(422, "invalid_request", "target_date cannot be older than 366 days")

    # (1) Sleep factor
    sleep_stmt = (
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
    )
    sleep_row = session.execute(sleep_stmt).first()

    sleep_input: SleepInput | None = None
    if sleep_row is not None:
        sess, rating = sleep_row
        if sess.status == "confirmed":
            sleep_input = SleepInput(
                duration_minutes=sess.duration_minutes,
                confirmed=True,
                quality_rating=rating,
            )
        elif sess.status == "pending_confirmation":
            sleep_input = SleepInput(
                duration_minutes=sess.duration_minutes,
                confirmed=False,
                quality_rating=None,
            )

    # (2) Workload factor
    day_start_utc, day_end_utc = get_local_day_bounds(target_date, valid_zone)
    workload_summary = _compute_workload_summary(
        session, user_id, target_date, day_start_utc, day_end_utc
    )

    # (3) Recovery factor
    recovery_res = compute_recovery_for_day(
        session,
        user_id,
        target_date,
        valid_zone,
        workload_level=workload_summary.level,
    )
    if recovery_res is None:
        recovery_input = None
    else:
        detection, rec_score, _ = recovery_res
        recovery_input = RecoveryInput(result=rec_score, data_status=detection.data_status)

    # (4) Calculate energy
    energy_result = calculate_energy(
        sleep=sleep_input,
        workload=workload_summary,
        recovery=recovery_input,
    )

    # (5) Prepare typed columns and structured JSON
    status = energy_result.status
    if status == "ok":
        score_val = Decimal(str(energy_result.score)) if energy_result.score is not None else None
        energy_level_val = energy_result.level
        confidence_val = float(energy_result.confidence) if energy_result.confidence is not None else 0.0
    else:
        score_val = None
        energy_level_val = None
        # Confidence column is NOT NULL in database; store 0.0 for insufficient_data
        confidence_val = 0.0

    # Confirmed sleep only for sleep_duration_minutes
    if sleep_input is not None and sleep_input.confirmed:
        sleep_dur_min = sleep_input.duration_minutes
        sleep_rating = sleep_input.quality_rating
        sleep_qual_score = (
            Decimal(str(normalize_sleep_quality(sleep_rating)))
            if sleep_rating is not None
            else None
        )
    else:
        sleep_dur_min = None
        sleep_rating = None
        sleep_qual_score = None

    # Workload score & level
    workload_component = next(
        (c for c in energy_result.components if c.name == "workload"), None
    )
    if (
        workload_summary.has_data
        and not getattr(workload_summary, "truncated", False)
        and workload_component is not None
    ):
        workload_score_val = Decimal(str(workload_component.score))
        workload_level_val = workload_summary.level
    else:
        workload_score_val = None
        workload_level_val = (
            workload_summary.level
            if (workload_summary.has_data and not getattr(workload_summary, "truncated", False))
            else None
        )

    # Recovery score & level
    recovery_component = next(
        (c for c in energy_result.components if c.name == "recovery"), None
    )
    if (
        recovery_input is not None
        and recovery_input.result.status == "ok"
        and recovery_component is not None
    ):
        recovery_score_val = Decimal(str(recovery_component.score))
        recovery_level_val = recovery_input.result.level
    else:
        recovery_score_val = None
        recovery_level_val = (
            recovery_input.result.level if recovery_input is not None else None
        )

    insights_data = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "components": [
            {
                "name": c.name,
                "score": c.score,
                "weight_used": c.weight_used,
                "contribution": c.contribution,
            }
            for c in energy_result.components
        ],
        "insights": [
            {"code": ins.code, "message": ins.message}
            for ins in energy_result.insights
        ],
    }

    # Upsert into energy_scores
    upsert_stmt = (
        insert(EnergyScore)
        .values(
            user_id=user_id,
            target_date=target_date,
            status=status,
            score=score_val,
            energy_level=energy_level_val,
            confidence=confidence_val,
            sleep_duration_minutes=sleep_dur_min,
            sleep_quality_score=sleep_qual_score,
            sleep_quality_rating=sleep_rating,
            workload_score=workload_score_val,
            workload_level=workload_level_val,
            recovery_score=recovery_score_val,
            recovery_level=recovery_level_val,
            insights=insights_data,
            calculated_at=now_utc,
        )
        .on_conflict_do_update(
            constraint="uq_energy_scores_user_target_date",
            set_={
                "status": status,
                "score": score_val,
                "energy_level": energy_level_val,
                "confidence": confidence_val,
                "sleep_duration_minutes": sleep_dur_min,
                "sleep_quality_score": sleep_qual_score,
                "sleep_quality_rating": sleep_rating,
                "workload_score": workload_score_val,
                "workload_level": workload_level_val,
                "recovery_score": recovery_score_val,
                "recovery_level": recovery_level_val,
                "insights": insights_data,
                "calculated_at": now_utc,
            },
        )
        .returning(EnergyScore.id)
    )

    score_id = session.scalar(upsert_stmt)
    session.flush()

    # The upsert on energy_scores row-locks (user_id, target_date) until commit,
    # so the revision check and insert that follow are serialized per day.
    # Compare against the algorithm result values (e.g. confidence=None when
    # insufficient_data), NOT the stored energy_scores columns (confidence=0.0).
    rev_confidence_val = (
        float(energy_result.confidence)
        if status == "ok" and energy_result.confidence is not None
        else None
    )
    try:
        with session.begin_nested():
            latest_rev = session.execute(
                sa.select(
                    EnergyScoreRevision.status,
                    EnergyScoreRevision.score,
                    EnergyScoreRevision.energy_level,
                    EnergyScoreRevision.confidence,
                    EnergyScoreRevision.algorithm_version,
                )
                .where(
                    EnergyScoreRevision.user_id == user_id,
                    EnergyScoreRevision.target_date == target_date,
                )
                .order_by(
                    EnergyScoreRevision.calculated_at.desc(),
                    EnergyScoreRevision.id.desc(),
                )
                .limit(1)
            ).first()

            should_insert_rev = latest_rev is None or (
                latest_rev.status != status
                or latest_rev.score != score_val
                or latest_rev.energy_level != energy_level_val
                or latest_rev.confidence != rev_confidence_val
                or latest_rev.algorithm_version != ALGORITHM_VERSION
            )
            if should_insert_rev:
                session.add(
                    EnergyScoreRevision(
                        user_id=user_id,
                        target_date=target_date,
                        status=status,
                        score=score_val,
                        energy_level=energy_level_val,
                        confidence=rev_confidence_val,
                        algorithm_version=ALGORITHM_VERSION,
                        result=insights_data,
                        calculated_at=now_utc,
                    )
                )
                session.flush()
    except IntegrityError:
        # Savepoint rolled back; never fail the request on revision conflict.
        pass

    row = session.scalar(
        sa.select(EnergyScore).where(
            EnergyScore.id == score_id,
            EnergyScore.user_id == user_id,
        )
    )
    session.refresh(row)
    return to_energy_score_dict(row)


def get_current_energy_score(
    session: Session,
    user_id: UUID,
) -> dict:
    """Retrieve the caller's most recent stored energy score without calculating."""
    row = session.scalar(
        sa.select(EnergyScore)
        .where(EnergyScore.user_id == user_id)
        .order_by(
            EnergyScore.target_date.desc(),
            EnergyScore.calculated_at.desc(),
            EnergyScore.id.desc(),
        )
    )
    if row is None:
        raise APIError(404, "not_found", "Energy score not found")
    return to_energy_score_dict(row)


def list_energy_history(
    session: Session,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """List caller's stored energy scores, newest target_date first."""
    if date_from and date_to and date_to < date_from:
        raise APIError(422, "invalid_request", "date_to cannot precede date_from")

    query = sa.select(EnergyScore).where(EnergyScore.user_id == user_id)

    if status is not None:
        query = query.where(EnergyScore.status == status)
    if date_from is not None:
        query = query.where(EnergyScore.target_date >= date_from)
    if date_to is not None:
        query = query.where(EnergyScore.target_date <= date_to)

    query = (
        query.order_by(
            EnergyScore.target_date.desc(),
            EnergyScore.calculated_at.desc(),
            EnergyScore.id.desc(),
        )
        .limit(limit + 1)
        .offset(offset)
    )

    rows = session.scalars(query).all()
    items = [to_energy_score_dict(row) for row in rows[:limit]]
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "has_more": len(rows) > limit,
    }


def get_explanation(
    session: Session,
    user_id: UUID,
    score_id: UUID,
) -> dict:
    """Retrieve component and insight breakdown for a stored energy score."""
    row = session.scalar(
        sa.select(EnergyScore).where(
            EnergyScore.id == score_id,
            EnergyScore.user_id == user_id,
        )
    )
    if row is None:
        raise APIError(404, "not_found", "Energy score explanation not found")
    return to_energy_explanation_dict(row)


def get_daily_summary(
    session: Session,
    user_id: UUID,
    target_date: date,
    timezone_name: str = "UTC",
) -> dict:
    """Read-only aggregation of daily metrics: stored score, sleep, workload, and recovery."""
    try:
        valid_zone = validate_timezone_name(timezone_name)
    except ValueError as exc:
        raise APIError(422, "invalid_request", str(exc)) from exc

    # 1. Stored energy score (or None)
    score_row = session.scalar(
        sa.select(EnergyScore).where(
            EnergyScore.user_id == user_id,
            EnergyScore.target_date == target_date,
        )
    )
    score_dict = to_energy_score_dict(score_row) if score_row is not None else None

    # 2. Sleep session summary
    sleep_stmt = (
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
    )
    sleep_res = session.execute(sleep_stmt).first()
    if sleep_res is not None:
        s_row, q_rating = sleep_res
        sleep_summary = {
            "status": s_row.status,
            "duration_minutes": s_row.duration_minutes,
            "quality_rating": q_rating,
            "quality_score": (
                normalize_sleep_quality(q_rating) if q_rating is not None else None
            ),
        }
    else:
        sleep_summary = None

    # 3. Live workload summary
    workload_summary = get_daily_workload(
        session, user_id, target_date=target_date, timezone_name=valid_zone
    )

    # 4. Recovery summary
    recovery_summary = get_recovery_summary(
        session, user_id, target_date=target_date, timezone_name=valid_zone
    )

    return {
        "date": target_date,
        "timezone": valid_zone,
        "energy_score": score_dict,
        "sleep": sleep_summary,
        "workload": workload_summary,
        "recovery": recovery_summary,
    }
