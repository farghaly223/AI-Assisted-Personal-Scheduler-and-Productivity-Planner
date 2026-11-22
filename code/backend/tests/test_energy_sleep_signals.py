"""Tests for Sleep Usage Signals Sync and Sleep Candidate Estimation endpoints."""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from app.modules.energy.models import SleepSchedule, SleepSession, SleepUsageSignal
from app.modules.energy.services import sleep_signals as signals_service


@pytest.fixture
def energy_api(api):
    from app.modules.energy.router import router as energy_router

    if not getattr(api.client.app.state, "_energy_router_included", False):
        api.client.app.include_router(energy_router, prefix="/api/v1")
        api.client.app.state._energy_router_included = True
    return api


def test_signals_sync_happy_path_and_deduplication(energy_api):
    now_utc = datetime.now(timezone.utc)
    ts1 = (now_utc - timedelta(hours=8)).isoformat()
    ts2 = (now_utc - timedelta(hours=2)).isoformat()

    # 1. Happy path sync: 2 events
    payload = {
        "timezone": "UTC",
        "screen_events": [
            {"event_type": "SCREEN_NON_INTERACTIVE", "timestamp": ts1},
            {"event_type": "SCREEN_INTERACTIVE", "timestamp": ts2},
        ],
        "date": "2026-11-20",
    }
    res = energy_api.client.post("/api/v1/energy/sleep/signals/sync", headers=energy_api.headers, json=payload)
    assert res.status_code == 200
    assert res.json() == {"received": 2, "inserted": 2, "duplicates": 0}

    # 2. Re-sending identical payload: 0 inserted, 2 duplicates
    res_repeat = energy_api.client.post("/api/v1/energy/sleep/signals/sync", headers=energy_api.headers, json=payload)
    assert res_repeat.status_code == 200
    assert res_repeat.json() == {"received": 2, "inserted": 0, "duplicates": 2}

    # 3. Intra-payload duplicates (same instant with different offsets or identical)
    # ts3 at UTC, ts3_plus2 with +02:00 representing the exact same UTC instant
    instant_base = now_utc - timedelta(hours=10)
    ts3_utc = instant_base.astimezone(timezone.utc).isoformat()
    ts3_offset = (instant_base + timedelta(hours=2)).replace(
        tzinfo=timezone(timedelta(hours=2))
    ).isoformat()

    payload_dup = {
        "timezone": "UTC",
        "screen_events": [
            {"event_type": "KEYGUARD_SHOWN", "timestamp": ts3_utc},
            {"event_type": "KEYGUARD_SHOWN", "timestamp": ts3_offset},
            {"event_type": "KEYGUARD_HIDDEN", "timestamp": (now_utc - timedelta(hours=5)).isoformat()},
        ],
    }
    res_dup = energy_api.client.post("/api/v1/energy/sleep/signals/sync", headers=energy_api.headers, json=payload_dup)
    assert res_dup.status_code == 200
    # 3 received, 2 unique events in payload, 2 inserted, 1 intra-payload duplicate
    assert res_dup.json() == {"received": 3, "inserted": 2, "duplicates": 1}


