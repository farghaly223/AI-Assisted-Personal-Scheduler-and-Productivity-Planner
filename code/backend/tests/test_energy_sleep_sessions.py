"""Comprehensive tests for Energy module Sleep Sessions and Sleep Quality endpoints."""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.modules.energy.models import SleepQualityRating, SleepSession
from app.modules.energy.services import sleep_sessions as sleep_service


@pytest.fixture
def energy_api(api):
    from app.modules.energy.router import router as energy_router

    if not getattr(api.client.app.state, "_energy_router_included", False):
        api.client.app.include_router(energy_router, prefix="/api/v1")
        api.client.app.state._energy_router_included = True
    return api


def test_manual_create_and_duration_rounding(energy_api):
    # Session 1: 1h 0m 29s -> 60 minutes
    s1_start = "2026-11-20T22:00:00Z"
    s1_end = "2026-11-20T23:00:29Z"
    res1 = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={"start_time": s1_start, "end_time": s1_end, "timezone": "UTC"},
    )
    assert res1.status_code == 201
    d1 = res1.json()
    assert d1["duration_minutes"] == 60
    assert d1["source"] == "manual"
    assert d1["status"] == "confirmed"
    assert d1["confidence"] == 1.0
    assert d1["quality_rating"] is None
    assert d1["quality_score"] is None
    assert d1["sleep_date"] == "2026-11-20"

    # Session 2: 1h 0m 30s -> 61 minutes (ROUND_HALF_UP)
    s2_start = "2026-11-21T22:00:00Z"
    s2_end = "2026-11-21T23:00:30Z"
    res2 = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={"start_time": s2_start, "end_time": s2_end, "timezone": "UTC"},
    )
    assert res2.status_code == 201
    d2 = res2.json()
    assert d2["duration_minutes"] == 61
    assert d2["sleep_date"] == "2026-11-21"


def test_overnight_session_and_local_sleep_date(energy_api):
    # UTC 21:00 on 2026-11-20 = 23:00 local Cairo on 2026-11-20
    # UTC 05:00 on 2026-11-21 = 07:00 local Cairo on 2026-11-21
    res = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-20T21:00:00Z",
            "end_time": "2026-11-21T05:00:00Z",
            "timezone": "Africa/Cairo",
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["sleep_date"] == "2026-11-21"
    assert data["duration_minutes"] == 480


def test_duplicate_interval_409(energy_api):
    payload = {
        "start_time": "2026-11-20T22:00:00Z",
        "end_time": "2026-11-21T06:00:00Z",
        "timezone": "UTC",
    }
    res1 = energy_api.client.post("/api/v1/energy/sleep/sessions", headers=energy_api.headers, json=payload)
    assert res1.status_code == 201

    res2 = energy_api.client.post("/api/v1/energy/sleep/sessions", headers=energy_api.headers, json=payload)
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "conflict"


def test_duplicate_sleep_date_409(energy_api):
    res1 = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-20T22:00:00Z",
            "end_time": "2026-11-21T06:00:00Z",
            "timezone": "UTC",
        },
    )
    assert res1.status_code == 201
    assert res1.json()["sleep_date"] == "2026-11-21"

    # Different interval on the same sleep_date
    res2 = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-21T07:00:00Z",
            "end_time": "2026-11-21T09:00:00Z",
            "timezone": "UTC",
        },
    )
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "conflict"


