"""Tests for energy data erase, retention housekeeping, feedback, and revisions endpoints."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.energy.algorithms.energy.config import ALGORITHM_VERSION
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
    SleepUsageSignal,
)
from app.modules.energy.schemas import (
    ScreenEvent,
    SignalSyncRequest,
    SleepEstimateRequest,
)
from app.modules.energy.services import energy as energy_service
from app.modules.energy.services import retention as retention_service
from app.modules.energy.services import sleep_signals as signals_service
from app.modules.scheduler.models import Event, Task
from app.modules.user.models import Profile, UserPreferences
from tests.api_support import seed_auth_user


@pytest.fixture
def energy_api(api):
    from app.modules.energy.router import router as energy_router

    if not getattr(api.client.app.state, "_energy_router_included", False):
        api.client.app.include_router(energy_router, prefix="/api/v1")
        api.client.app.state._energy_router_included = True
    return api


@pytest.fixture
def isolated_users(database):
    user1 = seed_auth_user(database)
    user2 = seed_auth_user(database)
    with database.admin.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO public.profiles (id) VALUES (:u1), (:u2)"),
            {"u1": user1, "u2": user2},
        )
    return user1, user2


def _seed_all_user_data(session: Session, user_id, day_offset: int = 0) -> None:
    target_d = date(2026, 11, 20) + timedelta(days=day_offset)
    start_dt = datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc) + timedelta(days=day_offset)
    end_dt = datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc) + timedelta(days=day_offset)

    session.add(
        SleepSchedule(
            user_id=user_id,
            bedtime=time(23, 0),
            wake_time=time(7, 0),
            timezone="UTC",
            repeat_days=[0, 1, 2, 3, 4, 5, 6],
        )
    )
    sleep_sess = SleepSession(
        user_id=user_id,
        sleep_date=target_d,
        start_time=start_dt,
        end_time=end_dt,
        duration_minutes=480,
        timezone="UTC",
        source="android_usage",
        confidence=0.85,
        status="confirmed",
    )
    session.add(sleep_sess)
    session.flush()

    session.add(SleepQualityRating(user_id=user_id, session_id=sleep_sess.id, rating=4))
    session.add_all([
        SleepUsageSignal(
            user_id=user_id,
            session_id=sleep_sess.id,
            event_type="SCREEN_NON_INTERACTIVE",
            event_timestamp=start_dt,
        ),
        SleepUsageSignal(
            user_id=user_id,
            session_id=None,
            event_type="SCREEN_INTERACTIVE",
            event_timestamp=end_dt,
        ),
    ])
    session.add(RecoveryStrategy(user_id=user_id, strategy_type="POMODORO"))
    session.add(
        FocusSession(
            user_id=user_id,
            start_time=end_dt + timedelta(hours=1),
            end_time=end_dt + timedelta(hours=1, minutes=25),
            duration_minutes=25,
            status="COMPLETED",
        )
    )
    session.add(
        RecoveryPeriod(
            user_id=user_id,
            start_time=end_dt + timedelta(hours=1, minutes=25),
            end_time=end_dt + timedelta(hours=1, minutes=30),
            duration_minutes=5,
            source="DETECTED",
            recovery_type="SHORT_BREAK",
        )
    )
    session.add(
        EnergyScore(
            user_id=user_id,
            target_date=target_d,
            status="ok",
            score=Decimal("85.00"),
            energy_level="HIGH",
            confidence=0.9,
            insights={"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "components": [], "insights": []},
            calculated_at=end_dt + timedelta(hours=2),
        )
    )
    session.add(
        EnergyScoreRevision(
            user_id=user_id,
            target_date=target_d,
            status="ok",
            score=Decimal("85.00"),
            energy_level="HIGH",
            confidence=0.9,
            algorithm_version=ALGORITHM_VERSION,
            result={"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "components": [], "insights": []},
            calculated_at=end_dt + timedelta(hours=2),
        )
    )
    session.add(
        EnergyFeedback(
            user_id=user_id,
            target_date=target_d,
            felt_energy=4,
            score_at_feedback=Decimal("85.00"),
            level_at_feedback="HIGH",
            algorithm_version=ALGORITHM_VERSION,
        )
    )
    session.add(
        Task(
            user_id=user_id,
            title="Surviving Task",
            task_type="FLEXIBLE",
            estimated_duration_minutes=45,
            category="STUDY",
            priority=4,
            status="UNSCHEDULED",
        )
    )
    session.add(
        Event(
            user_id=user_id,
            title="Surviving Event",
            category="WORK",
            recurrence_type="ONCE",
            start_at=end_dt + timedelta(hours=3),
            end_at=end_dt + timedelta(hours=4),
        )
    )


# --- 1. Data Erase Tests ---


def test_erase_all_ten_tables_preserves_non_energy_and_other_users_idempotent(energy_api, database, isolated_users):
    user1, user2 = isolated_users
    u1_headers = {"Authorization": "Bearer " + energy_api.signer.token(user1)}

    with Session(database.admin) as session:
        _seed_all_user_data(session, user1, day_offset=0)
        _seed_all_user_data(session, user2, day_offset=1)
        session.commit()

    res = energy_api.client.post(
        "/api/v1/energy/data/erase",
        headers=u1_headers,
        json={"confirm": "DELETE_ENERGY_DATA"},
    )
    assert res.status_code == 200
    expected_counts = {
        "energy_feedback": 1,
        "energy_score_revisions": 1,
        "energy_scores": 1,
        "recovery_periods": 1,
        "focus_sessions": 1,
        "recovery_strategies": 1,
        "sleep_quality_ratings": 1,
        "sleep_usage_signals": 2,
        "sleep_sessions": 1,
        "sleep_schedules": 1,
    }
    assert res.json() == {"deleted": expected_counts}

    models = (
        EnergyFeedback,
        EnergyScoreRevision,
        EnergyScore,
        RecoveryPeriod,
        FocusSession,
        RecoveryStrategy,
        SleepQualityRating,
        SleepUsageSignal,
        SleepSession,
        SleepSchedule,
    )
    with Session(database.admin) as session:
        # User 1 energy tables all emptied
        for model in models:
            assert session.scalar(sa.select(sa.func.count()).select_from(model).where(model.user_id == user1)) == 0

        # User 1 tasks, events, profile, and user_preferences survive untouched
        assert session.scalar(sa.select(sa.func.count()).select_from(Task).where(Task.user_id == user1)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(Event).where(Event.user_id == user1)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(Profile).where(Profile.id == user1)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(UserPreferences).where(UserPreferences.user_id == user1)) == 1

        # User 2 energy and scheduler data completely untouched
        for model in models:
            expected_u2 = 2 if model is SleepUsageSignal else 1
            assert session.scalar(sa.select(sa.func.count()).select_from(model).where(model.user_id == user2)) == expected_u2
        assert session.scalar(sa.select(sa.func.count()).select_from(Task).where(Task.user_id == user2)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(Event).where(Event.user_id == user2)) == 1

    # Idempotent second call returns zero counts for all 10 tables
    res2 = energy_api.client.post(
        "/api/v1/energy/data/erase",
        headers=u1_headers,
        json={"confirm": "DELETE_ENERGY_DATA"},
    )
    assert res2.status_code == 200
    assert res2.json() == {"deleted": {k: 0 for k in expected_counts}}


def test_erase_validation_422_and_unauthenticated_401(energy_api):
    assert energy_api.client.post("/api/v1/energy/data/erase", json={"confirm": "DELETE_ENERGY_DATA"}).status_code == 401

    bad_payloads = [
        {},
        {"confirm": "delete_energy_data"},
        {"confirm": "WRONG"},
        {"confirm": None},
        {"confirm": 123},
        {"confirm": "DELETE_ENERGY_DATA", "extra": True},
        {"confirm": "DELETE_ENERGY_DATA", "user_id": str(uuid4())},
    ]
    for payload in bad_payloads:
        res = energy_api.client.post(
            "/api/v1/energy/data/erase",
            headers=energy_api.headers,
            json=payload,
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "invalid_request"


# --- 2. Retention Purge Tests ---


def test_purge_signals_exact_boundaries_and_user_isolation(database, isolated_users):
    user1, user2 = isolated_users
    now = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    cutoff_14d = now - timedelta(days=14)
    cutoff_48h = now - timedelta(hours=48)

    with Session(database.admin) as session:
        # Sessions for user1 (pass updated_at on INSERT; BEFORE UPDATE trigger overwrites updated_at)
        s_conf_exact = SleepSession(
            user_id=user1,
            sleep_date=date(2026, 11, 25),
            start_time=now - timedelta(days=2, hours=8),
            end_time=now - timedelta(days=2),
            duration_minutes=480,
            timezone="UTC",
            status="confirmed",
            updated_at=cutoff_48h,
        )
        s_conf_old = SleepSession(
            user_id=user1,
            sleep_date=date(2026, 11, 24),
            start_time=now - timedelta(days=3, hours=8),
            end_time=now - timedelta(days=3),
            duration_minutes=480,
            timezone="UTC",
            status="confirmed",
            updated_at=cutoff_48h - timedelta(seconds=1),
        )
        s_rej_exact = SleepSession(
            user_id=user1,
            sleep_date=date(2026, 11, 23),
            start_time=now - timedelta(days=4, hours=8),
            end_time=now - timedelta(days=4),
            duration_minutes=480,
            timezone="UTC",
            status="rejected",
            updated_at=cutoff_48h,
        )
        s_rej_old = SleepSession(
            user_id=user1,
            sleep_date=date(2026, 11, 23),
            start_time=now - timedelta(days=5, hours=8),
            end_time=now - timedelta(days=5),
            duration_minutes=480,
            timezone="UTC",
            status="rejected",
            updated_at=cutoff_48h - timedelta(seconds=1),
        )
        s_pending_old = SleepSession(
            user_id=user1,
            sleep_date=date(2026, 11, 22),
            start_time=now - timedelta(days=6, hours=8),
            end_time=now - timedelta(days=6),
            duration_minutes=480,
            timezone="UTC",
            status="pending_confirmation",
            updated_at=cutoff_48h - timedelta(days=3),
        )
        s_u2_old = SleepSession(
            user_id=user2,
            sleep_date=date(2026, 11, 24),
            start_time=now - timedelta(days=3, hours=8),
            end_time=now - timedelta(days=3),
            duration_minutes=480,
            timezone="UTC",
            status="confirmed",
            updated_at=cutoff_48h - timedelta(days=3),
        )
        session.add_all([s_conf_exact, s_conf_old, s_rej_exact, s_rej_old, s_pending_old, s_u2_old])
        session.flush()

        sig_unlinked_exact = SleepUsageSignal(
            user_id=user1, session_id=None, event_type="SCREEN_INTERACTIVE", event_timestamp=cutoff_14d
        )
        sig_unlinked_expired = SleepUsageSignal(
            user_id=user1,
            session_id=None,
            event_type="SCREEN_INTERACTIVE",
            event_timestamp=cutoff_14d - timedelta(seconds=1),
        )
        sig_conf_exact = SleepUsageSignal(
            user_id=user1,
            session_id=s_conf_exact.id,
            event_type="KEYGUARD_SHOWN",
            event_timestamp=now - timedelta(days=2),
        )
        sig_conf_expired = SleepUsageSignal(
            user_id=user1,
            session_id=s_conf_old.id,
            event_type="KEYGUARD_SHOWN",
            event_timestamp=now - timedelta(days=3),
        )
        sig_rej_exact = SleepUsageSignal(
            user_id=user1,
            session_id=s_rej_exact.id,
            event_type="KEYGUARD_HIDDEN",
            event_timestamp=now - timedelta(days=4),
        )
        sig_rej_expired = SleepUsageSignal(
            user_id=user1,
            session_id=s_rej_old.id,
            event_type="KEYGUARD_HIDDEN",
            event_timestamp=now - timedelta(days=5),
        )
        sig_pending_fresh = SleepUsageSignal(
            user_id=user1,
            session_id=s_pending_old.id,
            event_type="SCREEN_NON_INTERACTIVE",
            event_timestamp=now - timedelta(days=6),
        )
        sig_pending_14d_old = SleepUsageSignal(
            user_id=user1,
            session_id=s_pending_old.id,
            event_type="SCREEN_NON_INTERACTIVE",
            event_timestamp=cutoff_14d - timedelta(seconds=1),
        )
        sig_u2_expired = SleepUsageSignal(
            user_id=user2,
            session_id=s_u2_old.id,
            event_type="SCREEN_INTERACTIVE",
            event_timestamp=cutoff_14d - timedelta(days=5),
        )
        session.add_all([
            sig_unlinked_exact,
            sig_unlinked_expired,
            sig_conf_exact,
            sig_conf_expired,
            sig_rej_exact,
            sig_rej_expired,
            sig_pending_fresh,
            sig_pending_14d_old,
            sig_u2_expired,
        ])
        session.commit()

        keep_ids = {
            sig_unlinked_exact.id,
            sig_conf_exact.id,
            sig_rej_exact.id,
            sig_pending_fresh.id,
        }
        del_ids = {
            sig_unlinked_expired.id,
            sig_conf_expired.id,
            sig_rej_expired.id,
            sig_pending_14d_old.id,
        }
        u2_id = sig_u2_expired.id

    with Session(database.admin) as session:
        deleted_count = retention_service.purge_sleep_usage_signals(session, user1, now=now)
        session.commit()
        assert deleted_count == 4

        remaining_u1 = set(
            session.scalars(sa.select(SleepUsageSignal.id).where(SleepUsageSignal.user_id == user1)).all()
        )
        assert remaining_u1 == keep_ids
        assert remaining_u1.isdisjoint(del_ids)

        # User 2 untouched
        assert session.scalar(sa.select(SleepUsageSignal.id).where(SleepUsageSignal.id == u2_id)) == u2_id


def test_purge_signals_bounded_batch_and_one_delete_sql(database, isolated_users):
    user1, _ = isolated_users
    now = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    assert retention_service.DEFAULT_RETENTION_CONFIG.signal_purge_batch_limit == 20_000

    with Session(database.admin) as session:
        for i in range(7):
            session.add(
                SleepUsageSignal(
                    user_id=user1,
                    session_id=None,
                    event_type="SCREEN_INTERACTIVE",
                    event_timestamp=now - timedelta(days=20, minutes=i),
                )
            )
        session.commit()

    small_cfg = retention_service.RetentionConfig(signal_purge_batch_limit=3)
    stmts: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "sleep_usage_signals" in statement.lower():
            stmts.append(statement)

    sa.event.listen(database.admin, "before_cursor_execute", capture)
    try:
        with Session(database.admin) as session:
            assert retention_service.purge_sleep_usage_signals(session, user1, now=now, config=small_cfg) == 3
            assert retention_service.purge_sleep_usage_signals(session, user1, now=now, config=small_cfg) == 3
            assert retention_service.purge_sleep_usage_signals(session, user1, now=now, config=small_cfg) == 1
            session.commit()
    finally:
        sa.event.remove(database.admin, "before_cursor_execute", capture)

    assert len(stmts) == 3
    for q in stmts:
        q_low = q.lower()
        assert q_low.lstrip().startswith("delete from public.sleep_usage_signals")
        assert "id in (select" in q_low
        assert "limit" in q_low
        assert "sleep_sessions" in q_low


def test_aligned_14_day_windows_and_estimate_works_right_after_purge(database, isolated_users):
    user1, _ = isolated_users
    now = datetime(2026, 11, 27, 10, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        # Sync at exact 14-day boundary -> accepted
        ok_sync = signals_service.sync_screen_signals(
            session,
            user1,
            SignalSyncRequest(
                timezone="UTC",
                screen_events=[
                    ScreenEvent(event_type="SCREEN_INTERACTIVE", timestamp=now - timedelta(days=14)),
                ],
            ),
            now=now,
        )
        assert ok_sync == {"received": 1, "inserted": 1, "duplicates": 0}

        # Sync 1 second past 14-day boundary -> 422
        with pytest.raises(Exception) as err_sync:
            signals_service.sync_screen_signals(
                session,
                user1,
                SignalSyncRequest(
                    timezone="UTC",
                    screen_events=[
                        ScreenEvent(
                            event_type="SCREEN_INTERACTIVE",
                            timestamp=now - timedelta(days=14, seconds=1),
                        ),
                    ],
                ),
                now=now,
            )
        assert err_sync.value.status == 422

        # Estimate target_date 15 days old -> 422
        with pytest.raises(Exception) as err_est:
            signals_service.estimate_sleep_session(
                session,
                user1,
                SleepEstimateRequest(target_date=now.date() - timedelta(days=15), timezone="UTC"),
                now=now,
            )
        assert err_est.value.status == 422

        # Seed expired signal (>14d) AND fresh overnight signals for target_date
        session.add(
            SleepUsageSignal(
                user_id=user1,
                session_id=None,
                event_type="KEYGUARD_SHOWN",
                event_timestamp=now - timedelta(days=20),
            )
        )
        session.add_all([
            SleepUsageSignal(
                user_id=user1,
                session_id=None,
                event_type="SCREEN_NON_INTERACTIVE",
                event_timestamp=datetime(2026, 11, 26, 23, 0, tzinfo=timezone.utc),
            ),
            SleepUsageSignal(
                user_id=user1,
                session_id=None,
                event_type="SCREEN_INTERACTIVE",
                event_timestamp=datetime(2026, 11, 27, 7, 0, tzinfo=timezone.utc),
            ),
        ])
        session.commit()

        # Estimate purges expired signals AND succeeds for last night
        status_code, est_data = signals_service.estimate_sleep_session(
            session,
            user1,
            SleepEstimateRequest(target_date=date(2026, 11, 27), timezone="UTC"),
            now=now,
        )
        session.commit()
        assert status_code == 201
        assert est_data["status"] == "estimated"
        assert est_data["created"] is True
        assert est_data["session"]["duration_minutes"] == 480

        # Expired signals gone; exact-boundary signal + linked session signals remain
        remaining_ts = set(
            session.scalars(
                sa.select(SleepUsageSignal.event_timestamp).where(SleepUsageSignal.user_id == user1)
            ).all()
        )
        assert now - timedelta(days=20) not in remaining_ts
        assert datetime(2026, 11, 26, 23, 0, tzinfo=timezone.utc) in remaining_ts
        assert datetime(2026, 11, 27, 7, 0, tzinfo=timezone.utc) in remaining_ts


def test_purge_revisions_at_calculate_exact_90_day_boundaries(database, isolated_users):
    user1, user2 = isolated_users
    now = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    exact_90d = now - timedelta(days=90)
    expired_90d = now - timedelta(days=90, seconds=1)

    with Session(database.admin) as session:
        rev_keep = EnergyScoreRevision(
            user_id=user1,
            target_date=date(2026, 8, 29),
            status="insufficient_data",
            score=None,
            energy_level=None,
            confidence=None,
            algorithm_version=ALGORITHM_VERSION,
            result={"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "components": [], "insights": []},
            calculated_at=exact_90d,
        )
        rev_drop = EnergyScoreRevision(
            user_id=user1,
            target_date=date(2026, 8, 28),
            status="insufficient_data",
            score=None,
            energy_level=None,
            confidence=None,
            algorithm_version=ALGORITHM_VERSION,
            result={"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "components": [], "insights": []},
            calculated_at=expired_90d,
        )
        rev_u2 = EnergyScoreRevision(
            user_id=user2,
            target_date=date(2026, 8, 1),
            status="insufficient_data",
            score=None,
            energy_level=None,
            confidence=None,
            algorithm_version=ALGORITHM_VERSION,
            result={"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "components": [], "insights": []},
            calculated_at=now - timedelta(days=120),
        )
        session.add_all([rev_keep, rev_drop, rev_u2])
        session.commit()
        keep_id, drop_id, u2_id = rev_keep.id, rev_drop.id, rev_u2.id

    with Session(database.admin) as session:
        calc_res = energy_service.calculate_energy_for_date(
            session, user1, now.date(), "UTC", now=now
        )
        session.commit()
        assert calc_res["target_date"] == now.date()

        u1_ids = set(
            session.scalars(
                sa.select(EnergyScoreRevision.id).where(EnergyScoreRevision.user_id == user1)
            ).all()
        )
        assert keep_id in u1_ids
        assert drop_id not in u1_ids
        assert session.scalar(sa.select(EnergyScoreRevision.id).where(EnergyScoreRevision.id == u2_id)) == u2_id


def test_housekeeping_failure_in_savepoint_never_breaks_requests(energy_api, database, monkeypatch, caplog):
    user_id = energy_api.users[0]
    now = datetime.now(timezone.utc)

    def boom_signals(*args, **kwargs):
        raise RuntimeError("sensitive-signal-payload-should-not-leak")

    def boom_revisions(*args, **kwargs):
        raise ValueError("sensitive-revision-payload-should-not-leak")

    monkeypatch.setattr(retention_service, "purge_sleep_usage_signals", boom_signals)
    monkeypatch.setattr(retention_service, "purge_energy_score_revisions", boom_revisions)

    with caplog.at_level(logging.WARNING):
        res_sync = energy_api.client.post(
            "/api/v1/energy/sleep/signals/sync",
            headers=energy_api.headers,
            json={
                "timezone": "UTC",
                "screen_events": [
                    {"event_type": "SCREEN_NON_INTERACTIVE", "timestamp": (now - timedelta(hours=9)).isoformat()},
                    {"event_type": "SCREEN_INTERACTIVE", "timestamp": (now - timedelta(hours=1)).isoformat()},
                ],
            },
        )
        assert res_sync.status_code == 200
        assert res_sync.json()["inserted"] == 2

        res_est = energy_api.client.post(
            "/api/v1/energy/sleep/estimate",
            headers=energy_api.headers,
            json={"target_date": now.date().isoformat(), "timezone": "UTC"},
        )
        assert res_est.status_code in (200, 201)

        res_calc = energy_api.client.post(
            "/api/v1/energy/calculate",
            headers=energy_api.headers,
            json={"target_date": now.date().isoformat(), "timezone": "UTC"},
        )
        assert res_calc.status_code == 200

    warning_messages = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
    joined = "\n".join(warning_messages)
    assert "RuntimeError" in joined
    assert "ValueError" in joined
    assert "sensitive-signal-payload-should-not-leak" not in joined
    assert "sensitive-revision-payload-should-not-leak" not in joined

    # Also verify SQL-level failure inside the savepoint rolls back without poisoning outer transaction
    def bad_sql(session, *args, **kwargs):
        session.execute(sa.text("SELECT 1 / 0"))
        return 0

    monkeypatch.setattr(retention_service, "purge_sleep_usage_signals", bad_sql)
    res_sync2 = energy_api.client.post(
        "/api/v1/energy/sleep/signals/sync",
        headers=energy_api.headers,
        json={
            "timezone": "UTC",
            "screen_events": [
                {"event_type": "KEYGUARD_SHOWN", "timestamp": (now - timedelta(hours=4)).isoformat()},
            ],
        },
    )
    assert res_sync2.status_code == 200
    assert res_sync2.json()["inserted"] == 1
    with Session(database.admin) as session:
        assert session.scalar(
            sa.select(sa.func.count()).select_from(SleepUsageSignal).where(SleepUsageSignal.user_id == user_id)
        ) == 3


# --- 3. Feedback Tests ---


def test_feedback_create_upsert_update_and_404_rules(energy_api, database, isolated_users):
    user1, user2 = isolated_users
    u1_headers = {"Authorization": "Bearer " + energy_api.signer.token(user1)}
    u2_headers = {"Authorization": "Bearer " + energy_api.signer.token(user2)}
    target_d = date(2026, 11, 20)

    # 1. No energy score stored -> 404
    res_missing = energy_api.client.post(
        "/api/v1/energy/feedback",
        headers=u1_headers,
        json={"target_date": target_d.isoformat(), "felt_energy": 4},
    )
    assert res_missing.status_code == 404
    assert res_missing.json()["error"]["code"] == "not_found"

    # 2. insufficient_data score -> 404
    res_calc_insuf = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=u1_headers,
        json={"target_date": target_d.isoformat(), "timezone": "UTC"},
    )
    assert res_calc_insuf.status_code == 200
    assert res_calc_insuf.json()["status"] == "insufficient_data"

    res_insuf_fb = energy_api.client.post(
        "/api/v1/energy/feedback",
        headers=u1_headers,
        json={"target_date": target_d.isoformat(), "felt_energy": 4},
    )
    assert res_insuf_fb.status_code == 404

    # 3. Seed confirmed sleep -> calculate 'ok' score -> create feedback
    with Session(database.admin) as session:
        sleep_sess = SleepSession(
            user_id=user1,
            sleep_date=target_d,
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
        sess_id = sleep_sess.id

    res_calc_ok = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=u1_headers,
        json={"target_date": target_d.isoformat(), "timezone": "UTC"},
    )
    assert res_calc_ok.status_code == 200
    calc_1 = res_calc_ok.json()
    assert calc_1["status"] == "ok"

    # Cross-user isolation: user2 still gets 404 for user1's scored date
    assert energy_api.client.post(
        "/api/v1/energy/feedback",
        headers=u2_headers,
        json={"target_date": target_d.isoformat(), "felt_energy": 3},
    ).status_code == 404

    res_fb1 = energy_api.client.post(
        "/api/v1/energy/feedback",
        headers=u1_headers,
        json={"target_date": target_d.isoformat(), "felt_energy": 3},
    )
    assert res_fb1.status_code == 200
    fb1 = res_fb1.json()
    assert fb1["target_date"] == target_d.isoformat()
    assert fb1["felt_energy"] == 3
    assert fb1["score_at_feedback"] == calc_1["energy_score"]
    assert fb1["level_at_feedback"] == calc_1["energy_level"]
    assert fb1["algorithm_version"] == ALGORITHM_VERSION

    # 4. Rate sleep poorly and recalculate -> score changes -> upsert feedback updates row
    assert energy_api.client.post(
        f"/api/v1/energy/sleep/sessions/{sess_id}/quality",
        headers=u1_headers,
        json={"rating": 1},
    ).status_code == 201

    res_calc_2 = energy_api.client.post(
        "/api/v1/energy/calculate",
        headers=u1_headers,
        json={"target_date": target_d.isoformat(), "timezone": "UTC"},
    )
    assert res_calc_2.status_code == 200
    calc_2 = res_calc_2.json()
    assert calc_2["energy_score"] != calc_1["energy_score"]

    res_fb2 = energy_api.client.post(
        "/api/v1/energy/feedback",
        headers=u1_headers,
        json={"target_date": target_d.isoformat(), "felt_energy": 5},
    )
    assert res_fb2.status_code == 200
    fb2 = res_fb2.json()
    assert fb2["id"] == fb1["id"]
    assert fb2["felt_energy"] == 5
    assert fb2["score_at_feedback"] == calc_2["energy_score"]
    assert fb2["level_at_feedback"] == calc_2["energy_level"]
    assert fb2["algorithm_version"] == ALGORITHM_VERSION


def test_feedback_validation_bounds_strict_types_and_401(energy_api):
    assert energy_api.client.post("/api/v1/energy/feedback", json={"target_date": "2026-11-20", "felt_energy": 3}).status_code == 401
    assert energy_api.client.get("/api/v1/energy/feedback").status_code == 401

    invalid_bodies = [
        {"target_date": "2026-11-20", "felt_energy": 0},
        {"target_date": "2026-11-20", "felt_energy": 6},
        {"target_date": "2026-11-20", "felt_energy": True},
        {"target_date": "2026-11-20", "felt_energy": False},
        {"target_date": "2026-11-20", "felt_energy": 3.0},
        {"target_date": "2026-11-20", "felt_energy": "4"},
        {"target_date": 12345, "felt_energy": 3},
        {"target_date": True, "felt_energy": 3},
        {"target_date": "2026-11-20", "felt_energy": 3, "user_id": str(uuid4())},
        {"target_date": "2026-11-20", "felt_energy": 3, "extra": 1},
    ]
    for body in invalid_bodies:
        res = energy_api.client.post("/api/v1/energy/feedback", headers=energy_api.headers, json=body)
        assert res.status_code == 422

    res_bad_range = energy_api.client.get(
        "/api/v1/energy/feedback?date_from=2026-11-22&date_to=2026-11-20",
        headers=energy_api.headers,
    )
    assert res_bad_range.status_code == 422


def test_feedback_list_pagination_filters_and_isolation(energy_api, database, isolated_users):
    user1, user2 = isolated_users
    u1_headers = {"Authorization": "Bearer " + energy_api.signer.token(user1)}
    u2_headers = {"Authorization": "Bearer " + energy_api.signer.token(user2)}

    with Session(database.admin) as session:
        for i in range(4):
            session.add(
                EnergyFeedback(
                    user_id=user1,
                    target_date=date(2026, 11, 18 + i),
                    felt_energy=1 + i,
                    score_at_feedback=Decimal("80.00"),
                    level_at_feedback="HIGH",
                    algorithm_version=ALGORITHM_VERSION,
                )
            )
        session.add(
            EnergyFeedback(
                user_id=user2,
                target_date=date(2026, 11, 25),
                felt_energy=5,
                score_at_feedback=Decimal("90.00"),
                level_at_feedback="HIGH",
                algorithm_version=ALGORITHM_VERSION,
            )
        )
        session.commit()

    # Paginated newest first
    res_p1 = energy_api.client.get("/api/v1/energy/feedback?limit=2&offset=0", headers=u1_headers)
    assert res_p1.status_code == 200
    d1 = res_p1.json()
    assert d1["limit"] == 2
    assert d1["offset"] == 0
    assert d1["has_more"] is True
    assert [item["target_date"] for item in d1["items"]] == ["2026-11-21", "2026-11-20"]
    assert d1["items"][0]["level_at_feedback"] == "High"

    res_p2 = energy_api.client.get("/api/v1/energy/feedback?limit=2&offset=2", headers=u1_headers)
    assert res_p2.status_code == 200
    d2 = res_p2.json()
    assert d2["has_more"] is False
    assert [item["target_date"] for item in d2["items"]] == ["2026-11-19", "2026-11-18"]

    # Date filters
    res_filtered = energy_api.client.get(
        "/api/v1/energy/feedback?date_from=2026-11-19&date_to=2026-11-20",
        headers=u1_headers,
    )
    assert res_filtered.status_code == 200
    assert [item["target_date"] for item in res_filtered.json()["items"]] == ["2026-11-20", "2026-11-19"]

    # User 2 isolation
    res_u2 = energy_api.client.get("/api/v1/energy/feedback", headers=u2_headers)
    assert res_u2.status_code == 200
    assert [item["target_date"] for item in res_u2.json()["items"]] == ["2026-11-25"]


# --- 4. Revisions List Tests ---


def test_revisions_list_pagination_insufficient_data_and_isolation(energy_api, database, isolated_users):
    user1, user2 = isolated_users
    u1_headers = {"Authorization": "Bearer " + energy_api.signer.token(user1)}
    u2_headers = {"Authorization": "Bearer " + energy_api.signer.token(user2)}
    target_d = date(2026, 11, 20)

    assert energy_api.client.get("/api/v1/energy/revisions").status_code == 401

    with Session(database.admin) as session:
        session.add_all([
            EnergyScoreRevision(
                user_id=user1,
                target_date=target_d,
                status="insufficient_data",
                score=None,
                energy_level=None,
                confidence=None,
                algorithm_version="energy-algo-1",
                result={"schema_version": 1, "algorithm_version": "energy-algo-1", "components": [], "insights": []},
                calculated_at=datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc),
            ),
            EnergyScoreRevision(
                user_id=user1,
                target_date=target_d,
                status="ok",
                score=Decimal("65.50"),
                energy_level="MEDIUM",
                confidence=0.75,
                algorithm_version="energy-algo-2",
                result={"schema_version": 1, "algorithm_version": "energy-algo-2", "components": [], "insights": []},
                calculated_at=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
            ),
            EnergyScoreRevision(
                user_id=user1,
                target_date=target_d,
                status="ok",
                score=Decimal("88.00"),
                energy_level="HIGH",
                confidence=0.95,
                algorithm_version="energy-algo-2",
                result={"schema_version": 1, "algorithm_version": "energy-algo-2", "components": [], "insights": []},
                calculated_at=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
            ),
            EnergyScoreRevision(
                user_id=user1,
                target_date=date(2026, 11, 19),
                status="ok",
                score=Decimal("35.00"),
                energy_level="LOW",
                confidence=0.60,
                algorithm_version="energy-algo-2",
                result={"schema_version": 1, "algorithm_version": "energy-algo-2", "components": [], "insights": []},
                calculated_at=datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc),
            ),
            EnergyScoreRevision(
                user_id=user2,
                target_date=target_d,
                status="ok",
                score=Decimal("99.00"),
                energy_level="HIGH",
                confidence=1.0,
                algorithm_version="energy-algo-2",
                result={"schema_version": 1, "algorithm_version": "energy-algo-2", "components": [], "insights": []},
                calculated_at=datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc),
            ),
        ])
        session.commit()

    # Filtered by target_date, page 1
    res_p1 = energy_api.client.get(
        f"/api/v1/energy/revisions?target_date={target_d.isoformat()}&limit=2&offset=0",
        headers=u1_headers,
    )
    assert res_p1.status_code == 200
    d1 = res_p1.json()
    assert d1["has_more"] is True
    assert len(d1["items"]) == 2
    assert d1["items"][0]["energy_score"] == 88.0
    assert d1["items"][0]["energy_level"] == "High"
    assert d1["items"][0]["confidence"] == 0.95
    assert d1["items"][0]["algorithm_version"] == "energy-algo-2"
    assert d1["items"][1]["energy_score"] == 65.5
    assert d1["items"][1]["energy_level"] == "Medium"

    # Page 2 -> insufficient_data has null score, level, confidence
    res_p2 = energy_api.client.get(
        f"/api/v1/energy/revisions?target_date={target_d.isoformat()}&limit=2&offset=2",
        headers=u1_headers,
    )
    assert res_p2.status_code == 200
    d2 = res_p2.json()
    assert d2["has_more"] is False
    assert len(d2["items"]) == 1
    assert d2["items"][0]["status"] == "insufficient_data"
    assert d2["items"][0]["energy_score"] is None
    assert d2["items"][0]["energy_level"] is None
    assert d2["items"][0]["confidence"] is None
    assert d2["items"][0]["algorithm_version"] == "energy-algo-1"

    # Optional target_date omitted returns all caller revisions newest-first
    res_all = energy_api.client.get("/api/v1/energy/revisions", headers=u1_headers)
    assert res_all.status_code == 200
    assert len(res_all.json()["items"]) == 4

    # User 2 sees only their own revision
    res_u2 = energy_api.client.get(
        f"/api/v1/energy/revisions?target_date={target_d.isoformat()}",
        headers=u2_headers,
    )
    assert res_u2.status_code == 200
    assert len(res_u2.json()["items"]) == 1
    assert res_u2.json()["items"][0]["energy_score"] == 99.0


# --- 5. Constant Statement Count on List Endpoints ---


def test_list_feedback_and_revisions_constant_statement_count(energy_api, database, isolated_users):
    user1, _ = isolated_users
    u1_headers = {"Authorization": "Bearer " + energy_api.signer.token(user1)}
    target_d = date(2026, 11, 20)

    with Session(database.admin) as session:
        for i in range(50):
            session.add(
                EnergyFeedback(
                    user_id=user1,
                    target_date=date(2026, 9, 1) + timedelta(days=i),
                    felt_energy=(i % 5) + 1,
                    score_at_feedback=Decimal("75.00"),
                    level_at_feedback="HIGH",
                    algorithm_version=ALGORITHM_VERSION,
                )
            )
            session.add(
                EnergyScoreRevision(
                    user_id=user1,
                    target_date=target_d,
                    status="ok",
                    score=Decimal("75.00"),
                    energy_level="HIGH",
                    confidence=0.85,
                    algorithm_version=ALGORITHM_VERSION,
                    result={"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "components": [], "insights": []},
                    calculated_at=datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=i),
                )
            )
        session.commit()

    def count_queries(path: str):
        queries: list[str] = []

        def listener(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        sa.event.listen(energy_api.database.runtime.engine, "before_cursor_execute", listener)
        try:
            res = energy_api.client.get(path, headers=u1_headers)
            assert res.status_code == 200
            return queries, res.json()
        finally:
            sa.event.remove(energy_api.database.runtime.engine, "before_cursor_execute", listener)

    fb_q1, fb_d1 = count_queries("/api/v1/energy/feedback?limit=1")
    fb_q50, fb_d50 = count_queries("/api/v1/energy/feedback?limit=50")
    assert len(fb_d1["items"]) == 1 and fb_d1["has_more"] is True
    assert len(fb_d50["items"]) == 50 and fb_d50["has_more"] is False
    assert len(fb_q1) == len(fb_q50)
    assert sum("from public.energy_feedback" in q.lower() for q in fb_q1) == 1
    assert sum("from public.energy_feedback" in q.lower() for q in fb_q50) == 1
    assert sum("count(" in q.lower() for q in fb_q1) == 0

    rev_q1, rev_d1 = count_queries(f"/api/v1/energy/revisions?target_date={target_d.isoformat()}&limit=1")
    rev_q50, rev_d50 = count_queries(f"/api/v1/energy/revisions?target_date={target_d.isoformat()}&limit=50")
    assert len(rev_d1["items"]) == 1 and rev_d1["has_more"] is True
    assert len(rev_d50["items"]) == 50 and rev_d50["has_more"] is False
    assert len(rev_q1) == len(rev_q50)
    assert sum("from public.energy_score_revisions" in q.lower() for q in rev_q1) == 1
    assert sum("from public.energy_score_revisions" in q.lower() for q in rev_q50) == 1
    assert sum("count(" in q.lower() for q in rev_q1) == 0
