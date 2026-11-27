"""Comprehensive tests for Energy module Energy Score calculation and endpoints."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.energy.algorithms.energy.config import ALGORITHM_VERSION
from app.modules.energy.algorithms.sleep.sleep_quality import normalize_sleep_quality
from app.modules.energy.models import (
    EnergyFeedback,
    EnergyScore,
    EnergyScoreRevision,
    FocusSession,
    RecoveryPeriod,
    RecoveryStrategy,
    SleepQualityRating,
    SleepSchedule,
    SleepSession,
)
from app.modules.energy.services import energy as energy_service
from app.modules.energy.services.recovery import get_local_day_bounds
from app.modules.scheduler.models import Event, Task
from tests.api_support import seed_auth_user


@pytest.fixture
def energy_api(api):
    from app.modules.energy.router import router as energy_router

    if not getattr(api.client.app.state, "_energy_router_included", False):
        api.client.app.include_router(energy_router, prefix="/api/v1")
        api.client.app.state._energy_router_included = True
    return api


@pytest.fixture
def test_users(database):
    user1 = seed_auth_user(database)
    user2 = seed_auth_user(database)
    with database.admin.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO public.profiles (id) VALUES (:u1), (:u2)"),
            {"u1": user1, "u2": user2},
        )
    return user1, user2


def test_calculate_all_three_factors_and_typed_columns(energy_api, database):
    user_id = energy_api.users[0]
    target_d = date(2026, 11, 20)
    tz_name = "UTC"
    day_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    # 1. Seed Confirmed Sleep Session with Quality Rating
    with Session(database.admin) as session:
        sleep_sess = SleepSession(
            user_id=user_id,
            sleep_date=target_d,
            start_time=datetime(2026, 11, 19, 22, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc),
            duration_minutes=480,  # 8 hours optimal
            timezone=tz_name,
            status="confirmed",
            source="manual",
            confidence=1.0,
        )
        session.add(sleep_sess)
        session.flush()

        rating = SleepQualityRating(
            user_id=user_id,
            session_id=sleep_sess.id,
            rating=4,
        )
        session.add(rating)

        # 2. Seed Task and Event (Workload)
        task = Task(
            user_id=user_id,
            title="Design Review",
            task_type="FIXED",
            estimated_duration_minutes=120,
            category="WORK",
            priority=3,
            fixed_start_at=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
            fixed_end_at=datetime(2026, 11, 20, 11, 0, tzinfo=timezone.utc),
            status="UNSCHEDULED",
        )
        session.add(task)

        # 3. Seed Strategy and Focus Sessions (Recovery)
        strategy = RecoveryStrategy(
            user_id=user_id,
            strategy_type="POMODORO",
            work_duration_minutes=25,
            break_duration_minutes=5,
            long_break_duration_minutes=20,
            long_break_after_sessions=4,
            is_active=True,
        )
        session.add(strategy)

        # 2 completed focus sessions with a 5-minute break
        f1 = FocusSession(
            user_id=user_id,
            start_time=datetime(2026, 11, 20, 11, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 11, 20, 11, 25, tzinfo=timezone.utc),
            duration_minutes=25,
            status="COMPLETED",
        )
        f2 = FocusSession(
            user_id=user_id,
            start_time=datetime(2026, 11, 20, 11, 30, tzinfo=timezone.utc),
            end_time=datetime(2026, 11, 20, 11, 55, tzinfo=timezone.utc),
            duration_minutes=25,
            status="COMPLETED",
        )
        session.add_all([f1, f2])
        session.commit()

    # Call calculate endpoint
    res = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": "2026-11-20", "timezone": tz_name},
    )
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "ok"
    assert data["energy_score"] is not None
    assert data["energy_level"] in ("Low", "Medium", "High")
    assert data["confidence"] is not None and 0.0 <= data["confidence"] <= 1.0
    assert data["sleep_duration"] == 8.0  # 480 minutes / 60
    assert data["sleep_quality"] == 4
    assert data["workload_level"] in ("Low", "Medium", "High")
    assert data["recovery_level"] in ("Low", "Medium", "High")
    assert isinstance(data["insights"], list)
    assert isinstance(data["insight_details"], list)

    # Amendment 2: Verify typed columns in DB match the response
    with Session(database.admin) as session:
        row = session.scalar(
            sa.select(EnergyScore).where(
                EnergyScore.user_id == user_id,
                EnergyScore.target_date == target_d,
            )
        )
        assert row is not None
        assert row.status == "ok"
        assert float(row.score) == data["energy_score"]
        assert row.energy_level == data["energy_level"].upper()
        assert row.confidence == data["confidence"]
        assert row.sleep_duration_minutes == 480
        assert row.sleep_quality_rating == 4
        assert float(row.sleep_quality_score) == 75.0  # normalize_sleep_quality(4)
        assert row.workload_score is not None
        assert row.workload_level == data["workload_level"].upper()
        assert row.recovery_score is not None
        assert row.recovery_level == data["recovery_level"].upper()


def test_sleep_only_low_confidence(energy_api, database):
    user_id = energy_api.users[0]
    target_d = "2026-11-20"

    # Confirmed sleep only, no tasks, no recovery strategy
    with Session(database.admin) as session:
        sleep_sess = SleepSession(
            user_id=user_id,
            sleep_date=date(2026, 11, 20),
            start_time=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc),
            duration_minutes=480,
            timezone="UTC",
            status="confirmed",
            source="manual",
            confidence=1.0,
        )
        session.add(sleep_sess)
        session.commit()

    res = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": target_d, "timezone": "UTC"},
    )
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "ok"
    assert data["confidence"] is not None and data["confidence"] < 0.70
    codes = [item["code"] for item in data["insight_details"]]
    assert "LOW_CONFIDENCE" in codes
    assert "WORKLOAD_NO_DATA" in codes
    assert "RECOVERY_NO_DATA" in codes


def test_unconfirmed_sleep_insufficient_data(energy_api, database):
    user_id = energy_api.users[0]
    target_d = "2026-11-20"

    # Unconfirmed sleep session
    with Session(database.admin) as session:
        sleep_sess = SleepSession(
            user_id=user_id,
            sleep_date=date(2026, 11, 20),
            start_time=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc),
            duration_minutes=480,
            timezone="UTC",
            status="pending_confirmation",
            source="android_usage",
            confidence=0.8,
        )
        session.add(sleep_sess)
        session.commit()

    res = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": target_d, "timezone": "UTC"},
    )
    assert res.status_code == 200
    data = res.json()

    # Amendment 1: confidence=null, energy_score=null, energy_level=null
    assert data["status"] == "insufficient_data"
    assert data["energy_score"] is None
    assert data["energy_level"] is None
    assert data["confidence"] is None

    codes = [item["code"] for item in data["insight_details"]]
    assert "SLEEP_UNCONFIRMED" in codes
    assert "INSUFFICIENT_DATA" in codes

    # DB confidence is 0.0, and sleep_duration_minutes is NULL (unconfirmed never writes it)
    with Session(database.admin) as session:
        row = session.scalar(
            sa.select(EnergyScore).where(
                EnergyScore.user_id == user_id,
                EnergyScore.target_date == date(2026, 11, 20),
            )
        )
        assert row.status == "insufficient_data"
        assert row.confidence == 0.0
        assert row.score is None
        assert row.energy_level is None
        assert row.sleep_duration_minutes is None


def test_no_sleep_insufficient_data(energy_api):
    res = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": "2026-11-20", "timezone": "UTC"},
    )
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "insufficient_data"
    assert data["energy_score"] is None
    assert data["energy_level"] is None
    assert data["confidence"] is None
    codes = [item["code"] for item in data["insight_details"]]
    assert "SLEEP_NO_DATA" in codes
    assert "INSUFFICIENT_DATA" in codes


def test_inactive_or_missing_recovery_strategy_no_error(energy_api, database):
    user_id = energy_api.users[0]
    target_d = "2026-11-20"

    # Confirmed sleep + disabled strategy
    with Session(database.admin) as session:
        session.add(
            SleepSession(
                user_id=user_id,
                sleep_date=date(2026, 11, 20),
                start_time=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc),
                duration_minutes=480,
                timezone="UTC",
                status="confirmed",
                source="manual",
                confidence=1.0,
            )
        )
        session.add(
            RecoveryStrategy(
                user_id=user_id,
                strategy_type="POMODORO",
                is_active=False,  # disabled!
            )
        )
        session.commit()

    # Recovery strategy missing/inactive must NOT fail or 409
    res = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": target_d, "timezone": "UTC"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["recovery_level"] is None
    codes = [item["code"] for item in data["insight_details"]]
    assert "RECOVERY_NO_DATA" in codes


def test_calculate_twice_idempotent_overwrite(energy_api, database):
    user_id = energy_api.users[0]
    target_d = "2026-11-20"

    with Session(database.admin) as session:
        session.add(
            SleepSession(
                user_id=user_id,
                sleep_date=date(2026, 11, 20),
                start_time=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc),
                duration_minutes=480,
                timezone="UTC",
                status="confirmed",
                source="manual",
                confidence=1.0,
            )
        )
        session.commit()

    # First calculate
    t1 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    with Session(database.admin) as session:
        r1 = energy_service.calculate_energy_for_date(
            session, user_id, date(2026, 11, 20), "UTC", now=t1
        )
        session.commit()

    # Second calculate with new timestamp
    t2 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    with Session(database.admin) as session:
        r2 = energy_service.calculate_energy_for_date(
            session, user_id, date(2026, 11, 20), "UTC", now=t2
        )
        session.commit()

    assert r1["id"] == r2["id"]
    assert r2["calculated_at"] == t2

    # Exactly 1 row in DB
    with Session(database.admin) as session:
        rows = session.scalars(
            sa.select(EnergyScore).where(
                EnergyScore.user_id == user_id,
                EnergyScore.target_date == date(2026, 11, 20),
            )
        ).all()
        assert len(rows) == 1


def test_concurrent_calculate_for_same_day(energy_api, database):
    user_id = energy_api.users[0]
    with Session(database.admin) as session:
        session.add(
            SleepSession(
                user_id=user_id,
                sleep_date=date(2026, 11, 20),
                start_time=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc),
                duration_minutes=480,
                timezone="UTC",
                status="confirmed",
                source="manual",
                confidence=1.0,
            )
        )
        session.commit()

    def do_calc():
        with Session(database.admin) as session:
            res = energy_service.calculate_energy_for_date(
                session, user_id, date(2026, 11, 20), "UTC"
            )
            session.commit()
            return res

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(do_calc) for _ in range(4)]
        results = [f.result() for f in futures]

    # All returned same id and succeeded without 5xx
    ids = {r["id"] for r in results}
    assert len(ids) == 1

    with Session(database.admin) as session:
        rows = session.scalars(
            sa.select(EnergyScore).where(
                EnergyScore.user_id == user_id,
                EnergyScore.target_date == date(2026, 11, 20),
            )
        ).all()
        assert len(rows) == 1


def test_calculate_never_writes_recovery_periods(energy_api, database):
    user_id = energy_api.users[0]
    with Session(database.admin) as session:
        session.add(
            SleepSession(
                user_id=user_id,
                sleep_date=date(2026, 11, 20),
                start_time=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc),
                duration_minutes=480,
                timezone="UTC",
                status="confirmed",
                source="manual",
                confidence=1.0,
            )
        )
        session.add(
            RecoveryStrategy(
                user_id=user_id,
                strategy_type="POMODORO",
                is_active=True,
            )
        )
        session.add_all([
            FocusSession(
                user_id=user_id,
                start_time=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 20, 9, 25, tzinfo=timezone.utc),
                duration_minutes=25,
                status="COMPLETED",
            ),
            FocusSession(
                user_id=user_id,
                start_time=datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 20, 9, 55, tzinfo=timezone.utc),
                duration_minutes=25,
                status="COMPLETED",
            ),
        ])
        session.commit()

    with Session(database.admin) as session:
        periods_before = session.scalar(
            sa.select(sa.func.count(RecoveryPeriod.id)).where(RecoveryPeriod.user_id == user_id)
        )

    res = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": "2026-11-20", "timezone": "UTC"},
    )
    assert res.status_code == 200

    with Session(database.admin) as session:
        periods_after = session.scalar(
            sa.select(sa.func.count(RecoveryPeriod.id)).where(RecoveryPeriod.user_id == user_id)
        )

    assert periods_before == periods_after == 0


def test_target_date_bounds_validation(energy_api):
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()

    # Day after tomorrow -> 422
    future_date = (today + timedelta(days=2)).isoformat()
    res_future = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": future_date, "timezone": "UTC"},
    )
    assert res_future.status_code == 422

    # Older than 366 days -> 422
    ancient_date = (today - timedelta(days=367)).isoformat()
    res_old = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": ancient_date, "timezone": "UTC"},
    )
    assert res_old.status_code == 422


def test_current_endpoint_and_no_writes(energy_api, database):
    user_id = energy_api.users[0]

    # GET /energy/current when no score stored -> 404
    res_404 = energy_api.client.get("/api/v1/energy/current", headers=energy_api.headers)
    assert res_404.status_code == 404

    # Seed an EnergyScore
    with Session(database.admin) as session:
        session.add(
            EnergyScore(
                user_id=user_id,
                target_date=date(2026, 11, 20),
                status="ok",
                score=Decimal("82.50"),
                energy_level="HIGH",
                confidence=0.85,
                calculated_at=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
            )
        )
        session.commit()

    # GET /energy/current returns the score and performs NO writes
    res_ok = energy_api.client.get("/api/v1/energy/current", headers=energy_api.headers)
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["energy_score"] == 82.50
    assert data["energy_level"] == "High"
    assert data["confidence"] == 0.85


def test_history_endpoint_and_pagination(energy_api, database):
    user_id = energy_api.users[0]

    # Seed 3 scores on 3 days
    with Session(database.admin) as session:
        for i in range(3):
            session.add(
                EnergyScore(
                    user_id=user_id,
                    target_date=date(2026, 11, 18 + i),
                    status="ok" if i != 1 else "insufficient_data",
                    score=Decimal("80.00") if i != 1 else None,
                    energy_level="HIGH" if i != 1 else None,
                    confidence=0.8 if i != 1 else 0.0,
                    calculated_at=datetime(2026, 11, 18 + i, 8, 0, tzinfo=timezone.utc),
                )
            )
        session.commit()

    # Paginated query with limit=2
    res_p1 = energy_api.client.get(
        "/api/v1/energy/history?limit=2&offset=0",
        headers=energy_api.headers,
    )
    assert res_p1.status_code == 200
    d1 = res_p1.json()
    assert len(d1["items"]) == 2
    assert d1["has_more"] is True
    # Newest target_date first
    assert d1["items"][0]["target_date"] == "2026-11-20"
    assert d1["items"][1]["target_date"] == "2026-11-19"

    # Amendment 1 on history: item with insufficient_data has null score, level, confidence
    assert d1["items"][1]["status"] == "insufficient_data"
    assert d1["items"][1]["energy_score"] is None
    assert d1["items"][1]["energy_level"] is None
    assert d1["items"][1]["confidence"] is None

    # Filter date_to < date_from -> 422
    res_err = energy_api.client.get(
        "/api/v1/energy/history?date_from=2026-11-20&date_to=2026-11-18",
        headers=energy_api.headers,
    )
    assert res_err.status_code == 422


def test_daily_summary_read_only_and_nulls(energy_api, database):
    user_id = energy_api.users[0]

    # Seed sleep session and focus sessions
    with Session(database.admin) as session:
        session.add(
            SleepSession(
                user_id=user_id,
                sleep_date=date(2026, 11, 20),
                start_time=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc),
                duration_minutes=480,
                timezone="UTC",
                status="confirmed",
                source="manual",
                confidence=1.0,
            )
        )
        session.commit()

    # Record count before
    with Session(database.admin) as session:
        scores_before = session.scalar(sa.select(sa.func.count(EnergyScore.id)))

    # GET /energy/daily-summary
    res = energy_api.client.get(
        "/api/v1/energy/daily-summary?date=2026-11-20&timezone=UTC",
        headers=energy_api.headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["date"] == "2026-11-20"
    assert data["timezone"] == "UTC"
    assert data["sleep"]["status"] == "confirmed"
    assert data["sleep"]["duration_minutes"] == 480
    assert data["workload"]["has_data"] is False
    assert data["recovery"]["periods_count"] == 0

    # Verify daily-summary performs NO writes
    with Session(database.admin) as session:
        scores_after = session.scalar(sa.select(sa.func.count(EnergyScore.id)))
        assert scores_before == scores_after


def test_explanation_endpoint_and_cross_user_404(energy_api, database, test_users):
    user1, user2 = test_users

    # Create score for user2
    with Session(database.admin) as session:
        score_u2 = EnergyScore(
            user_id=user2,
            target_date=date(2026, 11, 20),
            status="ok",
            score=Decimal("90.00"),
            energy_level="HIGH",
            confidence=0.9,
            insights={
                "schema_version": 1,
                "components": [
                    {"name": "sleep", "score": 90.0, "weight_used": 0.5, "contribution": 45.0}
                ],
                "insights": [{"code": "OPTIMAL_SLEEP", "message": "Optimal sleep"}],
            },
            calculated_at=datetime.now(timezone.utc),
        )
        session.add(score_u2)
        session.commit()
        u2_id = score_u2.id

    # user1 tries to access user2's explanation -> 404
    res_forbidden = energy_api.client.get(
        f"/api/v1/energy/explanation/{u2_id}",
        headers=energy_api.headers,
    )
    assert res_forbidden.status_code == 404

    # Non-existent ID -> 404
    res_random = energy_api.client.get(
        f"/api/v1/energy/explanation/{uuid4()}",
        headers=energy_api.headers,
    )
    assert res_random.status_code == 404


def test_unauthenticated_requests_401(energy_api):
    assert energy_api.client.post("/api/v1/energy/calculate", json={"target_date": "2026-11-20", "timezone": "UTC"}).status_code == 401
    assert energy_api.client.get("/api/v1/energy/current").status_code == 401
    assert energy_api.client.get("/api/v1/energy/history").status_code == 401
    assert energy_api.client.get("/api/v1/energy/daily-summary?date=2026-11-20&timezone=UTC").status_code == 401
    assert energy_api.client.get(f"/api/v1/energy/explanation/{uuid4()}").status_code == 401


def test_internal_interfaces(database, test_users):
    user1, _ = test_users

    with Session(database.admin) as session:
        # 1. get_current_energy when empty
        curr_empty = energy_service.get_current_energy(session, user1)
        assert curr_empty is None

        # 2. Add score
        session.add(
            EnergyScore(
                user_id=user1,
                target_date=date(2026, 11, 20),
                status="ok",
                score=Decimal("88.00"),
                energy_level="HIGH",
                confidence=0.88,
                calculated_at=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
            )
        )
        session.commit()

        curr = energy_service.get_current_energy(session, user1)
        assert curr is not None
        assert curr["score"] == 88.00
        assert curr["level"] == "High"
        assert curr["confidence"] == 0.88

        # 3. get_daily_workload
        workload = energy_service.get_daily_workload(
            session, user1, date(2026, 11, 20), "UTC"
        )
        assert workload["has_data"] is False
        assert workload["total_task_duration"] == 0


def test_statement_count_in_calculate(energy_api, database):
    user_id = energy_api.users[0]
    target_d = date(2026, 11, 20)

    # Calculate once with statement tracking
    stmt_count = 0
    with Session(database.admin) as session:
        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            nonlocal stmt_count
            stmt_count += 1

        sa.event.listen(database.admin, "before_cursor_execute", before_cursor_execute)
        try:
            energy_service.calculate_energy_for_date(
                session, user_id, target_d, "UTC"
            )
        finally:
            sa.event.remove(database.admin, "before_cursor_execute", before_cursor_execute)

    # 1 sleep + 1 sleep_schedule + 1 tasks + 2 events + 1..2 recovery + 1 upsert
    # + 2 savepoint (SAVEPOINT/RELEASE) + 1 rev select + 1 rev insert + 1..2 fetch/refresh
    assert stmt_count <= 14


def test_available_minutes_from_sleep_schedule(database, test_users):
    """Available waking minutes derive from active sleep_schedule when weekday in repeat_days."""
    user1, _ = test_users
    # 2026-11-20 is Friday -> weekday() == 4
    friday = date(2026, 11, 20)
    # 2026-11-21 is Saturday -> weekday() == 5
    saturday = date(2026, 11, 21)

    with Session(database.admin) as session:
        # 1. Missing schedule -> fallback 960
        assert energy_service._resolve_available_minutes(session, user1, friday) == 960

        # 2. Overnight schedule (23:00 -> 05:00 = 360m sleep -> 1080m available), active on Friday (4)
        sched = SleepSchedule(
            user_id=user1,
            bedtime=time(23, 0),
            wake_time=time(5, 0),
            timezone="UTC",
            repeat_days=[4],
            is_active=True,
        )
        session.add(sched)
        session.commit()

        assert energy_service._resolve_available_minutes(session, user1, friday) == 1080
        # Weekday not in repeat_days -> fallback 960
        assert energy_service._resolve_available_minutes(session, user1, saturday) == 960

        # 3. Same-day window (01:00 -> 07:00 = 360m sleep -> 1080m available)
        sched.bedtime = time(1, 0)
        sched.wake_time = time(7, 0)
        sched.repeat_days = [4, 5]
        session.commit()
        assert energy_service._resolve_available_minutes(session, user1, friday) == 1080

        # 4. Clamping upper bound: 2h sleep (23:00 -> 01:00 = 120m -> 1320 clamped to 1200)
        sched.bedtime = time(23, 0)
        sched.wake_time = time(1, 0)
        session.commit()
        assert energy_service._resolve_available_minutes(session, user1, friday) == 1200

        # 5. Clamping lower bound: 16h sleep (20:00 -> 12:00 = 960m -> 480 clamped to 600)
        sched.bedtime = time(20, 0)
        sched.wake_time = time(12, 0)
        session.commit()
        assert energy_service._resolve_available_minutes(session, user1, friday) == 600

        # 6. Inactive schedule -> fallback 960
        sched.is_active = False
        session.commit()
        assert energy_service._resolve_available_minutes(session, user1, friday) == 960


def test_revisions_deduplication_changes_and_insufficient_data(energy_api, database):
    """Recalculating same inputs creates 1 revision; changed inputs create a 2nd; insufficient_data stores real NULL confidence."""
    user_id = energy_api.users[0]
    target_d = date(2026, 11, 20)

    # Case A: insufficient_data day calculated twice -> exactly 1 revision with confidence IS NULL
    res_insuf1 = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": target_d.isoformat(), "timezone": "UTC"},
    )
    assert res_insuf1.status_code == 200
    assert res_insuf1.json()["status"] == "insufficient_data"

    res_insuf2 = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": target_d.isoformat(), "timezone": "UTC"},
    )
    assert res_insuf2.status_code == 200

    with Session(database.admin) as session:
        revs = session.scalars(
            sa.select(EnergyScoreRevision)
            .where(
                EnergyScoreRevision.user_id == user_id,
                EnergyScoreRevision.target_date == target_d,
            )
            .order_by(EnergyScoreRevision.calculated_at.asc(), EnergyScoreRevision.id.asc())
        ).all()
        assert len(revs) == 1
        assert revs[0].status == "insufficient_data"
        assert revs[0].score is None
        assert revs[0].energy_level is None
        assert revs[0].confidence is None
        assert revs[0].algorithm_version == ALGORITHM_VERSION
        assert revs[0].result["algorithm_version"] == ALGORITHM_VERSION

    # Case B: Add confirmed sleep -> status/score/confidence change -> creates 2nd revision
    with Session(database.admin) as session:
        session.add(
            SleepSession(
                user_id=user_id,
                sleep_date=target_d,
                start_time=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc),
                duration_minutes=480,
                timezone="UTC",
                status="confirmed",
                source="manual",
                confidence=1.0,
            )
        )
        session.commit()

    res_ok1 = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": target_d.isoformat(), "timezone": "UTC"},
    )
    assert res_ok1.status_code == 200
    score_id = res_ok1.json()["id"]

    # Recalculate identical ok inputs -> still 2 revisions total
    res_ok2 = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=energy_api.headers,
        json={"target_date": target_d.isoformat(), "timezone": "UTC"},
    )
    assert res_ok2.status_code == 200

    with Session(database.admin) as session:
        revs2 = session.scalars(
            sa.select(EnergyScoreRevision)
            .where(
                EnergyScoreRevision.user_id == user_id,
                EnergyScoreRevision.target_date == target_d,
            )
            .order_by(EnergyScoreRevision.calculated_at.asc(), EnergyScoreRevision.id.asc())
        ).all()
        assert len(revs2) == 2
        assert revs2[1].status == "ok"
        assert revs2[1].confidence is not None
        assert revs2[1].algorithm_version == ALGORITHM_VERSION

    # Explanation endpoint includes algorithm_version
    res_exp = energy_api.client.get(
        f"/api/v1/energy/explanation/{score_id}",
        headers=energy_api.headers,
    )
    assert res_exp.status_code == 200
    assert res_exp.json()["algorithm_version"] == ALGORITHM_VERSION


def test_concurrent_calculate_no_duplicate_revisions(energy_api, database):
    """Concurrent calculations for the same day must never 5xx and must emit only 1 identical revision."""
    user_id = energy_api.users[0]
    target_d = date(2026, 11, 20)

    with Session(database.admin) as session:
        session.add(
            SleepSession(
                user_id=user_id,
                sleep_date=target_d,
                start_time=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc),
                duration_minutes=480,
                timezone="UTC",
                status="confirmed",
                source="manual",
                confidence=1.0,
            )
        )
        session.commit()

    def do_calc():
        return energy_api.client.post(
            "/api/v1/energy/calculate",
            headers=energy_api.headers,
            json={"target_date": target_d.isoformat(), "timezone": "UTC"},
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(do_calc) for _ in range(4)]
        responses = [f.result() for f in futures]

    assert all(r.status_code == 200 for r in responses)

    with Session(database.admin) as session:
        revs = session.scalars(
            sa.select(EnergyScoreRevision).where(
                EnergyScoreRevision.user_id == user_id,
                EnergyScoreRevision.target_date == target_d,
            )
        ).all()
        assert len(revs) == 1


def test_revisions_and_feedback_rls_and_grants(database, signer, users):
    """Verify append-only privileges on energy_score_revisions and owner RLS/grants on energy_feedback."""
    from sqlalchemy.exc import DBAPIError

    u0, u1 = users
    with database.runtime.user_session(signer.token(u0), signer.verifier) as session:
        rev = EnergyScoreRevision(
            user_id=u0,
            target_date=date(2026, 11, 20),
            status="ok",
            score=Decimal("85.00"),
            energy_level="HIGH",
            confidence=0.8,
            algorithm_version=ALGORITHM_VERSION,
            result={"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "components": [], "insights": []},
        )
        fb = EnergyFeedback(
            user_id=u0,
            target_date=date(2026, 11, 20),
            felt_energy=4,
            score_at_feedback=Decimal("85.00"),
            level_at_feedback="HIGH",
            algorithm_version=ALGORITHM_VERSION,
        )
        session.add_all([rev, fb])
        session.flush()
        rev_id = rev.id
        fb_id = fb.id

    # UPDATE on energy_score_revisions must be denied (no UPDATE grant)
    with pytest.raises(DBAPIError) as err, database.runtime.user_session(signer.token(u0), signer.verifier) as session:
        session.execute(
            sa.update(EnergyScoreRevision).where(EnergyScoreRevision.id == rev_id).values(status="insufficient_data")
        )
    assert err.value.orig.sqlstate == "42501"

    # Cross-user isolation and own feedback update
    with database.runtime.user_session(signer.token(u1), signer.verifier) as session:
        assert session.scalars(sa.select(EnergyScoreRevision)).all() == []
        assert session.scalars(sa.select(EnergyFeedback)).all() == []

    with database.runtime.user_session(signer.token(u0), signer.verifier) as session:
        session.execute(
            sa.update(EnergyFeedback).where(EnergyFeedback.id == fb_id).values(felt_energy=5)
        )
        row = session.scalar(sa.select(EnergyFeedback).where(EnergyFeedback.id == fb_id))
        assert row.felt_energy == 5
