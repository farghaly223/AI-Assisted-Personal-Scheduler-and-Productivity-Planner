import json

import pytest
import sqlalchemy as sa

from app.modules.user.models import Profile, UserPreferences
from tests.api_support import seed_auth_user


CREDS = {"email": "student@example.com", "password": "test-password-123"}


@pytest.mark.parametrize("route", ["register", "login", "refresh"])
def test_authentication_delegates_and_returns_verified_tokens(api, route):
    body = {"refresh_token": "old-refresh"} if route == "refresh" else CREDS
    response = api.client.post("/api/v1/auth/" + route, json=body)
    assert response.status_code == (201 if route == "register" else 200)
    assert response.json()["session"]["token_type"] == "bearer"
    token = response.json()["session"]["access_token"]
    assert api.signer.verifier.verify(token).user_id == api.users[0]
    assert response.headers["cache-control"] == "no-store"
    sent = json.loads(api.state.requests[-1].content)
    assert sent == body or sent == body | {"data": {}}


def test_confirmation_signup_does_not_provision_unverified_profile(api):
    subject = seed_auth_user(api.database)
    api.state.subject, api.state.mode = subject, "confirmation"
    response = api.client.post("/api/v1/auth/register", json=CREDS)
    assert response.status_code == 201
    assert response.json()["confirmation_required"] is True
    assert response.json()["session"] is None
    with api.database.admin.connect() as connection:
        assert connection.scalar(sa.select(Profile.id).where(Profile.id == subject)) is None


def test_login_and_profile_get_provision_defaults_idempotently(api):
    subject = seed_auth_user(api.database)
    api.state.subject = subject
    assert api.client.post("/api/v1/auth/login", json=CREDS).status_code == 200
    assert api.client.post("/api/v1/auth/login", json=CREDS).status_code == 200
    headers = {"Authorization": "Bearer " + api.signer.token(subject)}
    assert api.client.get("/api/v1/profiles/me", headers=headers).json()["id"] == str(subject)
    preferences = api.client.get("/api/v1/preferences/me", headers=headers).json()
    assert preferences["timezone"] == "UTC" and preferences["notifications_enabled"] is False
    with api.database.admin.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(UserPreferences).where(UserPreferences.user_id == subject)) == 1


@pytest.mark.parametrize("mode", ["bad_token", "wrong_subject", "empty_refresh"])
def test_authentication_never_returns_unverified_upstream_session(api, mode):
    api.state.mode = mode
    response = api.client.post("/api/v1/auth/login", json=CREDS)
    assert response.status_code == 502
    assert "access_token" not in response.text


@pytest.mark.parametrize("status,expected", [(400, 401), (401, 401), (429, 429), (500, 502)])
def test_login_upstream_errors_are_safe(api, status, expected):
    api.state.status = status
    response = api.client.post("/api/v1/auth/login", json=CREDS)
    assert response.status_code == expected
    assert "secret provider detail" not in response.text
    assert CREDS["password"] not in response.text
    if status == 429:
        assert response.headers["retry-after"] == "30"


def test_auth_timeout_is_service_unavailable(api):
    api.state.mode = "timeout"
    response = api.client.post("/api/v1/auth/register", json=CREDS)
    assert response.status_code == 503
    assert "sensitive" not in response.text


