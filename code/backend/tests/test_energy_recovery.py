"""Comprehensive tests for Energy module Recovery endpoints."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.energy.models import FocusSession, RecoveryPeriod, RecoveryStrategy
from app.modules.energy.services import recovery as recovery_service


@pytest.fixture
def energy_api(api):
    from app.modules.energy.router import router as energy_router

    if not getattr(api.client.app.state, "_energy_router_included", False):
        api.client.app.include_router(energy_router, prefix="/api/v1")
        api.client.app.state._energy_router_included = True
    return api


# 1. Strategy create, replace, get, 404
def test_strategy_crud_and_404(energy_api):
    # GET with no strategy -> 404
    res_get = energy_api.client.get("/api/v1/energy/recovery/strategy", headers=energy_api.headers)
    assert res_get.status_code == 404

    # PUT create Pomodoro default
    res_put = energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "POMODORO"},
    )
    assert res_put.status_code == 200
    data = res_put.json()
    assert data["strategy_type"] == "POMODORO"
    assert data["work_duration_minutes"] == 25
    assert data["break_duration_minutes"] == 5
    assert data["long_break_duration_minutes"] == 20
    assert data["long_break_after_sessions"] == 4
    assert data["is_active"] is True

    # GET existing strategy
    res_get2 = energy_api.client.get("/api/v1/energy/recovery/strategy", headers=energy_api.headers)
    assert res_get2.status_code == 200
    assert res_get2.json()["id"] == data["id"]

    # PUT replace with CUSTOM
    res_custom = energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={
            "strategy_type": "CUSTOM",
            "work_duration_minutes": 50,
            "break_duration_minutes": 10,
            "long_break_duration_minutes": 30,
            "long_break_after_sessions": 3,
        },
    )
    assert res_custom.status_code == 200
    c_data = res_custom.json()
    assert c_data["id"] == data["id"]  # Upsert safely onto same row
    assert c_data["strategy_type"] == "CUSTOM"
    assert c_data["work_duration_minutes"] == 50
    assert c_data["long_break_after_sessions"] == 3


# 2. Pomodoro default-fill and rejection of other values
def test_pomodoro_values_validation(energy_api):
    # Explicitly providing matching values is accepted
    res_ok = energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={
            "strategy_type": "POMODORO",
            "work_duration_minutes": 25,
            "break_duration_minutes": 5,
            "long_break_duration_minutes": 20,
            "long_break_after_sessions": 4,
        },
    )
    assert res_ok.status_code == 200

    # Providing different value for work_duration_minutes rejected with 422
    res_fail1 = energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "POMODORO", "work_duration_minutes": 30},
    )
    assert res_fail1.status_code == 422

    # Providing different value for break_duration_minutes rejected with 422
    res_fail2 = energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "POMODORO", "break_duration_minutes": 10},
    )
    assert res_fail2.status_code == 422


# 3. CUSTOM missing fields
def test_custom_missing_fields(energy_api):
    res = energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": 50},
    )
    assert res.status_code == 422


# 4. Bounds and strict-int rejection (true/"25"/25.5/0/negative/extra='forbid')
def test_strategy_types_and_bounds_rejection(energy_api):
    # Boolean true instead of int
    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": True, "break_duration_minutes": 5, "long_break_duration_minutes": 20, "long_break_after_sessions": 4},
    ).status_code == 422

    # String "25"
    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": "25", "break_duration_minutes": 5, "long_break_duration_minutes": 20, "long_break_after_sessions": 4},
    ).status_code == 422

    # Float 25.5
    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": 25.5, "break_duration_minutes": 5, "long_break_duration_minutes": 20, "long_break_after_sessions": 4},
    ).status_code == 422

    # Zero
    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": 0, "break_duration_minutes": 5, "long_break_duration_minutes": 20, "long_break_after_sessions": 4},
    ).status_code == 422

    # Negative
    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": -10, "break_duration_minutes": 5, "long_break_duration_minutes": 20, "long_break_after_sessions": 4},
    ).status_code == 422

    # Upper bounds exceeded
    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": 241, "break_duration_minutes": 5, "long_break_duration_minutes": 20, "long_break_after_sessions": 4},
    ).status_code == 422

    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": 50, "break_duration_minutes": 121, "long_break_duration_minutes": 20, "long_break_after_sessions": 4},
    ).status_code == 422

    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": 50, "break_duration_minutes": 10, "long_break_duration_minutes": 241, "long_break_after_sessions": 4},
    ).status_code == 422

    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "CUSTOM", "work_duration_minutes": 50, "break_duration_minutes": 10, "long_break_duration_minutes": 20, "long_break_after_sessions": 13},
    ).status_code == 422

    # extra='forbid'
    assert energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={"strategy_type": "POMODORO", "unallowed": 1},
    ).status_code == 422


# 5 & 6. Start focus session and second start 409
def test_start_focus_session_and_second_start_409(energy_api):
    res1 = energy_api.client.post("/api/v1/energy/recovery/sessions/start", headers=energy_api.headers, json={})
    assert res1.status_code == 201
    s1 = res1.json()
    assert s1["status"] == "IN_PROGRESS"
    assert s1["end_time"] is None
    assert s1["duration_minutes"] is None

    # Second start while first is IN_PROGRESS -> 409
    res2 = energy_api.client.post("/api/v1/energy/recovery/sessions/start", headers=energy_api.headers, json={})
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "conflict"


# 7. Start with stale / future started_at 422
def test_start_future_and_stale_validation(energy_api):
    now_utc = datetime.now(timezone.utc)

    # 10 minutes in future (> 5m skew) -> 422
    future_ts = (now_utc + timedelta(minutes=10)).isoformat()
    res_fut = energy_api.client.post(
        "/api/v1/energy/recovery/sessions/start",
        headers=energy_api.headers,
        json={"started_at": future_ts},
    )
    assert res_fut.status_code == 422

    # 25 hours in past (> 24h) -> 422
    past_ts = (now_utc - timedelta(hours=25)).isoformat()
    res_past = energy_api.client.post(
        "/api/v1/energy/recovery/sessions/start",
        headers=energy_api.headers,
        json={"started_at": past_ts},
    )
    assert res_past.status_code == 422

    # Naive timestamp -> 422
    res_naive = energy_api.client.post(
        "/api/v1/energy/recovery/sessions/start",
        headers=energy_api.headers,
        json={"started_at": "2026-11-22T10:00:00"},
    )
    assert res_naive.status_code == 422


# 8. Finish COMPLETED, CANCELLED, ABANDONED with duration rounding
def test_finish_status_and_duration_rounding(energy_api):
    now_utc = datetime.now(timezone.utc)

    # 1. COMPLETED: 1h 0m 29s -> 60 minutes
    start1 = now_utc - timedelta(hours=5)
    res1 = energy_api.client.post(
        "/api/v1/energy/recovery/sessions/start",
        headers=energy_api.headers,
        json={"started_at": start1.isoformat()},
    )
    assert res1.status_code == 201
    s1_id = res1.json()["id"]

    end1 = start1 + timedelta(hours=1, seconds=29)
    res_f1 = energy_api.client.post(
        f"/api/v1/energy/recovery/sessions/{s1_id}/finish",
        headers=energy_api.headers,
        json={"status": "COMPLETED", "ended_at": end1.isoformat()},
    )
    assert res_f1.status_code == 200
    d1 = res_f1.json()
    assert d1["status"] == "COMPLETED"
    assert d1["duration_minutes"] == 60

    # 2. CANCELLED: 1h 0m 30s -> 61 minutes (ROUND_HALF_UP)
    start2 = now_utc - timedelta(hours=3)
    res2 = energy_api.client.post(
        "/api/v1/energy/recovery/sessions/start",
        headers=energy_api.headers,
        json={"started_at": start2.isoformat()},
    )
    assert res2.status_code == 201
    s2_id = res2.json()["id"]

    end2 = start2 + timedelta(hours=1, seconds=30)
    res_f2 = energy_api.client.post(
        f"/api/v1/energy/recovery/sessions/{s2_id}/finish",
        headers=energy_api.headers,
        json={"status": "CANCELLED", "ended_at": end2.isoformat()},
    )
    assert res_f2.status_code == 200
    assert res_f2.json()["status"] == "CANCELLED"
    assert res_f2.json()["duration_minutes"] == 61

    # 3. ABANDONED
    start3 = now_utc - timedelta(hours=1)
    res3 = energy_api.client.post(
        "/api/v1/energy/recovery/sessions/start",
        headers=energy_api.headers,
        json={"started_at": start3.isoformat()},
    )
    assert res3.status_code == 201
    s3_id = res3.json()["id"]

    end3 = start3 + timedelta(minutes=15)
    res_f3 = energy_api.client.post(
        f"/api/v1/energy/recovery/sessions/{s3_id}/finish",
        headers=energy_api.headers,
        json={"status": "ABANDONED", "ended_at": end3.isoformat()},
    )
    assert res_f3.status_code == 200
    assert res_f3.json()["status"] == "ABANDONED"
    assert res_f3.json()["duration_minutes"] == 15


# 9, 10, & 11. Finish twice 409, invalid ended_at, and CAS stale-read test
def test_finish_twice_and_invalid_ended_at_and_cas(energy_api):
    now_utc = datetime.now(timezone.utc)
    start = now_utc - timedelta(hours=2)
    res = energy_api.client.post(
        "/api/v1/energy/recovery/sessions/start",
        headers=energy_api.headers,
        json={"started_at": start.isoformat()},
    )
    assert res.status_code == 201
    s_id = res.json()["id"]

    # ended_at <= start -> 422
    assert energy_api.client.post(
        f"/api/v1/energy/recovery/sessions/{s_id}/finish",
        headers=energy_api.headers,
        json={"status": "COMPLETED", "ended_at": start.isoformat()},
    ).status_code == 422

    # > 24 hours -> 422
    too_long = (start + timedelta(hours=24, minutes=1)).isoformat()
    assert energy_api.client.post(
        f"/api/v1/energy/recovery/sessions/{s_id}/finish",
        headers=energy_api.headers,
        json={"status": "COMPLETED", "ended_at": too_long},
    ).status_code == 422

    # Finish successfully first time
    end1 = (start + timedelta(minutes=25)).isoformat()
    res_first = energy_api.client.post(
        f"/api/v1/energy/recovery/sessions/{s_id}/finish",
        headers=energy_api.headers,
        json={"status": "COMPLETED", "ended_at": end1},
    )
    assert res_first.status_code == 200
    assert res_first.json()["status"] == "COMPLETED"

    # Finish second time (CAS stale read) -> 409
    end2 = (start + timedelta(minutes=50)).isoformat()
    res_second = energy_api.client.post(
        f"/api/v1/energy/recovery/sessions/{s_id}/finish",
        headers=energy_api.headers,
        json={"status": "CANCELLED", "ended_at": end2},
    )
    assert res_second.status_code == 409

    # Verify first finish was NOT overwritten
    res_get = energy_api.client.get("/api/v1/energy/recovery/sessions", headers=energy_api.headers)
    found = next(item for item in res_get.json()["items"] if item["id"] == s_id)
    assert found["status"] == "COMPLETED"
    assert found["duration_minutes"] == 25


# 12. Lists with filters, pagination, ordering, day boundary in Africa/Cairo, and constant SQL count
def test_sessions_and_periods_lists_and_constant_sql_count(energy_api):
    user_id = energy_api.users[0]

    # Create focus session at 2026-11-21 22:30:00 UTC
    # In Africa/Cairo (UTC+2), this is 2026-11-22 00:30:00!
    ts_boundary = datetime(2026, 11, 21, 22, 30, 0, tzinfo=timezone.utc)
    with Session(energy_api.database.admin) as s:
        sess = FocusSession(
            user_id=user_id,
            start_time=ts_boundary,
            end_time=ts_boundary + timedelta(minutes=25),
            duration_minutes=25,
            status="COMPLETED",
        )
        s.add(sess)
        s.commit()
        boundary_id = str(sess.id)

    # In Cairo, day 2026-11-22 includes this session!
    res_cairo_in = energy_api.client.get(
        "/api/v1/energy/recovery/sessions?date_from=2026-11-22&date_to=2026-11-22&timezone=Africa/Cairo",
        headers=energy_api.headers,
    )
    assert res_cairo_in.status_code == 200
    ids_cairo = [x["id"] for x in res_cairo_in.json()["items"]]
    assert boundary_id in ids_cairo

    # But for day 2026-11-21 in Cairo, it is excluded!
    res_cairo_ex = energy_api.client.get(
        "/api/v1/energy/recovery/sessions?date_from=2026-11-21&date_to=2026-11-21&timezone=Africa/Cairo",
        headers=energy_api.headers,
    )
    assert res_cairo_ex.status_code == 200
    ids_ex = [x["id"] for x in res_cairo_ex.json()["items"]]
    assert boundary_id not in ids_ex

    # Constant SQL statement count test: limit=1 vs limit=50 must issue the exact same count
    statements_1 = []
    statements_50 = []

    def collect_1(conn, cursor, statement, parameters, context, executemany):
        statements_1.append(statement)

    def collect_50(conn, cursor, statement, parameters, context, executemany):
        statements_50.append(statement)

    sa.event.listen(energy_api.database.runtime.engine, "before_cursor_execute", collect_1)
    try:
        energy_api.client.get("/api/v1/energy/recovery/sessions?limit=1", headers=energy_api.headers)
    finally:
        sa.event.remove(energy_api.database.runtime.engine, "before_cursor_execute", collect_1)

    sa.event.listen(energy_api.database.runtime.engine, "before_cursor_execute", collect_50)
    try:
        energy_api.client.get("/api/v1/energy/recovery/sessions?limit=50", headers=energy_api.headers)
    finally:
        sa.event.remove(energy_api.database.runtime.engine, "before_cursor_execute", collect_50)

    assert len(statements_50) == len(statements_1)
    assert sum("FROM public.focus_sessions" in query for query in statements_1) == 1
    assert sum("FROM public.focus_sessions" in query for query in statements_50) == 1


# 13, 14, 15. Detect breaks, produce LONG_BREAK, and detect idempotency
def test_detect_breaks_long_break_and_idempotency(energy_api):
    # Strategy with long break after 2 sessions:
    # Session 1 (25m), Break 1 (short 5m), Session 2 (25m), Break 2 (long 20m), Session 3 (25m)
    energy_api.client.put(
        "/api/v1/energy/recovery/strategy",
        headers=energy_api.headers,
        json={
            "strategy_type": "CUSTOM",
            "work_duration_minutes": 25,
            "break_duration_minutes": 5,
            "long_break_duration_minutes": 20,
            "long_break_after_sessions": 2,
        },
    )

    day = date(2026, 11, 15)
    user_id = energy_api.users[0]

    with Session(energy_api.database.admin) as s:
        # Session 1: 08:00 - 08:25
        s.add(FocusSession(user_id=user_id, start_time=datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc), end_time=datetime(2026, 11, 15, 8, 25, tzinfo=timezone.utc), duration_minutes=25, status="COMPLETED"))
        # Gap 1: 08:25 - 08:30 (5m -> TAKEN, SHORT_BREAK)
        # Session 2: 08:30 - 08:55
        s.add(FocusSession(user_id=user_id, start_time=datetime(2026, 11, 15, 8, 30, tzinfo=timezone.utc), end_time=datetime(2026, 11, 15, 8, 55, tzinfo=timezone.utc), duration_minutes=25, status="COMPLETED"))
        # Gap 2: 08:55 - 09:15 (20m -> TAKEN, matching block is LONG_BREAK!)
        # Session 3: 09:15 - 09:40
        s.add(FocusSession(user_id=user_id, start_time=datetime(2026, 11, 15, 9, 15, tzinfo=timezone.utc), end_time=datetime(2026, 11, 15, 9, 40, tzinfo=timezone.utc), duration_minutes=25, status="COMPLETED"))
        
        # Add a MANUAL period on that same day to verify it is NEVER touched
        manual_p = RecoveryPeriod(
            user_id=user_id,
            start_time=datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 11, 15, 12, 15, 0, tzinfo=timezone.utc),
            duration_minutes=15,
            source="MANUAL",
            recovery_type="REST",
        )
        s.add(manual_p)
        s.commit()
        manual_id = manual_p.id

    # Run detect 1st time
    res_det1 = energy_api.client.post(
        "/api/v1/energy/recovery/detect",
        headers=energy_api.headers,
        json={"date": day.isoformat(), "timezone": "UTC"},
    )
    assert res_det1.status_code == 200
    d1 = res_det1.json()
    assert d1["data_status"] == "OBSERVED"
    assert len(d1["breaks"]) == 2
    assert d1["breaks"][0]["planned_break_kind"] == "SHORT_BREAK"
    assert d1["breaks"][1]["planned_break_kind"] == "LONG_BREAK"
    assert d1["score"]["status"] == "ok"

    # Verify DETECTED periods in DB: one SHORT_BREAK, one LONG_BREAK
    res_p = energy_api.client.get(
        f"/api/v1/energy/recovery/periods?date_from={day.isoformat()}&date_to={day.isoformat()}&timezone=UTC",
        headers=energy_api.headers,
    )
    items = res_p.json()["items"]
    detected_items = [x for x in items if x["source"] == "DETECTED"]
    assert len(detected_items) == 2
    types = {x["recovery_type"] for x in detected_items}
    assert "SHORT_BREAK" in types
    assert "LONG_BREAK" in types

    # Run detect 2nd time (Idempotency)
    res_det2 = energy_api.client.post(
        "/api/v1/energy/recovery/detect",
        headers=energy_api.headers,
        json={"date": day.isoformat(), "timezone": "UTC"},
    )
    assert res_det2.status_code == 200

    # Ensure no duplicates created and MANUAL row is intact
    res_p2 = energy_api.client.get(
        f"/api/v1/energy/recovery/periods?date_from={day.isoformat()}&date_to={day.isoformat()}&timezone=UTC",
        headers=energy_api.headers,
    )
    items2 = res_p2.json()["items"]
    detected2 = [x for x in items2 if x["source"] == "DETECTED"]
    manual2 = [x for x in items2 if x["source"] == "MANUAL"]
    assert len(detected2) == 2
    assert len(manual2) == 1
    assert manual2[0]["id"] == str(manual_id)


# 16. Detect with no strategy 409
def test_detect_no_strategy_409(energy_api):
    user1_headers = {"Authorization": "Bearer " + energy_api.signer.token(energy_api.users[1])}
    res = energy_api.client.post(
        "/api/v1/energy/recovery/detect",
        headers=user1_headers,
        json={"date": "2026-11-22", "timezone": "UTC"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "recovery_strategy_not_configured"


# 17. Detect with < 2 completed sessions -> UNKNOWN, insufficient_data, nothing written
def test_detect_insufficient_sessions_unknown(energy_api):
    energy_api.client.put("/api/v1/energy/recovery/strategy", headers=energy_api.headers, json={"strategy_type": "POMODORO"})

    target_day = date(2026, 11, 10)
    user_id = energy_api.users[0]
    with Session(energy_api.database.admin) as s:
        s.add(
            FocusSession(
                user_id=user_id,
                start_time=datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 10, 9, 25, tzinfo=timezone.utc),
                duration_minutes=25,
                status="COMPLETED",
            )
        )
        s.commit()

    res = energy_api.client.post(
        "/api/v1/energy/recovery/detect",
        headers=energy_api.headers,
        json={"date": target_day.isoformat(), "timezone": "UTC"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["data_status"] == "UNKNOWN"
    assert data["score"]["status"] == "insufficient_data"
    assert data["score"]["score"] is None
    assert len(data["breaks"]) == 0

    # Ensure nothing written
    res_p = energy_api.client.get(
        f"/api/v1/energy/recovery/periods?date_from={target_day.isoformat()}&date_to={target_day.isoformat()}&timezone=UTC",
        headers=energy_api.headers,
    )
    assert len(res_p.json()["items"]) == 0


# 18. Truncated when > 24 sessions
def test_detect_truncated_sessions(energy_api):
    energy_api.client.put("/api/v1/energy/recovery/strategy", headers=energy_api.headers, json={"strategy_type": "POMODORO"})
    trunc_day = date(2026, 11, 5)

    # Insert 25 sessions directly into DB
    user_id = energy_api.users[0]
    with Session(energy_api.database.admin) as s:
        for i in range(25):
            st = datetime(2026, 11, 5, 0, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=i * 30)
            en = st + timedelta(minutes=25)
            s.add(
                FocusSession(
                    user_id=user_id,
                    start_time=st,
                    end_time=en,
                    duration_minutes=25,
                    status="COMPLETED",
                )
            )
        s.commit()

    res = energy_api.client.post(
        "/api/v1/energy/recovery/detect",
        headers=energy_api.headers,
        json={"date": trunc_day.isoformat(), "timezone": "UTC"},
    )
    assert res.status_code == 200
    assert res.json()["truncated"] is True


# 19. Race path IntegrityError mapping for single-IN_PROGRESS rule
def test_race_path_integrity_error_mapping(energy_api, monkeypatch):
    # Start first session
    res1 = energy_api.client.post("/api/v1/energy/recovery/sessions/start", headers=energy_api.headers, json={})
    assert res1.status_code == 201

    # Bypass service-level pre-check to trigger database unique index uq_focus_sessions_user_in_progress
    monkeypatch.setattr(recovery_service, "_precheck_in_progress", lambda *args, **kwargs: None)

    # Second start hits DB constraint and maps to 409
    res2 = energy_api.client.post("/api/v1/energy/recovery/sessions/start", headers=energy_api.headers, json={})
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "conflict"


# 20. Concurrent detect calls serialized with SELECT ... FOR UPDATE (Amendment 1)
def test_concurrent_detect_calls_no_duplicates(energy_api):
    energy_api.client.put("/api/v1/energy/recovery/strategy", headers=energy_api.headers, json={"strategy_type": "POMODORO"})
    c_day = date(2026, 11, 12)

    # Insert 2 completed sessions
    user_id = energy_api.users[0]
    with Session(energy_api.database.admin) as s:
        s.add(
            FocusSession(
                user_id=user_id,
                start_time=datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 12, 10, 25, tzinfo=timezone.utc),
                duration_minutes=25,
                status="COMPLETED",
            )
        )
        s.add(
            FocusSession(
                user_id=user_id,
                start_time=datetime(2026, 11, 12, 10, 30, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 12, 10, 55, tzinfo=timezone.utc),
                duration_minutes=25,
                status="COMPLETED",
            )
        )
        s.commit()

    def run_detect():
        return energy_api.client.post(
            "/api/v1/energy/recovery/detect",
            headers=energy_api.headers,
            json={"date": c_day.isoformat(), "timezone": "UTC"},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(run_detect)
        f2 = executor.submit(run_detect)
        r1 = f1.result()
        r2 = f2.result()

    assert r1.status_code == 200
    assert r2.status_code == 200

    # Verify no duplicate periods in DB
    res_p = energy_api.client.get(
        f"/api/v1/energy/recovery/periods?date_from={c_day.isoformat()}&date_to={c_day.isoformat()}&timezone=UTC",
        headers=energy_api.headers,
    )
    detected_items = [x for x in res_p.json()["items"] if x["source"] == "DETECTED"]
    assert len(detected_items) == 1  # Exactly 1 break between the 2 sessions


# 21. Summary numbers
def test_recovery_summary(energy_api):
    sum_day = date(2026, 11, 18)
    user_id = energy_api.users[0]

    with Session(energy_api.database.admin) as s:
        s.add(
            FocusSession(
                user_id=user_id,
                start_time=datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 18, 9, 25, tzinfo=timezone.utc),
                duration_minutes=25,
                status="COMPLETED",
            )
        )
        s.add(
            FocusSession(
                user_id=user_id,
                start_time=datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 18, 10, 25, tzinfo=timezone.utc),
                duration_minutes=25,
                status="COMPLETED",
            )
        )
        s.add(
            RecoveryPeriod(
                user_id=user_id,
                start_time=datetime(2026, 11, 18, 9, 25, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc),
                duration_minutes=5,
                source="DETECTED",
                recovery_type="SHORT_BREAK",
            )
        )
        s.commit()

    res = energy_api.client.get(
        f"/api/v1/energy/recovery/summary?date={sum_day.isoformat()}&timezone=UTC",
        headers=energy_api.headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["focus_sessions_completed"] == 2
    assert data["total_focus_minutes"] == 50
    assert data["in_progress"] is False
    assert data["total_recovery_minutes"] == 5
    assert data["periods_count"] == 1
    # Check all four sources present with 0 defaults (Amendment 5)
    by_src = data["recovery_minutes_by_source"]
    assert by_src["DETECTED"] == 5
    assert by_src["FOCUS_TIMER"] == 0
    assert by_src["MANUAL"] == 0
    assert by_src["PLANNED"] == 0


def test_get_focus_summary_interface(energy_api):
    """Internal interface get_focus_summary is read-only, owner-filtered, and DST-safe."""
    user0 = energy_api.users[0]
    user1 = energy_api.users[1]
    day = date(2026, 11, 22)

    with Session(energy_api.database.admin) as s:
        # 2026-11-21 22:30 UTC == 2026-11-22 00:30 Africa/Cairo -> included on 2026-11-22 in Cairo
        s.add(
            FocusSession(
                user_id=user0,
                start_time=datetime(2026, 11, 21, 22, 30, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 21, 22, 55, tzinfo=timezone.utc),
                duration_minutes=25,
                status="COMPLETED",
            )
        )
        # In-progress session for user0 on 2026-11-22 Cairo
        s.add(
            FocusSession(
                user_id=user0,
                start_time=datetime(2026, 11, 22, 8, 0, tzinfo=timezone.utc),
                end_time=None,
                duration_minutes=None,
                status="IN_PROGRESS",
            )
        )
        # Other user's completed session must NOT be counted for user0
        s.add(
            FocusSession(
                user_id=user1,
                start_time=datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 22, 9, 50, tzinfo=timezone.utc),
                duration_minutes=50,
                status="COMPLETED",
            )
        )
        s.commit()

        summary_u0 = recovery_service.get_focus_summary(
            s, user_id=user0, date=day, timezone="Africa/Cairo"
        )
        assert summary_u0 == {
            "completed_sessions": 1,
            "total_focus_minutes": 25,
            "in_progress": True,
        }

        summary_u1 = recovery_service.get_focus_summary(s, user1, day, "Africa/Cairo")
        assert summary_u1 == {
            "completed_sessions": 1,
            "total_focus_minutes": 50,
            "in_progress": False,
        }


# 22. DST transition tests in Africa/Cairo (2026 transition dates: 23h and 25h days) & invalid timezone 422
def test_africa_cairo_dst_transitions(energy_api):
    # Invalid timezone query param returns 422
    res_invalid_tz = energy_api.client.get(
        "/api/v1/energy/recovery/sessions?timezone=Invalid/Zone",
        headers=energy_api.headers,
    )
    assert res_invalid_tz.status_code == 422

    # 1. Spring forward: 2026-04-24 in Africa/Cairo is a 23-hour day
    bounds_23 = recovery_service.get_local_day_bounds(date(2026, 4, 24), "Africa/Cairo")
    assert (bounds_23[1] - bounds_23[0]).total_seconds() / 3600 == 23.0

    # 2. Fall back: 2026-10-29 in Africa/Cairo is a 25-hour day
    bounds_25 = recovery_service.get_local_day_bounds(date(2026, 10, 29), "Africa/Cairo")
    assert (bounds_25[1] - bounds_25[0]).total_seconds() / 3600 == 25.0

    # Test summary on 23-hour DST day
    res_sum_23 = energy_api.client.get(
        "/api/v1/energy/recovery/summary?date=2026-04-24&timezone=Africa/Cairo",
        headers=energy_api.headers,
    )
    assert res_sum_23.status_code == 200

    # Test detect on 25-hour DST day
    energy_api.client.put("/api/v1/energy/recovery/strategy", headers=energy_api.headers, json={"strategy_type": "POMODORO"})
    res_det_25 = energy_api.client.post(
        "/api/v1/energy/recovery/detect",
        headers=energy_api.headers,
        json={"date": "2026-10-29", "timezone": "Africa/Cairo"},
    )
    assert res_det_25.status_code == 200


# 23. Cross-user isolation on every endpoint
def test_cross_user_isolation(energy_api):
    user0_headers = energy_api.headers
    user1_headers = {"Authorization": "Bearer " + energy_api.signer.token(energy_api.users[1])}

    # User 0 sets strategy
    energy_api.client.put("/api/v1/energy/recovery/strategy", headers=user0_headers, json={"strategy_type": "POMODORO"})

    # User 1 cannot see User 0's strategy
    assert energy_api.client.get("/api/v1/energy/recovery/strategy", headers=user1_headers).status_code == 404

    # User 0 starts a session
    res_s = energy_api.client.post("/api/v1/energy/recovery/sessions/start", headers=user0_headers, json={})
    assert res_s.status_code == 201
    u0_session_id = res_s.json()["id"]

    # User 1 cannot finish User 0's session -> 404
    assert energy_api.client.post(
        f"/api/v1/energy/recovery/sessions/{u0_session_id}/finish",
        headers=user1_headers,
        json={"status": "COMPLETED"},
    ).status_code == 404

    # User 1's list shows 0 sessions
    res_list1 = energy_api.client.get("/api/v1/energy/recovery/sessions", headers=user1_headers)
    assert len(res_list1.json()["items"]) == 0

    # User 1 can start their own session concurrently without conflict
    res_u1_s = energy_api.client.post("/api/v1/energy/recovery/sessions/start", headers=user1_headers, json={})
    assert res_u1_s.status_code == 201


# 24. Unauthenticated 401
def test_unauthenticated_requests_401(energy_api):
    assert energy_api.client.get("/api/v1/energy/recovery/strategy").status_code == 401
    assert energy_api.client.put("/api/v1/energy/recovery/strategy", json={"strategy_type": "POMODORO"}).status_code == 401
    assert energy_api.client.post("/api/v1/energy/recovery/sessions/start").status_code == 401
    assert energy_api.client.get("/api/v1/energy/recovery/sessions").status_code == 401
    assert energy_api.client.get("/api/v1/energy/recovery/periods").status_code == 401
    assert energy_api.client.post("/api/v1/energy/recovery/detect", json={"date": "2026-11-22", "timezone": "UTC"}).status_code == 401
    assert energy_api.client.get("/api/v1/energy/recovery/summary?date=2026-11-22&timezone=UTC").status_code == 401