def test_signals_sync_validations(energy_api):
    now_utc = datetime.now(timezone.utc)

    # 1. Unknown event_type -> 422
    res_unknown = energy_api.client.post(
        "/api/v1/energy/sleep/signals/sync",
        headers=energy_api.headers,
        json={
            "timezone": "UTC",
            "screen_events": [
                {"event_type": "APP_USAGE", "timestamp": now_utc.isoformat()},
            ],
        },
    )
    assert res_unknown.status_code == 422

    # 2. Empty list -> 422
    res_empty = energy_api.client.post(
        "/api/v1/energy/sleep/signals/sync",
        headers=energy_api.headers,
        json={"timezone": "UTC", "screen_events": []},
    )
    assert res_empty.status_code == 422

    # 3. > 5000 events -> 422
    too_many = [
        {"event_type": "KEYGUARD_SHOWN", "timestamp": (now_utc - timedelta(minutes=i)).isoformat()}
        for i in range(5001)
    ]
    res_overflow = energy_api.client.post(
        "/api/v1/energy/sleep/signals/sync",
        headers=energy_api.headers,
        json={"timezone": "UTC", "screen_events": too_many},
    )
    assert res_overflow.status_code == 422

    # 4. Naive timestamp -> 422
    res_naive = energy_api.client.post(
        "/api/v1/energy/sleep/signals/sync",
        headers=energy_api.headers,
        json={
            "timezone": "UTC",
            "screen_events": [
                {"event_type": "SCREEN_INTERACTIVE", "timestamp": "2026-11-20T10:00:00"},
            ],
        },
    )
    assert res_naive.status_code == 422

    # 5. Future timestamp beyond 5 minutes clock skew -> 422
    future_ts = (now_utc + timedelta(minutes=10)).isoformat()
    res_future = energy_api.client.post(
        "/api/v1/energy/sleep/signals/sync",
        headers=energy_api.headers,
        json={
            "timezone": "UTC",
            "screen_events": [
                {"event_type": "SCREEN_INTERACTIVE", "timestamp": future_ts},
            ],
        },
    )
    assert res_future.status_code == 422

    # 6. Timestamp older than 30 days -> 422
    old_ts = (now_utc - timedelta(days=31)).isoformat()
    res_old = energy_api.client.post(
        "/api/v1/energy/sleep/signals/sync",
        headers=energy_api.headers,
        json={
            "timezone": "UTC",
            "screen_events": [
                {"event_type": "SCREEN_INTERACTIVE", "timestamp": old_ts},
            ],
        },
    )
    assert res_old.status_code == 422


def test_signals_sync_sql_statement_count_is_constant(energy_api):
    """Assert sync issues a constant number of SQL statements for 1 event and for 50 events."""
    now_utc = datetime.now(timezone.utc)

    # 1 event payload
    p1 = {
        "timezone": "UTC",
        "screen_events": [
            {"event_type": "SCREEN_NON_INTERACTIVE", "timestamp": (now_utc - timedelta(hours=15)).isoformat()}
        ],
    }

    # 50 events payload
    p50 = {
        "timezone": "UTC",
        "screen_events": [
            {"event_type": "SCREEN_NON_INTERACTIVE", "timestamp": (now_utc - timedelta(hours=16, minutes=i)).isoformat()}
            for i in range(50)
        ],
    }

    queries_1 = []
    queries_50 = []

    def capture_1(conn, cursor, statement, parameters, context, executemany):
        if "sleep_usage_signals" in statement.lower():
            queries_1.append(statement)

    def capture_50(conn, cursor, statement, parameters, context, executemany):
        if "sleep_usage_signals" in statement.lower():
            queries_50.append(statement)

    # Measure 1 event
    event.listen(energy_api.database.runtime.engine, "before_cursor_execute", capture_1)
    try:
        res1 = energy_api.client.post("/api/v1/energy/sleep/signals/sync", headers=energy_api.headers, json=p1)
        assert res1.status_code == 200
    finally:
        event.remove(energy_api.database.runtime.engine, "before_cursor_execute", capture_1)

    # Measure 50 events
    event.listen(energy_api.database.runtime.engine, "before_cursor_execute", capture_50)
    try:
        res50 = energy_api.client.post("/api/v1/energy/sleep/signals/sync", headers=energy_api.headers, json=p50)
        assert res50.status_code == 200
    finally:
        event.remove(energy_api.database.runtime.engine, "before_cursor_execute", capture_50)

    # Both must issue exactly 1 insert statement for sleep_usage_signals
    assert len(queries_1) == 1
    assert len(queries_50) == 1


