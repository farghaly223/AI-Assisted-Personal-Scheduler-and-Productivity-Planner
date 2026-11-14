import pytest
from fastapi.testclient import TestClient

from app.config import ConfigurationError, Settings
from app.main import create_app


def test_liveness_docs_and_missing_database_readiness():
    with TestClient(create_app(settings=Settings())) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert client.get("/api/v1/health/ready").status_code == 503
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_ready_endpoint_uses_restricted_database(database):
    with TestClient(create_app(settings=Settings(), database=database.runtime)) as client:
        assert client.get("/api/v1/health/ready").json() == {"status": "ready"}


@pytest.mark.parametrize("settings", [
    dict(environment="production", supabase_url="http://127.0.0.1:54321"),
    dict(environment="development", supabase_url="http://attacker.example"),
    dict(supabase_url="https://user:secret@project.example"),
    dict(supabase_url="https://project.example?secret=hidden"),
    dict(supabase_key="sb_secret_FORBIDDEN"),
    dict(cors_origins=("*",)),
])
def test_unsafe_api_configuration_rejected(settings):
    with pytest.raises(ConfigurationError):
        Settings(**settings)


def test_local_supabase_requires_explicit_development_mode():
    Settings(environment="development", supabase_url="http://127.0.0.1:54321")


def test_invalid_secret_input_is_not_echoed():
    with TestClient(create_app(settings=Settings())) as client:
        response = client.post("/api/v1/auth/register", json={"email": "bad", "password": "secret"})
        assert response.status_code == 422
        assert "secret" not in response.text
        assert response.json()["error"]["code"] == "invalid_request"
        assert response.headers["cache-control"] == "no-store"


def test_openapi_describes_authenticated_endpoints():
    schema = create_app(settings=Settings()).openapi()
    assert schema["paths"]["/api/v1/tasks"]["get"]["security"]
    assert "401" in schema["paths"]["/api/v1/tasks"]["get"]["responses"]
    assert schema["paths"]["/api/v1/auth/login"]["post"]["requestBody"]


def test_every_api_operation_has_documented_examples():
    schema = create_app(settings=Settings()).openapi()
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            assert operation["summary"] and operation["description"]
            if method in ("post", "patch"):
                assert operation["requestBody"]["content"]["application/json"]["examples"]
            for status, response in operation["responses"].items():
                if status != "204":
                    assert response["content"]["application/json"]["examples"]


@pytest.mark.parametrize("resource", ["tasks", "events"])
def test_openapi_creation_example_works_against_real_api(api, resource):
    operation = api.client.app.openapi()["paths"]["/api/v1/" + resource]["post"]
    body = operation["requestBody"]["content"]["application/json"]["examples"]["default"]["value"]
    assert api.client.post("/api/v1/" + resource, json=body, headers=api.headers).status_code == 201