def test_invalid_session_inputs(energy_api):
    # 1. Naive datetimes rejected
    res = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-20T22:00:00",
            "end_time": "2026-11-21T06:00:00",
            "timezone": "UTC",
        },
    )
    assert res.status_code == 422

    # 2. end <= start rejected
    res_end = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-21T06:00:00Z",
            "end_time": "2026-11-21T06:00:00Z",
            "timezone": "UTC",
        },
    )
    assert res_end.status_code == 422

    # 3. Too short (< 30 min)
    res_short = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-21T06:00:00Z",
            "end_time": "2026-11-21T06:29:00Z",
            "timezone": "UTC",
        },
    )
    assert res_short.status_code == 422

    # 4. Too long (> 24 hours)
    res_long = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-21T06:00:00Z",
            "end_time": "2026-11-22T06:01:00Z",
            "timezone": "UTC",
        },
    )
    assert res_long.status_code == 422

    # 5. Bad timezone
    for bad_tz in ("Invalid/TZ", " Africa/Cairo", "Africa/Cairo ", ""):
        res_tz = energy_api.client.post(
            "/api/v1/energy/sleep/sessions",
            headers=energy_api.headers,
            json={
                "start_time": "2026-11-21T01:00:00Z",
                "end_time": "2026-11-21T07:00:00Z",
                "timezone": bad_tz,
            },
        )
        assert res_tz.status_code == 422

    # 6. Extra fields rejected
    res_extra = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-21T01:00:00Z",
            "end_time": "2026-11-21T07:00:00Z",
            "timezone": "UTC",
            "extra": "value",
        },
    )
    assert res_extra.status_code == 422

    # 7. user_id in body rejected
    res_uid = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-21T01:00:00Z",
            "end_time": "2026-11-21T07:00:00Z",
            "timezone": "UTC",
            "user_id": str(uuid4()),
        },
    )
    assert res_uid.status_code == 422


def test_list_filters_pagination_and_ordering(energy_api):
    # Create 3 sessions on different dates
    for day in (10, 11, 12):
        res = energy_api.client.post(
            "/api/v1/energy/sleep/sessions",
            headers=energy_api.headers,
            json={
                "start_time": f"2026-11-{day}T00:00:00Z",
                "end_time": f"2026-11-{day}T08:00:00Z",
                "timezone": "UTC",
            },
        )
        assert res.status_code == 201

    # 1. Newest first ordering
    res_list = energy_api.client.get("/api/v1/energy/sleep/sessions", headers=energy_api.headers)
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert len(items) == 3
    assert items[0]["sleep_date"] == "2026-11-12"
    assert items[1]["sleep_date"] == "2026-11-11"
    assert items[2]["sleep_date"] == "2026-11-10"

    # 2. Pagination: limit=2, offset=0 -> has_more=True
    res_page = energy_api.client.get(
        "/api/v1/energy/sleep/sessions?limit=2&offset=0",
        headers=energy_api.headers,
    )
    assert res_page.status_code == 200
    p = res_page.json()
    assert len(p["items"]) == 2
    assert p["has_more"] is True

    # Next page
    res_page2 = energy_api.client.get(
        "/api/v1/energy/sleep/sessions?limit=2&offset=2",
        headers=energy_api.headers,
    )
    assert res_page2.status_code == 200
    p2 = res_page2.json()
    assert len(p2["items"]) == 1
    assert p2["has_more"] is False

    # 3. Date range filter
    res_date = energy_api.client.get(
        "/api/v1/energy/sleep/sessions?date_from=2026-11-11&date_to=2026-11-11",
        headers=energy_api.headers,
    )
    assert res_date.status_code == 200
    d_items = res_date.json()["items"]
    assert len(d_items) == 1
    assert d_items[0]["sleep_date"] == "2026-11-11"

    # 4. Invalid date range (date_to < date_from) -> 422
    res_bad_dates = energy_api.client.get(
        "/api/v1/energy/sleep/sessions?date_from=2026-11-15&date_to=2026-11-10",
        headers=energy_api.headers,
    )
    assert res_bad_dates.status_code == 422

    # 5. Status filter
    res_status = energy_api.client.get(
        "/api/v1/energy/sleep/sessions?status=pending_confirmation",
        headers=energy_api.headers,
    )
    assert res_status.status_code == 200
    assert len(res_status.json()["items"]) == 0


