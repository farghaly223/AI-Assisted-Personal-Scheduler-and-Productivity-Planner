"""HTTP transport simulation; real application, JWT cryptography and RLS remain active."""

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient


@pytest.fixture
def api(database, signer, users):
    from app.core.config import Settings
    from app.main import create_app

    state = SimpleNamespace(mode="session", status=None, requests=[], admin_requests=[], admin_users=[],
                            subject=users[0], metadata={"full_name": "Student"})

    def provider(request):
        assert request.url.host == "scheduler-tests.invalid"
        if state.mode == "timeout":
            raise httpx.ReadTimeout("Synthetic sensitive upstream detail", request=request)
        if request.url.path == "/auth/v1/admin/users":
            state.admin_requests.append(request)
            assert request.method == "GET"
            assert request.headers["apikey"] == "sb_secret_test_admin_fixture"
            assert request.url.params["page"] == "1" and request.url.params["per_page"] == "100"
            return httpx.Response(200, json={"users": state.admin_users})
        state.requests.append(request)
        assert request.headers["apikey"] == "sb_publishable_test"
        if state.status:
            return httpx.Response(state.status, json={"msg": "secret provider detail"}, headers={"Retry-After": "30"})
        body = json.loads(request.content)
        if request.url.path.endswith("/signup"):
            assert set(body) <= {"email", "password", "data"}
        else:
            assert request.url.params["grant_type"] in ("password", "refresh_token")
        user = {"id": str(state.subject), "email": "student@example.com", "user_metadata": state.metadata}
        if state.mode == "confirmation":
            return httpx.Response(200, json=user)
        payload = dict(access_token=signer.token(state.subject), refresh_token="synthetic-refresh-token",
                       token_type="bearer", expires_in=300, user=user)
        if state.mode == "bad_token":
            payload["access_token"] = "not.a.token"
        if state.mode == "wrong_subject":
            payload["user"]["id"] = str(uuid4())
        if state.mode == "empty_refresh":
            payload["refresh_token"] = ""
        return httpx.Response(200, json=payload)

    transport_client = httpx.Client(transport=httpx.MockTransport(provider))
    app = create_app(settings=Settings(supabase_url="https://scheduler-tests.invalid", supabase_key="sb_publishable_test",
                                      auth_admin_key="sb_secret_test_admin_fixture"),
                     database=database.runtime, verifier=signer.verifier, auth_client=transport_client)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, state=state, database=database, signer=signer, users=users,
                              headers={"Authorization": "Bearer " + signer.token(users[0])})
    transport_client.close()


def seed_auth_user(database):
    subject = uuid4()
    with database.admin.begin() as connection:
        connection.execute(sa.text("INSERT INTO auth.users(id) VALUES (:id)"), {"id": subject})
    return subject
