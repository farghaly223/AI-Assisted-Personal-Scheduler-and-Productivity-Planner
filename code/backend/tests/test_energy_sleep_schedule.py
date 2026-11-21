"""Tests for Energy module Sleep Schedule endpoints."""

import concurrent.futures
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.modules.energy.models import SleepSchedule

VALID_SCHEDULE = {
    "bedtime": "23:00",
    "wake_time": "07:00",
    "timezone": "Africa/Cairo",
    "repeat_days": [0, 1, 2, 3, 4, 5, 6],
    "bedtime_reminder_enabled": True,
    "reminder_minutes_before": 15,
    "wake_alarm_enabled": True,
}


@pytest.fixture
def energy_api(api):
    from app.modules.energy.router import router as energy_router

    if not getattr(api.client.app.state, "_energy_router_included", False):
        api.client.app.include_router(energy_router, prefix="/api/v1")
        api.client.app.state._energy_router_included = True
    return api


def test_put_creates_and_replaces_sleep_schedule(energy_api):
    # 1. Create schedule
    res = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=VALID_SCHEDULE,
    )
    assert res.status_code == 200
    created = res.json()
    assert created["bedtime"] == "23:00:00"
    assert created["wake_time"] == "07:00:00"
    assert created["timezone"] == "Africa/Cairo"
    assert created["repeat_days"] == [0, 1, 2, 3, 4, 5, 6]
    assert created["bedtime_reminder_enabled"] is True
    assert created["reminder_minutes_before"] == 15
    assert created["wake_alarm_enabled"] is True
    assert created["is_active"] is True
    assert created["created_at"] is not None
    assert created["updated_at"] is not None

    # 2. Replace schedule (PUT upsert)
    updated_payload = dict(
        VALID_SCHEDULE,
        bedtime="22:30:00",
        wake_time="06:30:00",
        reminder_minutes_before=30,
        repeat_days=[1, 2, 3, 4, 5],
    )
    res_updated = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=updated_payload,
    )
    assert res_updated.status_code == 200
    replaced = res_updated.json()
    assert replaced["id"] == created["id"]
    assert replaced["bedtime"] == "22:30:00"
    assert replaced["wake_time"] == "06:30:00"
    assert replaced["reminder_minutes_before"] == 30
    assert replaced["repeat_days"] == [1, 2, 3, 4, 5]
    assert replaced["is_active"] is True


def test_get_sleep_schedule_success_and_404(energy_api):
    # 404 when no schedule
    res = energy_api.client.get("/api/v1/energy/sleep-schedule", headers=energy_api.headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"

    # Create schedule
    energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=VALID_SCHEDULE,
    )

    # 200 retrieved
    res_get = energy_api.client.get("/api/v1/energy/sleep-schedule", headers=energy_api.headers)
    assert res_get.status_code == 200
    data = res_get.json()
    assert data["bedtime"] == "23:00:00"
    assert data["timezone"] == "Africa/Cairo"


def test_toggle_sleep_schedule(energy_api):
    # 404 when no schedule exists
    res = energy_api.client.patch(
        "/api/v1/energy/sleep-schedule/toggle",
        headers=energy_api.headers,
        json={"is_active": False},
    )
    assert res.status_code == 404

    # Create schedule
    energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=VALID_SCHEDULE,
    )

    # Toggle to false
    res_toggle = energy_api.client.patch(
        "/api/v1/energy/sleep-schedule/toggle",
        headers=energy_api.headers,
        json={"is_active": False},
    )
    assert res_toggle.status_code == 200
    assert res_toggle.json()["is_active"] is False

    # Toggle to true
    res_toggle_on = energy_api.client.patch(
        "/api/v1/energy/sleep-schedule/toggle",
        headers=energy_api.headers,
        json={"is_active": True},
    )
    assert res_toggle_on.status_code == 200
    assert res_toggle_on.json()["is_active"] is True


def test_delete_disables_schedule_and_is_idempotent(energy_api):
    # 404 when no schedule exists
    res_del_404 = energy_api.client.delete(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
    )
    assert res_del_404.status_code == 404

    # Create schedule
    energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=VALID_SCHEDULE,
    )

    # Delete schedule -> 204
    res_del = energy_api.client.delete(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
    )
    assert res_del.status_code == 204
    assert res_del.content == b""

    # GET returns disabled schedule with settings preserved
    res_get = energy_api.client.get("/api/v1/energy/sleep-schedule", headers=energy_api.headers)
    assert res_get.status_code == 200
    schedule = res_get.json()
    assert schedule["is_active"] is False
    assert schedule["bedtime"] == "23:00:00"
    assert schedule["wake_time"] == "07:00:00"

    # DELETE on already-disabled schedule -> 204 (idempotent)
    res_del_again = energy_api.client.delete(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
    )
    assert res_del_again.status_code == 204


def test_put_always_reenables_disabled_schedule(energy_api):
    # Create, then disable
    energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=VALID_SCHEDULE,
    )
    energy_api.client.delete("/api/v1/energy/sleep-schedule", headers=energy_api.headers)

    get_disabled = energy_api.client.get("/api/v1/energy/sleep-schedule", headers=energy_api.headers).json()
    assert get_disabled["is_active"] is False

    # PUT again -> must set is_active = True
    res_put = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=VALID_SCHEDULE,
    )
    assert res_put.status_code == 200
    assert res_put.json()["is_active"] is True

    get_active = energy_api.client.get("/api/v1/energy/sleep-schedule", headers=energy_api.headers).json()
    assert get_active["is_active"] is True