def test_pending_confirmation_list_and_get_session(energy_api):
    # Insert pending session via DB to test GET /pending-confirmation
    user_id = energy_api.users[0]
    with energy_api.database.admin.connect() as conn:
        conn.execute(
            sa.text("""
                INSERT INTO public.sleep_sessions
                    (user_id, sleep_date, start_time, end_time, duration_minutes, timezone, source, confidence, status)
                VALUES
                    (:user_id, '2026-11-14', '2026-11-14 00:00:00+00', '2026-11-14 07:00:00+00', 420, 'UTC', 'android_usage', 0.85, 'pending_confirmation')
            """),
            {"user_id": user_id},
        )
        conn.commit()

    res_pending = energy_api.client.get("/api/v1/energy/sleep/pending-confirmation", headers=energy_api.headers)
    assert res_pending.status_code == 200
    items = res_pending.json()
    assert len(items) == 1
    session_id = items[0]["id"]
    assert items[0]["status"] == "pending_confirmation"
    assert items[0]["confidence"] == 0.85
    assert items[0]["source"] == "android_usage"

    # GET /sessions/{id}
    res_get = energy_api.client.get(f"/api/v1/energy/sleep/sessions/{session_id}", headers=energy_api.headers)
    assert res_get.status_code == 200
    assert res_get.json()["id"] == session_id

    # GET non-existent -> 404
    res_404 = energy_api.client.get(f"/api/v1/energy/sleep/sessions/{uuid4()}", headers=energy_api.headers)
    assert res_404.status_code == 404


def test_confirm_reject_and_transitions(energy_api):
    user_id = energy_api.users[0]
    # Insert pending session
    with energy_api.database.admin.connect() as conn:
        result = conn.execute(
            sa.text("""
                INSERT INTO public.sleep_sessions
                    (user_id, sleep_date, start_time, end_time, duration_minutes, timezone, source, confidence, status)
                VALUES
                    (:user_id, '2026-11-15', '2026-11-15 00:00:00+00', '2026-11-15 07:00:00+00', 420, 'UTC', 'android_usage', 0.85, 'pending_confirmation')
                RETURNING id
            """),
            {"user_id": user_id},
        )
        session_id = str(result.scalar_one())
        conn.commit()

    # Confirm with time corrections: single time update (start_time only) merged with stored end_time
    res_confirm = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{session_id}/confirm",
        headers=energy_api.headers,
        json={"status": "confirmed", "start_time": "2026-11-15T01:00:00Z"},
    )
    assert res_confirm.status_code == 200
    c_data = res_confirm.json()
    assert c_data["status"] == "confirmed"
    assert c_data["duration_minutes"] == 360  # 01:00 to 07:00
    assert c_data["source"] == "android_usage"
    assert c_data["confidence"] == 0.85

    # Edit confirmed session: timezone only change recomputes sleep_date
    res_tz_edit = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{session_id}/confirm",
        headers=energy_api.headers,
        json={"status": "confirmed", "timezone": "Asia/Tokyo"},  # UTC 07:00 is Tokyo 16:00
    )
    assert res_tz_edit.status_code == 200
    assert res_tz_edit.json()["timezone"] == "Asia/Tokyo"
    assert res_tz_edit.json()["sleep_date"] == "2026-11-15"

    # Transition confirmed -> rejected is forbidden (409)
    res_illegal_reject = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{session_id}/confirm",
        headers=energy_api.headers,
        json={"status": "rejected"},
    )
    assert res_illegal_reject.status_code == 409

    # Insert another pending session and reject it
    with energy_api.database.admin.connect() as conn:
        result = conn.execute(
            sa.text("""
                INSERT INTO public.sleep_sessions
                    (user_id, sleep_date, start_time, end_time, duration_minutes, timezone, source, confidence, status)
                VALUES
                    (:user_id, '2026-11-16', '2026-11-16 00:00:00+00', '2026-11-16 07:00:00+00', 420, 'UTC', 'android_usage', 0.85, 'pending_confirmation')
                RETURNING id
            """),
            {"user_id": user_id},
        )
        pending_id2 = str(result.scalar_one())
        conn.commit()

    # Rejection with time modifications forbidden (422)
    res_reject_with_time = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{pending_id2}/confirm",
        headers=energy_api.headers,
        json={"status": "rejected", "start_time": "2026-11-16T01:00:00Z"},
    )
    assert res_reject_with_time.status_code == 422

    # Reject pending -> 200
    res_reject = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{pending_id2}/confirm",
        headers=energy_api.headers,
        json={"status": "rejected"},
    )
    assert res_reject.status_code == 200
    assert res_reject.json()["status"] == "rejected"

    # Transition rejected -> anything is forbidden (409)
    res_rejected_to_conf = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{pending_id2}/confirm",
        headers=energy_api.headers,
        json={"status": "confirmed"},
    )
    assert res_rejected_to_conf.status_code == 409


