import pytest
import sqlalchemy as sa

from app.modules.scheduler.models import Event, Task
from tests.test_api_schemas import ONCE, TASK, WEEKLY


@pytest.mark.parametrize("resource,body,id_field,model", [("tasks", TASK, "task_id", Task), ("events", ONCE, "event_id", Event)])
def test_owned_crud_persists_and_cross_user_reads_edits_deletes_are_hidden(api, resource, body, id_field, model):
    response = api.client.post("/api/v1/" + resource, json=body, headers=api.headers)
    assert response.status_code == 201
    created = response.json()
    assert created["user_id"] == str(api.users[0])
    path = "/api/v1/" + resource + "/" + created[id_field]
    assert api.client.get(path, headers=api.headers).json()["title"] == body["title"]
    other_headers = {"Authorization": "Bearer " + api.signer.token(api.users[1])}
    for method in ("get", "patch", "delete"):
        kwargs = {"json": {"title": "Stolen"}} if method == "patch" else {}
        assert getattr(api.client, method)(path, headers=other_headers, **kwargs).status_code == 404
    assert api.client.get("/api/v1/" + resource, headers=other_headers).json()["items"] == []
    assert api.client.patch(path, headers=api.headers, json={"title": "Updated"}).json()["title"] == "Updated"
    with api.database.admin.connect() as connection:
        assert connection.scalar(sa.select(model.title).where(getattr(model, id_field) == created[id_field])) == "Updated"
    assert api.client.delete(path, headers=api.headers).status_code == 204
    assert api.client.get(path, headers=api.headers).status_code == 404
    api.database.runtime.check_connection()


@pytest.mark.parametrize("resource,body", [("tasks", TASK), ("events", ONCE)])
def test_create_rejects_forged_owner_and_status(api, resource, body):
    for extra in ({"user_id": str(api.users[1])}, {"status": "COMPLETED"}):
        assert api.client.post("/api/v1/" + resource, headers=api.headers, json=body | extra).status_code == 422


def test_task_patch_validates_combined_record_and_allows_atomic_type_switch(api):
    fixed = TASK | dict(task_type="FIXED", fixed_start_at=ONCE["start_at"], fixed_end_at=ONCE["end_at"])
    result = api.client.post("/api/v1/tasks", headers=api.headers, json=fixed).json()
    path = "/api/v1/tasks/" + result["task_id"]
    for change in ({"fixed_end_at": fixed["fixed_start_at"]}, {"fixed_start_at": None}, {"task_type": "FLEXIBLE"}):
        assert api.client.patch(path, headers=api.headers, json=change).status_code == 422
    change = {"task_type": "FLEXIBLE", "fixed_start_at": None, "fixed_end_at": None, "deadline": None}
    assert api.client.patch(path, headers=api.headers, json=change).status_code == 200


def test_weekly_timetable_and_recurrence_patch(api):
    result = api.client.post("/api/v1/events", headers=api.headers, json=WEEKLY)
    assert result.status_code == 201
    assert result.json()["weekday"] == 1
    assert result.json()["source"] == "MANUAL" and result.json()["status"] == "SAVED"
    path = "/api/v1/events/" + result.json()["event_id"]
    assert api.client.patch(path, headers=api.headers, json={"recurrence_end_day_offset": 0}).status_code == 422
    response = api.client.patch(path, headers=api.headers, json={"recurrence_first_date": "2026-11-17"})
    assert response.status_code == 200 and response.json()["weekday"] == 2
    assert api.client.patch(path, headers=api.headers, json={"weekday": 1}).status_code == 422


@pytest.mark.parametrize("resource,body", [("tasks", TASK), ("events", ONCE)])
def test_lists_are_paginated_filtered_and_do_not_query_per_item(api, resource, body):
    for i in range(4):
        assert api.client.post("/api/v1/" + resource, headers=api.headers, json=body | {"title": "Entry " + str(i)}).status_code == 201
    statements = []
    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    sa.event.listen(api.database.runtime.engine, "before_cursor_execute", collect)
    try:
        first = api.client.get("/api/v1/" + resource + "?limit=2&category=" + body["category"], headers=api.headers).json()
        assert len(first["items"]) == 2 and first["has_more"] is True
        first_count = len(statements)
        statements.clear()
        full = api.client.get("/api/v1/" + resource + "?limit=100", headers=api.headers).json()
        assert len(full["items"]) == 4 and full["has_more"] is False
        assert len(statements) == first_count
        assert sum("FROM public." + resource in query for query in statements) == 1
    finally:
        sa.event.remove(api.database.runtime.engine, "before_cursor_execute", collect)
    second = api.client.get("/api/v1/" + resource + "?limit=2&offset=2", headers=api.headers).json()
    id_field = "task_id" if resource == "tasks" else "event_id"
    assert {row[id_field] for row in first["items"]}.isdisjoint(row[id_field] for row in second["items"])
    assert api.client.get("/api/v1/" + resource + "?category=HEALTH", headers=api.headers).json()["items"] == []
    for query in ("limit=0", "limit=101", "offset=-1", "category=INVALID"):
        assert api.client.get("/api/v1/" + resource + "?" + query, headers=api.headers).status_code == 422


def test_task_filters_and_deadline_bounds(api):
    first = api.client.post("/api/v1/tasks", headers=api.headers, json=TASK | {"deadline": "2026-11-20T09:00:00Z"})
    assert first.status_code == 201
    fixed = TASK | {"task_type": "FIXED", "fixed_start_at": ONCE["start_at"], "fixed_end_at": ONCE["end_at"],
                    "deadline": "2026-11-22T09:00:00Z", "priority": 5, "category": "WORK"}
    result = api.client.post("/api/v1/tasks", headers=api.headers, json=fixed)
    assert result.status_code == 201
    page = api.client.get("/api/v1/tasks?task_type=FIXED&category=WORK&priority=5&deadline_from=2026-11-21T00:00:00Z&deadline_to=2026-11-23T00:00:00Z", headers=api.headers)
    assert page.status_code == 200
    assert [item["task_id"] for item in page.json()["items"]] == [result.json()["task_id"]]
    assert api.client.get("/api/v1/tasks?deadline_from=2026-11-23T00:00:00Z&deadline_to=2026-11-21T00:00:00Z", headers=api.headers).status_code == 422
    assert api.client.get("/api/v1/tasks?deadline_from=2026-11-21T00:00:00", headers=api.headers).status_code == 422


def test_event_recurrence_filter_returns_only_matching_rules(api):
    assert api.client.post("/api/v1/events", headers=api.headers, json=ONCE).status_code == 201
    weekly = api.client.post("/api/v1/events", headers=api.headers, json=WEEKLY)
    assert weekly.status_code == 201
    result = api.client.get("/api/v1/events?recurrence_type=WEEKLY&category=WORK", headers=api.headers)
    assert result.status_code == 200
    assert [item["event_id"] for item in result.json()["items"]] == [weekly.json()["event_id"]]
    assert api.client.get("/api/v1/events?recurrence_type=DAILY", headers=api.headers).status_code == 422