@pytest.mark.parametrize("path", ["profiles/me", "preferences/me", "tasks", "events"])
@pytest.mark.parametrize("token", [None, "invalid", "expired", "invalid_subject"])
def test_missing_invalid_and_expired_identity_never_accesses_private_api(api, path, token):
    headers = {}
    if token:
        value = api.signer.token(api.users[0], exp=1) if token == "expired" else token
        if token == "invalid_subject":
            value = api.signer.token(api.users[0], sub="not-a-uuid")
        headers["Authorization"] = "Bearer " + value
    response = api.client.get("/api/v1/" + path, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_profile_and_preferences_updates_are_owned_and_validated(api):
    response = api.client.patch("/api/v1/profiles/me", headers=api.headers, json={"full_name": "Updated Student"})
    assert response.status_code == 200 and response.json()["full_name"] == "Updated Student"
    assert response.json()["id"] == str(api.users[0])
    assert api.client.patch("/api/v1/profiles/me", headers=api.headers, json={"id": str(api.users[1])}).status_code == 422
    body = {"timezone": "Africa/Cairo", "preferred_work_start": "22:00:00", "preferred_work_end": "06:00:00"}
    assert api.client.patch("/api/v1/preferences/me", headers=api.headers, json=body).status_code == 200
    assert api.client.patch("/api/v1/preferences/me", headers=api.headers, json={"preferred_work_end": "22:00:00"}).status_code == 422
    other = {"Authorization": "Bearer " + api.signer.token(api.users[1])}
    assert api.client.get("/api/v1/preferences/me", headers=other).json()["timezone"] == "UTC"
    assert api.client.patch("/api/v1/preferences/me", headers=api.headers, json={"user_id": str(api.users[1])}).status_code == 422
    api.database.runtime.check_connection()


def test_username_conflict_rolls_back_before_response(api):
    with api.database.admin.begin() as connection:
        connection.execute(sa.update(Profile).where(Profile.id == api.users[1]).values(username="taken_" + str(api.users[1])))
    response = api.client.patch("/api/v1/profiles/me", headers=api.headers, json={"username": "taken_" + str(api.users[1])})
    assert response.status_code == 409
    api.database.runtime.check_connection()
    assert api.client.get("/api/v1/profiles/me", headers=api.headers).json()["username"] is None


def test_database_commit_failure_cannot_return_success_or_persist_changes(api):
    from sqlalchemy.orm import Session
    from fastapi.testclient import TestClient
    def fail_commit(session):
        if session.info.get("user_id"):
            session.execute(sa.text("SELECT 1/0"))
    sa.event.listen(Session, "before_commit", fail_commit)
    try:
        with TestClient(api.client.app, raise_server_exceptions=False) as client:
            result = client.patch("/api/v1/profiles/me", headers=api.headers, json={"full_name": "Must roll back"})
        assert result.status_code == 503
        assert "division" not in result.text
    finally:
        sa.event.remove(Session, "before_commit", fail_commit)
    api.database.runtime.check_connection()
    assert api.client.get("/api/v1/profiles/me", headers=api.headers).json()["full_name"] == "Synthetic test user"


def test_confirmation_login_preserves_valid_registration_profile_details(api):
    api.state.subject = seed_auth_user(api.database)
    api.state.metadata = {"username": "student_" + str(api.state.subject), "full_name": "Registered Student"}
    api.state.mode = "confirmation"
    assert api.client.post("/api/v1/auth/register", json=CREDS | api.state.metadata).status_code == 201
    api.state.mode = "session"
    result = api.client.post("/api/v1/auth/login", json=CREDS)
    assert result.status_code == 200
    headers = {"Authorization": "Bearer " + result.json()["session"]["access_token"]}
    profile = api.client.get("/api/v1/profiles/me", headers=headers).json()
    assert profile["full_name"] == "Registered Student"
    assert profile["username"] == api.state.metadata["username"]


def test_auth_refresh_returns_verified_rotated_session_without_database(api, monkeypatch):
    monkeypatch.setattr(api.client.app.state, "database", None)
    result = api.client.post("/api/v1/auth/refresh", json={"refresh_token": "old-refresh"})
    assert result.status_code == 200
    assert result.json()["session"]["refresh_token"] == "synthetic-refresh-token"
    assert api.signer.verifier.verify(result.json()["session"]["access_token"]).user_id == api.users[0]


def test_automatic_onboarding_keeps_taken_optional_username_null(api):
    name = "claimed_" + str(api.users[1])
    with api.database.admin.begin() as connection:
        connection.execute(sa.update(Profile).where(Profile.id == api.users[1]).values(username=name))
    api.state.subject = seed_auth_user(api.database)
    api.state.metadata = {"username": name, "full_name": "New Student"}
    result = api.client.post("/api/v1/auth/register", json=CREDS | api.state.metadata)
    assert result.status_code == 201
    headers = {"Authorization": "Bearer " + result.json()["session"]["access_token"]}
    profile = api.client.get("/api/v1/profiles/me", headers=headers).json()
    assert profile["username"] is None and profile["full_name"] == "New Student"


@pytest.mark.parametrize("path,body", [("tasks", {"title": "Bad\x00text", "task_type": "FLEXIBLE", "estimated_duration_minutes": 1, "category": "STUDY", "priority": 1}),
    ("events", {"title": "Event", "location": "Bad\x00place", "category": "WORK", "start_at": "2026-11-16T09:00:00Z", "end_at": "2026-11-16T10:00:00Z"})])
def test_null_character_input_is_validation_error(api, path, body):
    assert api.client.post("/api/v1/" + path, headers=api.headers, json=body).status_code == 422


def test_profile_null_character_is_validation_error(api):
    assert api.client.patch("/api/v1/profiles/me", headers=api.headers, json={"full_name": "Bad\x00name"}).status_code == 422


def test_unicode_profile_text_persists(api):
    result = api.client.patch("/api/v1/profiles/me", headers=api.headers, json={"full_name": "مصطفى أحمد"})
    assert result.status_code == 200 and result.json()["full_name"] == "مصطفى أحمد"