def test_rejected_session_frees_sleep_date_for_new_session(energy_api):
    # Insert pending session on 2026-11-17 and reject it
    user_id = energy_api.users[0]
    with energy_api.database.admin.connect() as conn:
        result = conn.execute(
            sa.text("""
                INSERT INTO public.sleep_sessions
                    (user_id, sleep_date, start_time, end_time, duration_minutes, timezone, source, confidence, status)
                VALUES
                    (:user_id, '2026-11-17', '2026-11-17 00:00:00+00', '2026-11-17 07:00:00+00', 420, 'UTC', 'android_usage', 0.85, 'rejected')
                RETURNING id
            """),
            {"user_id": user_id},
        )
        conn.commit()

    # Now creating a new manual session on the same sleep_date (2026-11-17) succeeds
    res = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-17T01:00:00Z",
            "end_time": "2026-11-17T08:00:00Z",
            "timezone": "UTC",
        },
    )
    assert res.status_code == 201
    assert res.json()["sleep_date"] == "2026-11-17"


def test_confirm_colliding_pending_session_returns_409(energy_api):
    # Create confirmed session on 2026-11-18
    res_conf = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-18T00:00:00Z",
            "end_time": "2026-11-18T07:00:00Z",
            "timezone": "UTC",
        },
    )
    assert res_conf.status_code == 201

    # Insert pending session on 2026-11-19
    user_id = energy_api.users[0]
    with energy_api.database.admin.connect() as conn:
        result = conn.execute(
            sa.text("""
                INSERT INTO public.sleep_sessions
                    (user_id, sleep_date, start_time, end_time, duration_minutes, timezone, source, confidence, status)
                VALUES
                    (:user_id, '2026-11-19', '2026-11-19T00:00:00+00', '2026-11-19T07:00:00+00', 420, 'UTC', 'android_usage', 0.85, 'pending_confirmation')
                RETURNING id
            """),
            {"user_id": user_id},
        )
        pending_id = str(result.scalar_one())
        conn.commit()

    # Confirming this pending session with corrections that shift sleep_date to 2026-11-18 -> collides -> 409
    res_collide = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{pending_id}/confirm",
        headers=energy_api.headers,
        json={
            "status": "confirmed",
            "start_time": "2026-11-18T08:00:00Z",
            "end_time": "2026-11-18T12:00:00Z",
        },
    )
    assert res_collide.status_code == 409
    assert res_collide.json()["error"]["code"] == "conflict"



def test_delete_cascades_quality_rating(energy_api):
    # 1. Create session
    res_sess = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-19T00:00:00Z",
            "end_time": "2026-11-19T07:00:00Z",
            "timezone": "UTC",
        },
    )
    assert res_sess.status_code == 201
    session_id = res_sess.json()["id"]

    # 2. Add quality rating
    res_rate = energy_api.client.post(
        f"/api/v1/energy/sleep/sessions/{session_id}/quality",
        headers=energy_api.headers,
        json={"rating": 5},
    )
    assert res_rate.status_code == 201

    # 3. Delete session -> 204
    res_del = energy_api.client.delete(
        f"/api/v1/energy/sleep/sessions/{session_id}",
        headers=energy_api.headers,
    )
    assert res_del.status_code == 204

    # 4. GET session -> 404
    assert energy_api.client.get(f"/api/v1/energy/sleep/sessions/{session_id}", headers=energy_api.headers).status_code == 404

    # 5. Check quality rating was cascaded in database
    with energy_api.database.admin.connect() as conn:
        count = conn.scalar(
            sa.select(sa.func.count())
            .select_from(SleepQualityRating)
            .where(SleepQualityRating.session_id == session_id)
        )
        assert count == 0