def test_estimate_full_night_africa_cairo_and_signal_linking(energy_api):
    # Fixed wake-up date
    target_date_str = "2026-11-20"
    target_date = date(2026, 11, 20)

    # Overnight in Africa/Cairo (UTC+2):
    # Bedtime: 2026-11-19 23:30 local Cairo = 2026-11-19 21:30:00Z
    # Wake time: 2026-11-20 07:30 local Cairo = 2026-11-20 05:30:00Z
    # Duration: 8 hours = 480 minutes
    sync_payload = {
        "timezone": "Africa/Cairo",
        "screen_events": [
            {"event_type": "SCREEN_NON_INTERACTIVE", "timestamp": "2026-11-19T21:30:00Z"},
            {"event_type": "SCREEN_INTERACTIVE", "timestamp": "2026-11-20T05:30:00Z"},
        ],
    }
    # Mock `now` in service to 2026-11-20 08:00:00 Cairo time (UTC 06:00:00)
    fixed_now = datetime(2026, 11, 20, 6, 0, 0, tzinfo=timezone.utc)

    # Sync signals using service with fixed now
    user_id = energy_api.users[0]
    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES
                    (:user_id, 'SCREEN_NON_INTERACTIVE', '2026-11-19 21:30:00+00'),
                    (:user_id, 'SCREEN_INTERACTIVE', '2026-11-20 05:30:00+00')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    # 1. Estimate sleep
    res_est = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date_str, "timezone": "Africa/Cairo"},
    )
    assert res_est.status_code == 201
    data = res_est.json()
    assert data["status"] == "estimated"
    assert data["created"] is True
    session = data["session"]
    assert session is not None
    assert session["status"] == "pending_confirmation"
    assert session["source"] == "android_usage"
    assert session["duration_minutes"] == 480
    assert session["sleep_date"] == target_date_str
    assert 0.0 <= session["confidence"] <= 1.0
    session_id = session["id"]

    # Verify signals are linked to this session
    with energy_api.database.admin.connect() as conn:
        linked_count = conn.scalar(
            sa.select(sa.func.count())
            .select_from(SleepUsageSignal)
            .where(SleepUsageSignal.user_id == user_id, SleepUsageSignal.session_id == session_id)
        )
        assert linked_count == 2

    # 2. Estimate twice -> second call returns same session with created=false
    res_est2 = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date_str, "timezone": "Africa/Cairo"},
    )
    assert res_est2.status_code == 200
    data2 = res_est2.json()
    assert data2["status"] == "existing"
    assert data2["created"] is False
    assert data2["session"]["id"] == session_id


def test_estimate_insufficient_data_when_no_signals(energy_api):
    res = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": "2026-11-21", "timezone": "UTC"},
    )
    assert res.status_code == 200
    assert res.json() == {
        "status": "insufficient_data",
        "created": False,
        "session": None,
    }


def test_estimate_target_date_validation(energy_api):
    local_today = datetime.now(timezone.utc).date()

    # Later than local today + 1 day -> 422
    future_date = (local_today + timedelta(days=2)).isoformat()
    res_future = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": future_date, "timezone": "UTC"},
    )
    assert res_future.status_code == 422

    # Older than 30 days -> 422
    past_date = (local_today - timedelta(days=31)).isoformat()
    res_past = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": past_date, "timezone": "UTC"},
    )
    assert res_past.status_code == 422


def test_schedule_used_when_active_and_ignored_when_inactive(energy_api):
    user_id = energy_api.users[0]
    target_date = "2026-11-22"

    # Insert signals for sleep from 23:00 to 07:00
    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES
                    (:user_id, 'SCREEN_NON_INTERACTIVE', '2026-11-21 23:00:00+00'),
                    (:user_id, 'SCREEN_INTERACTIVE', '2026-11-22 07:00:00+00')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    # 1. Active schedule matching the sleep hours -> increases confidence (overlap boost)
    energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json={
            "bedtime": "23:00",
            "wake_time": "07:00",
            "timezone": "UTC",
            "repeat_days": [0, 1, 2, 3, 4, 5, 6],
            "bedtime_reminder_enabled": True,
            "reminder_minutes_before": 15,
            "wake_alarm_enabled": True,
        },
    )

    res_active = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date, "timezone": "UTC"},
    )
    assert res_active.status_code == 201
    conf_with_sched = res_active.json()["session"]["confidence"]
    active_session_id = res_active.json()["session"]["id"]

    # Delete session and disable schedule
    energy_api.client.delete(f"/api/v1/energy/sleep/sessions/{active_session_id}", headers=energy_api.headers)
    energy_api.client.patch("/api/v1/energy/sleep-schedule/toggle", headers=energy_api.headers, json={"is_active": False})

    # Re-insert signals (since cascade delete removed them or unlinked them)
    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES
                    (:user_id, 'SCREEN_NON_INTERACTIVE', '2026-11-21 23:00:00+00'),
                    (:user_id, 'SCREEN_INTERACTIVE', '2026-11-22 07:00:00+00')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    # Estimate without active schedule -> confidence should be <= 0.75 without schedule
    res_inactive = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date, "timezone": "UTC"},
    )
    assert res_inactive.status_code == 201
    conf_without_sched = res_inactive.json()["session"]["confidence"]
    assert conf_without_sched <= 0.75
    assert conf_with_sched > conf_without_sched