def test_validation_edge_cases(energy_api):
    # 1. Equal times rejected
    res = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=dict(VALID_SCHEDULE, bedtime="07:00", wake_time="07:00"),
    )
    assert res.status_code == 422

    # 2. Overnight schedule (bedtime > wake_time) is valid
    res_overnight = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=dict(VALID_SCHEDULE, bedtime="23:30", wake_time="07:15"),
    )
    assert res_overnight.status_code == 200

    # 3. Bad timezone (invalid or with whitespace)
    for bad_tz in ("Invalid/Nowhere", " Africa/Cairo", "Africa/Cairo ", ""):
        res_tz = energy_api.client.put(
            "/api/v1/energy/sleep-schedule",
            headers=energy_api.headers,
            json=dict(VALID_SCHEDULE, timezone=bad_tz),
        )
        assert res_tz.status_code == 422

    # 4. Repeat days: duplicates deduplicated and sorted -> 200
    res_dup = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=dict(VALID_SCHEDULE, repeat_days=[5, 2, 2, 0, 5]),
    )
    assert res_dup.status_code == 200
    assert res_dup.json()["repeat_days"] == [0, 2, 5]

    # 5. Repeat days invalid: empty, out of range, types
    for bad_days in ([], [7], [-1], [True, 1], ["0", 1], [1.5]):
        res_days = energy_api.client.put(
            "/api/v1/energy/sleep-schedule",
            headers=energy_api.headers,
            json=dict(VALID_SCHEDULE, repeat_days=bad_days),
        )
        assert res_days.status_code == 422

    # 6. Reminder minutes before: 0 and 180 valid; -1 and 181 invalid
    for valid_min in (0, 180):
        res_min = energy_api.client.put(
            "/api/v1/energy/sleep-schedule",
            headers=energy_api.headers,
            json=dict(VALID_SCHEDULE, reminder_minutes_before=valid_min),
        )
        assert res_min.status_code == 200
    for invalid_min in (-1, 181):
        res_min = energy_api.client.put(
            "/api/v1/energy/sleep-schedule",
            headers=energy_api.headers,
            json=dict(VALID_SCHEDULE, reminder_minutes_before=invalid_min),
        )
        assert res_min.status_code == 422

    # 7. Bedtime with microseconds rejected
    res_micro = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=dict(VALID_SCHEDULE, bedtime="23:00:00.123456"),
    )
    assert res_micro.status_code == 422

    # 8. Extra fields forbidden
    res_extra = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=dict(VALID_SCHEDULE, unknown_field="unexpected"),
    )
    assert res_extra.status_code == 422

    # 9. user_id in body forbidden
    res_uid = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=dict(VALID_SCHEDULE, user_id=str(uuid4())),
    )
    assert res_uid.status_code == 422

    # 10. Toggle strict boolean
    for bad_bool in ("true", 1, 0, None):
        res_bool = energy_api.client.patch(
            "/api/v1/energy/sleep-schedule/toggle",
            headers=energy_api.headers,
            json={"is_active": bad_bool},
        )
        assert res_bool.status_code == 422


def test_cross_user_isolation(energy_api):
    # User 0 creates schedule
    res_user0 = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=energy_api.headers,
        json=VALID_SCHEDULE,
    )
    assert res_user0.status_code == 200
    user0_schedule_id = res_user0.json()["id"]

    # User 1 headers
    user1_headers = {"Authorization": "Bearer " + energy_api.signer.token(energy_api.users[1])}

    # User 1 cannot see User 0's schedule
    assert energy_api.client.get("/api/v1/energy/sleep-schedule", headers=user1_headers).status_code == 404

    # User 1 cannot toggle User 0's schedule
    assert energy_api.client.patch(
        "/api/v1/energy/sleep-schedule/toggle",
        headers=user1_headers,
        json={"is_active": False},
    ).status_code == 404

    # User 1 cannot delete User 0's schedule
    assert energy_api.client.delete("/api/v1/energy/sleep-schedule", headers=user1_headers).status_code == 404

    # User 1 creates own schedule
    user1_payload = dict(VALID_SCHEDULE, bedtime="21:00", wake_time="05:00")
    res_user1 = energy_api.client.put(
        "/api/v1/energy/sleep-schedule",
        headers=user1_headers,
        json=user1_payload,
    )
    assert res_user1.status_code == 200
    user1_schedule_id = res_user1.json()["id"]
    assert user1_schedule_id != user0_schedule_id

    # User 0 still has original schedule
    res_user0_check = energy_api.client.get("/api/v1/energy/sleep-schedule", headers=energy_api.headers)
    assert res_user0_check.status_code == 200
    assert res_user0_check.json()["id"] == user0_schedule_id
    assert res_user0_check.json()["bedtime"] == "23:00:00"


def test_unauthenticated_request_rejected(energy_api):
    assert energy_api.client.get("/api/v1/energy/sleep-schedule").status_code == 401
    assert energy_api.client.put("/api/v1/energy/sleep-schedule", json=VALID_SCHEDULE).status_code == 401
    assert energy_api.client.patch("/api/v1/energy/sleep-schedule/toggle", json={"is_active": True}).status_code == 401
    assert energy_api.client.delete("/api/v1/energy/sleep-schedule").status_code == 401


def test_concurrency_simultaneous_puts(energy_api):
    def put_schedule():
        return energy_api.client.put(
            "/api/v1/energy/sleep-schedule",
            headers=energy_api.headers,
            json=VALID_SCHEDULE,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(put_schedule)
        f2 = executor.submit(put_schedule)
        r1 = f1.result()
        r2 = f2.result()

    assert r1.status_code == 200
    assert r2.status_code == 200

    with energy_api.database.admin.connect() as conn:
        count = conn.scalar(
            sa.select(sa.func.count())
            .select_from(SleepSchedule)
            .where(SleepSchedule.user_id == energy_api.users[0])
        )
        assert count == 1