def test_sleep_quality_crud_and_validations(energy_api):
    # Create confirmed session
    res_sess = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-20T00:00:00Z",
            "end_time": "2026-11-20T07:00:00Z",
            "timezone": "UTC",
        },
    )
    assert res_sess.status_code == 201
    session_id = res_sess.json()["id"]

    # Insert pending session
    user_id = energy_api.users[0]
    with energy_api.database.admin.connect() as conn:
        result = conn.execute(
            sa.text("""
                INSERT INTO public.sleep_sessions
                    (user_id, sleep_date, start_time, end_time, duration_minutes, timezone, source, confidence, status)
                VALUES
                    (:user_id, '2026-11-21', '2026-11-21 00:00:00+00', '2026-11-21 07:00:00+00', 420, 'UTC', 'android_usage', 0.85, 'pending_confirmation')
                RETURNING id
            """),
            {"user_id": user_id},
        )
        pending_id = str(result.scalar_one())
        conn.commit()

    # 1. Rating on pending session rejected (409)
    res_pend_rate = energy_api.client.post(
        f"/api/v1/energy/sleep/sessions/{pending_id}/quality",
        headers=energy_api.headers,
        json={"rating": 3},
    )
    assert res_pend_rate.status_code == 409

    # 2. Strict int rejection of true, "4", 4.5, 0, 6
    for bad_rating in (True, "4", 4.5, 0, 6):
        res_bad = energy_api.client.post(
            f"/api/v1/energy/sleep/sessions/{session_id}/quality",
            headers=energy_api.headers,
            json={"rating": bad_rating},
        )
        assert res_bad.status_code == 422

    # 3. Create rating (1 to 5)
    # rating 4 -> normalize_sleep_quality(4) = 75.0
    res_rate = energy_api.client.post(
        f"/api/v1/energy/sleep/sessions/{session_id}/quality",
        headers=energy_api.headers,
        json={"rating": 4},
    )
    assert res_rate.status_code == 201
    q_data = res_rate.json()
    assert q_data["rating"] == 4
    assert q_data["quality_score"] == 75.0
    assert "notes" not in q_data  # notes must NOT be exposed

    # 4. Rating already exists -> 409
    res_dup_rate = energy_api.client.post(
        f"/api/v1/energy/sleep/sessions/{session_id}/quality",
        headers=energy_api.headers,
        json={"rating": 3},
    )
    assert res_dup_rate.status_code == 409

    # 5. Patch rating: 4 -> 2 -> quality_score = 25.0
    res_patch = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{session_id}/quality",
        headers=energy_api.headers,
        json={"rating": 2},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["rating"] == 2
    assert res_patch.json()["quality_score"] == 25.0

    # 6. Patch rating on session without rating -> 404
    res_patch_404 = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{pending_id}/quality",
        headers=energy_api.headers,
        json={"rating": 2},
    )
    assert res_patch_404.status_code == 404

    # 7. GET /sessions/{id} returns rating and quality_score
    res_get_sess = energy_api.client.get(f"/api/v1/energy/sleep/sessions/{session_id}", headers=energy_api.headers)
    assert res_get_sess.status_code == 200
    assert res_get_sess.json()["quality_rating"] == 2
    assert res_get_sess.json()["quality_score"] == 25.0

    # 8. GET /energy/sleep/quality/history
    res_hist = energy_api.client.get("/api/v1/energy/sleep/quality/history", headers=energy_api.headers)
    assert res_hist.status_code == 200
    h_items = res_hist.json()["items"]
    assert len(h_items) >= 1
    assert h_items[0]["session_id"] == session_id
    assert h_items[0]["rating"] == 2
    assert h_items[0]["quality_score"] == 25.0
    assert h_items[0]["sleep_date"] == "2026-11-20"