def test_estimate_reject_re_estimate_and_signal_ownership(energy_api):
    """Test lifecycle: estimate -> reject -> estimate again produces new session owning signals."""
    user_id = energy_api.users[0]
    target_date = "2026-11-18"

    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES
                    (:user_id, 'SCREEN_NON_INTERACTIVE', '2026-11-17 23:00:00+00'),
                    (:user_id, 'SCREEN_INTERACTIVE', '2026-11-18 07:00:00+00')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    # 1. First estimate
    res1 = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date, "timezone": "UTC"},
    )
    assert res1.status_code == 201
    s1_id = res1.json()["session"]["id"]

    # 2. Reject first session
    res_rej = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{s1_id}/confirm",
        headers=energy_api.headers,
        json={"status": "rejected"},
    )
    assert res_rej.status_code == 200

    # User adds a device interaction at 06:00, altering candidate interval
    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES (:user_id, 'SCREEN_INTERACTIVE', '2026-11-18 06:00:00+00')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    # 3. Estimate again for the same date -> should create a new session
    res2 = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date, "timezone": "UTC"},
    )
    assert res2.status_code == 201
    s2_id = res2.json()["session"]["id"]
    assert s2_id != s1_id

    # 4. Signals (including from rejected session) now linked to s2_id
    with energy_api.database.admin.connect() as conn:
        linked_to_s2 = conn.scalar(
            sa.select(sa.func.count())
            .select_from(SleepUsageSignal)
            .where(SleepUsageSignal.user_id == user_id, SleepUsageSignal.session_id == s2_id)
        )
        assert linked_to_s2 == 3


def test_signals_not_stolen_from_confirmed_session(energy_api):
    """Signals linked to another non-rejected session of the same user are not stolen."""
    user_id = energy_api.users[0]
    target_date1 = "2026-11-16"

    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES
                    (:user_id, 'SCREEN_NON_INTERACTIVE', '2026-11-15 23:00:00+00'),
                    (:user_id, 'SCREEN_INTERACTIVE', '2026-11-16 07:00:00+00')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    # 1. Estimate and confirm session 1
    res1 = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date1, "timezone": "UTC"},
    )
    assert res1.status_code == 201
    s1_id = res1.json()["session"]["id"]

    energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{s1_id}/confirm",
        headers=energy_api.headers,
        json={"status": "confirmed"},
    )

    # 2. Add second night signals on 2026-11-17
    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES
                    (:user_id, 'SCREEN_NON_INTERACTIVE', '2026-11-16 23:00:00+00'),
                    (:user_id, 'SCREEN_INTERACTIVE', '2026-11-17 07:00:00+00')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    # 3. Estimate session 2
    res2 = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": "2026-11-17", "timezone": "UTC"},
    )
    assert res2.status_code == 201
    s2_id = res2.json()["session"]["id"]

    # Verify session 1 still owns its 2 signals
    with energy_api.database.admin.connect() as conn:
        s1_signals = conn.scalar(
            sa.select(sa.func.count())
            .select_from(SleepUsageSignal)
            .where(SleepUsageSignal.user_id == user_id, SleepUsageSignal.session_id == s1_id)
        )
        s2_signals = conn.scalar(
            sa.select(sa.func.count())
            .select_from(SleepUsageSignal)
            .where(SleepUsageSignal.user_id == user_id, SleepUsageSignal.session_id == s2_id)
        )
        assert s1_signals == 2
        assert s2_signals == 2


