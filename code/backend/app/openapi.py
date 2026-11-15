"""Executable request/response examples for the frontend API contract."""

from copy import deepcopy

from fastapi.openapi.utils import get_openapi

from app.schemas import EventUpdate, PreferencesUpdate, TaskUpdate

ID = "00000000-0000-4000-8000-000000000001"
STAMP = "2026-11-16T08:00:00Z"
TASK_INPUT = {"title": "Assignment", "task_type": "FLEXIBLE", "estimated_duration_minutes": 30, "category": "STUDY", "priority": 3}
ONCE_INPUT = {"title": "Lecture", "category": "STUDY", "start_at": "2026-11-16T09:00:00Z", "end_at": "2026-11-16T10:00:00Z"}
WEEKLY_INPUT = {"title": "Work shift", "category": "WORK", "recurrence_type": "WEEKLY", "recurrence_first_date": "2026-11-16",
    "recurrence_start_time": "22:00:00", "recurrence_end_time": "06:00:00", "recurrence_end_day_offset": 1,
    "recurrence_timezone": "Africa/Cairo", "weekday": 1}
PROFILE = {"id": ID, "username": None, "full_name": "Student", "created_at": STAMP, "updated_at": STAMP}
PREFERENCES = {"preference_id": ID, "user_id": ID, "timezone": "UTC", "notifications_enabled": False,
    "preferred_work_start": None, "preferred_work_end": None, "updated_at": STAMP}
TASK = TASK_INPUT | {"task_id": ID, "user_id": ID, "status": "UNSCHEDULED", "deadline": None,
    "fixed_start_at": None, "fixed_end_at": None, "context": None, "created_at": STAMP, "updated_at": STAMP}
EVENT = ONCE_INPUT | {"event_id": ID, "user_id": ID, "location": None, "recurrence_type": "ONCE", "source": "MANUAL",
    "status": "SAVED", "recurrence_first_date": None, "recurrence_until_date": None, "recurrence_start_time": None,
    "recurrence_end_time": None, "recurrence_end_day_offset": None, "recurrence_timezone": None, "weekday": None,
    "created_at": STAMP, "updated_at": STAMP}
AUTH = {"user": {"id": ID, "email": "student@example.com"}, "confirmation_required": False,
    "session": {"access_token": "REPLACE_WITH_ACCESS_TOKEN", "refresh_token": "REPLACE_WITH_REFRESH_TOKEN",
                "token_type": "bearer", "expires_in": 3600, "expires_at": 1794823200}}


def examples(value):
    return {"default": {"summary": "Example", "value": deepcopy(value)}}


def build_openapi(application):
    if application.openapi_schema:
        return application.openapi_schema
    schema = get_openapi(title=application.title, version=application.version,
                         description=application.description, routes=application.routes)
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            body = result = None
            if path.startswith("/api/v1/health"):
                result = {"status": "ready" if path.endswith("/ready") else "ok"}
                operation["description"] = "Public endpoint. " + ("Checks the restricted database role, RLS and absence of leaked identity; returns 503 if unavailable." if path.endswith("/ready") else "Checks process liveness without contacting the database or authentication provider.")
            elif path.startswith("/api/v1/auth/"):
                result = AUTH
                if path.endswith("/resend-otp"):
                    result = {"status": "accepted", "message": "If this email has an unconfirmed account, a verification code will be sent."}
                body = ({"email": "student@example.com"} if path.endswith("/resend-otp") else
                        {"email": "student@example.com", "token": "012345"} if path.endswith("/verify-otp") else
                        {"refresh_token": "REPLACE_WITH_REFRESH_TOKEN"} if path.endswith("/refresh") else
                        {"email": "student@example.com", "password": "EXAMPLE_PASSWORD_REPLACE_ME"})
            elif path == "/api/v1/profiles/me":
                result, body = PROFILE, {"full_name": "Student Name"}
            elif path == "/api/v1/preferences/me":
                result, body = PREFERENCES, {"timezone": "Africa/Cairo", "preferred_work_start": "09:00:00", "preferred_work_end": "17:00:00"}
            elif path.startswith("/api/v1/tasks"):
                result, body = TASK, TASK_INPUT if method == "post" else {"priority": 5}
            elif path.startswith("/api/v1/events"):
                result, body = EVENT, ONCE_INPUT if method == "post" else {"title": "Updated commitment"}
            if result is None:
                continue  # Module authors provide their own documented examples.
            if method in ("post", "patch"):
                operation["requestBody"]["content"]["application/json"]["examples"] = examples(body)
                if path == "/api/v1/events" and method == "post":
                    operation["requestBody"]["content"]["application/json"]["examples"]["weekly"] = {"summary": "Overnight weekly timetable", "value": deepcopy(WEEKLY_INPUT)}
            if method == "get" and path in ("/api/v1/tasks", "/api/v1/events"):
                result = {"items": [result], "limit": 50, "offset": 0, "has_more": False}
            for status, response in operation["responses"].items():
                if status == "204":
                    continue
                media = response.setdefault("content", {}).setdefault("application/json", {})
                if status.startswith("2"):
                    media["examples"] = examples(result)
                    if path.endswith("/register"):
                        media["examples"]["confirmation"] = {"summary": "Email confirmation required", "value": AUTH | {"session": None, "confirmation_required": True}}
                else:
                    code = {"401": "invalid_token", "404": "not_found", "409": "conflict", "422": "invalid_request",
                            "429": "auth_rate_limited", "503": "backend_unavailable"}.get(status, "request_failed")
                    if path.endswith("/verify-otp") and status == "401":
                        code = "invalid_otp"
                        response["description"] = "Invalid, expired or already used signup verification code"
                    if path.endswith("/register") and status == "409":
                        code = "email_already_registered"
                        response["description"] = "Email is already registered; log in or resend the signup verification code"
                    media["examples"] = examples({"error": {"code": code, "message": response["description"], "details": []}})
                if status == "401" and not path.endswith("/verify-otp"):
                    response["headers"] = {"WWW-Authenticate": {"schema": {"type": "string"}, "description": "Bearer for invalid/missing access tokens"}}
                elif status == "429":
                    response["headers"] = {"Retry-After": {"schema": {"type": "string"}, "description": "Retry delay in seconds when supplied by the Auth provider"}}
            for parameter in operation.get("parameters", []):
                if parameter["name"] in ("task_id", "event_id"):
                    parameter["example"] = ID
                elif parameter["name"] in ("limit", "offset"):
                    parameter["example"] = 50 if parameter["name"] == "limit" else 0
    # Omission is allowed for PATCH, explicit null is forbidden for required values.
    for contract in (TaskUpdate, EventUpdate, PreferencesUpdate):
        properties = schema["components"]["schemas"][contract.__name__]["properties"]
        for name in contract.required_values:
            field = properties[name]
            choices = [choice for choice in field.get("anyOf", []) if choice.get("type") != "null"]
            if len(choices) == 1:
                field.pop("anyOf", None)
                field.pop("default", None)
                field.update(choices[0])
    application.openapi_schema = schema
    return schema