def test_db_level_integrity_error_mapping(energy_api, monkeypatch):
    """Exercise DB-level IntegrityError mapping by bypassing pre-checks to simulate race conditions."""
    # 1. Duplicate interval DB error mapping
    payload1 = {
        "start_time": "2026-11-25T00:00:00Z",
        "end_time": "2026-11-25T07:00:00Z",
        "timezone": "UTC",
    }
    res1 = energy_api.client.post("/api/v1/energy/sleep/sessions", headers=energy_api.headers, json=payload1)
    assert res1.status_code == 201

    # Bypass pre-check so insert hits the DB unique constraint
    monkeypatch.setattr(sleep_service, "_precheck_session_conflicts", lambda *args, **kwargs: None)

    res_db_interval = energy_api.client.post("/api/v1/energy/sleep/sessions", headers=energy_api.headers, json=payload1)
    assert res_db_interval.status_code == 409
    assert res_db_interval.json()["error"]["code"] == "conflict"
    assert "start and end time" in res_db_interval.json()["error"]["message"]

    # 2. Duplicate sleep_date DB error mapping
    payload2 = {
        "start_time": "2026-11-25T08:00:00Z",
        "end_time": "2026-11-25T10:00:00Z",
        "timezone": "UTC",
    }
    res_db_date = energy_api.client.post("/api/v1/energy/sleep/sessions", headers=energy_api.headers, json=payload2)
    assert res_db_date.status_code == 409
    assert res_db_date.json()["error"]["code"] == "conflict"
    assert "sleep date" in res_db_date.json()["error"]["message"]

    # 3. Colliding edit DB error mapping
    payload3 = {
        "start_time": "2026-11-26T00:00:00Z",
        "end_time": "2026-11-26T07:00:00Z",
        "timezone": "UTC",
    }
    # Restore precheck temporarily to create second session
    monkeypatch.undo()
    res3 = energy_api.client.post("/api/v1/energy/sleep/sessions", headers=energy_api.headers, json=payload3)
    assert res3.status_code == 201
    s3_id = res3.json()["id"]

    # Bypass pre-check again
    monkeypatch.setattr(sleep_service, "_precheck_session_conflicts", lambda *args, **kwargs: None)
    # Edit s3 to collide on s1's interval
    res_edit_collide = energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{s3_id}/confirm",
        headers=energy_api.headers,
        json={"status": "confirmed", "start_time": "2026-11-25T00:00:00Z", "end_time": "2026-11-25T07:00:00Z"},
    )
    assert res_edit_collide.status_code == 409
    assert res_edit_collide.json()["error"]["code"] == "conflict"

    # 4. Duplicate rating DB error mapping
    monkeypatch.undo()
    res_rate1 = energy_api.client.post(
        f"/api/v1/energy/sleep/sessions/{s3_id}/quality",
        headers=energy_api.headers,
        json={"rating": 3},
    )
    assert res_rate1.status_code == 201

    # Bypass rating precheck
    monkeypatch.setattr(sleep_service, "_precheck_rating_exists", lambda *args, **kwargs: None)
    res_rate_db_dup = energy_api.client.post(
        f"/api/v1/energy/sleep/sessions/{s3_id}/quality",
        headers=energy_api.headers,
        json={"rating": 4},
    )
    assert res_rate_db_dup.status_code == 409
    assert res_rate_db_dup.json()["error"]["code"] == "conflict"
    assert "already exists for this session" in res_rate_db_dup.json()["error"]["message"]