def test_delete_session_cascades_to_linked_signals(energy_api):
    user_id = energy_api.users[0]
    target_date = "2026-11-15"

    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES
                    (:user_id, 'SCREEN_NON_INTERACTIVE', '2026-11-14 23:00:00+00'),
                    (:user_id, 'SCREEN_INTERACTIVE', '2026-11-15 07:00:00+00')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    res = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date, "timezone": "UTC"},
    )
    assert res.status_code == 201
    session_id = res.json()["session"]["id"]

    # Delete session -> 204
    res_del = energy_api.client.delete(f"/api/v1/energy/sleep/sessions/{session_id}", headers=energy_api.headers)
    assert res_del.status_code == 204

    # Signals had fk_sleep_usage_signals_session with ON DELETE CASCADE -> signals deleted from table
    with energy_api.database.admin.connect() as conn:
        count = conn.scalar(
            sa.select(sa.func.count())
            .select_from(SleepUsageSignal)
            .where(SleepUsageSignal.user_id == user_id)
        )
        assert count == 0


def test_estimate_race_condition_idempotent_return(energy_api, monkeypatch):
    """Test that when a concurrent estimate creates the session first, IntegrityError is caught and existing session is returned."""
    user_id = energy_api.users[0]
    target_date = "2026-11-14"

    # Insert signals
    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES
                    (:user_id, 'SCREEN_NON_INTERACTIVE', '2026-11-13 23:00:00+00'),
                    (:user_id, 'SCREEN_INTERACTIVE', '2026-11-14 07:00:00+00')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    # 1. First estimate succeeds
    res1 = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date, "timezone": "UTC"},
    )
    assert res1.status_code == 201
    s1_id = res1.json()["session"]["id"]

    # 2. Simulate concurrent race by monkeypatching _check_existing_session so pre-check returns None
    call_count = 0
    orig_check = signals_service._check_existing_session

    def fake_check(session, uid, t_date):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None  # Pre-check fails to see existing session -> triggers DB IntegrityError
        return orig_check(session, uid, t_date)

    monkeypatch.setattr(signals_service, "_check_existing_session", fake_check)

    # Calling estimate again returns 200 with existing session
    res2 = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=energy_api.headers,
        json={"target_date": target_date, "timezone": "UTC"},
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "existing"
    assert res2.json()["created"] is False
    assert res2.json()["session"]["id"] == s1_id


def test_cross_user_isolation_for_signals_and_estimate(energy_api):
    # User 0 has signals
    user0_id = energy_api.users[0]
    target_date = "2026-11-13"

    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_usage_signals (user_id, event_type, event_timestamp)
                VALUES
                    (:user_id, 'SCREEN_NON_INTERACTIVE', '2026-11-12 23:00:00+00'),
                    (:user_id, 'SCREEN_INTERACTIVE', '2026-11-13 07:00:00+00')
            """),
            {"user_id": user0_id},
        )
        conn.commit()

    # User 1 headers
    user1_headers = {"Authorization": "Bearer " + energy_api.signer.token(energy_api.users[1])}

    # User 1 estimates for target_date -> has no signals -> insufficient_data!
    res_u1 = energy_api.client.post(
        "/api/v1/energy/sleep/estimate",
        headers=user1_headers,
        json={"target_date": target_date, "timezone": "UTC"},
    )
    assert res_u1.status_code == 200
    assert res_u1.json()["status"] == "insufficient_data"
    assert res_u1.json()["created"] is False
    assert res_u1.json()["session"] is None



def test_unauthenticated_requests_for_signals_and_estimate(energy_api):
    assert energy_api.client.post("/api/v1/energy/sleep/signals/sync", json={}).status_code == 401
    assert energy_api.client.post("/api/v1/energy/sleep/estimate", json={}).status_code == 401