def test_cross_user_isolation(energy_api):
    # User 0 creates session and rating
    res_u0 = energy_api.client.post(
        "/api/v1/energy/sleep/sessions",
        headers=energy_api.headers,
        json={
            "start_time": "2026-11-28T00:00:00Z",
            "end_time": "2026-11-28T07:00:00Z",
            "timezone": "UTC",
        },
    )
    assert res_u0.status_code == 201
    u0_session_id = res_u0.json()["id"]

    res_rate0 = energy_api.client.post(
        f"/api/v1/energy/sleep/sessions/{u0_session_id}/quality",
        headers=energy_api.headers,
        json={"rating": 5},
    )
    assert res_rate0.status_code == 201

    # User 1 headers
    user1_headers = {"Authorization": "Bearer " + energy_api.signer.token(energy_api.users[1])}

    # 1. User 1 cannot GET User 0's session -> 404
    assert energy_api.client.get(f"/api/v1/energy/sleep/sessions/{u0_session_id}", headers=user1_headers).status_code == 404

    # 2. User 1 cannot confirm/patch User 0's session -> 404
    assert energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{u0_session_id}/confirm",
        headers=user1_headers,
        json={"status": "confirmed"},
    ).status_code == 404

    # 3. User 1 cannot delete User 0's session -> 404
    assert energy_api.client.delete(f"/api/v1/energy/sleep/sessions/{u0_session_id}", headers=user1_headers).status_code == 404

    # 4. User 1 cannot rate User 0's session -> 404
    assert energy_api.client.post(
        f"/api/v1/energy/sleep/sessions/{u0_session_id}/quality",
        headers=user1_headers,
        json={"rating": 3},
    ).status_code == 404

    # 5. User 1 cannot update User 0's rating -> 404
    assert energy_api.client.patch(
        f"/api/v1/energy/sleep/sessions/{u0_session_id}/quality",
        headers=user1_headers,
        json={"rating": 3},
    ).status_code == 404

    # 6. User 1 list sessions does not see User 0's session
    res_u1_list = energy_api.client.get("/api/v1/energy/sleep/sessions", headers=user1_headers)
    assert res_u1_list.status_code == 200
    assert len(res_u1_list.json()["items"]) == 0

    # 7. User 1 pending confirmation list does not see User 0's session
    res_u1_pending = energy_api.client.get("/api/v1/energy/sleep/pending-confirmation", headers=user1_headers)
    assert res_u1_pending.status_code == 200
    assert len(res_u1_pending.json()) == 0

    # 8. User 1 quality history does not see User 0's rating
    res_u1_hist = energy_api.client.get("/api/v1/energy/sleep/quality/history", headers=user1_headers)
    assert res_u1_hist.status_code == 200
    assert len(res_u1_hist.json()["items"]) == 0


def test_unauthenticated_requests_return_401(energy_api):
    dummy_id = str(uuid4())
    assert energy_api.client.post("/api/v1/energy/sleep/sessions", json={}).status_code == 401
    assert energy_api.client.get("/api/v1/energy/sleep/sessions").status_code == 401
    assert energy_api.client.get("/api/v1/energy/sleep/pending-confirmation").status_code == 401
    assert energy_api.client.get(f"/api/v1/energy/sleep/sessions/{dummy_id}").status_code == 401
    assert energy_api.client.patch(f"/api/v1/energy/sleep/sessions/{dummy_id}/confirm", json={}).status_code == 401
    assert energy_api.client.delete(f"/api/v1/energy/sleep/sessions/{dummy_id}").status_code == 401
    assert energy_api.client.post(f"/api/v1/energy/sleep/sessions/{dummy_id}/quality", json={}).status_code == 401
    assert energy_api.client.patch(f"/api/v1/energy/sleep/sessions/{dummy_id}/quality", json={}).status_code == 401
    assert energy_api.client.get("/api/v1/energy/sleep/quality/history").status_code == 401


def test_alembic_check(database, monkeypatch):
    """Verify that models and alembic migrations are in sync."""
    from alembic import command
    from alembic.config import Config
    from app.core.config import BACKEND_ROOT

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    monkeypatch.setenv("MIGRATION_DATABASE_URL", database.admin_url.render_as_string(hide_password=False))
    command.check(cfg)

