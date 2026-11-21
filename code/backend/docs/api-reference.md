# Sprint 1 API reference

Base URL: `http://127.0.0.1:8000/api/v1` for local development. Swagger: `/docs`; ReDoc: `/redoc`; OpenAPI: `/openapi.json` and exported docs/openapi.json. Every operation includes request/response examples, validation and errors. No Flutter code or integration is included.

Public endpoints: health, register, verify-otp, resend-otp, login, refresh. All others require `Authorization: Bearer ACCESS_TOKEN`. Never submit user_id, generated IDs, timestamps, task status, or event source/status. Obtain current user/profile identity from authenticated responses, not a request body.

## Routes and contracts

| Method | URL | Request/example | Success response |
|---|---|---|---|
| GET | `/health` | No body | 200 `{"status":"ok"}` |
| GET | `/health/ready` | No body | 200 `{"status":"ready"}`; restricted DB/RLS check |
| POST | `/auth/register` | Auth example below; optional username/full_name | 201 AuthResponse; session may be null for email confirmation |
| POST | `/auth/verify-otp` | `{"email":"student@example.com","token":"012345"}`; no bearer | 200 AuthResponse with verified session; own profile/default preferences provisioned |
| POST | `/auth/resend-otp` | `{"email":"student@example.com"}`; no bearer | 202 `{"status":"accepted","message":"If this email has an unconfirmed account, a verification code will be sent."}` |
| POST | `/auth/login` | Email/password Auth example | 200 AuthResponse |
| POST | `/auth/refresh` | `{"refresh_token":"REFRESH_TOKEN"}` | 200 AuthResponse with rotated session |
| GET | `/profiles/me` | Bearer, no body | 200 ProfileRead; missing own profile provisioned idempotently |
| PATCH | `/profiles/me` | `{"full_name":"Student Name"}` | 200 updated ProfileRead |
| GET | `/preferences/me` | Bearer, no body | 200 PreferencesRead |
| PATCH | `/preferences/me` | Preferences example below | 200 updated PreferencesRead |
| POST | `/tasks` | Task example below | 201 TaskRead |
| GET | `/tasks` | `?limit=50&offset=0&category=STUDY` | 200 Page of TaskRead |
| GET | `/tasks/{task_id}` | UUID path, no body | 200 TaskRead |
| PATCH | `/tasks/{task_id}` | `{"priority":5}` | 200 updated TaskRead |
| DELETE | `/tasks/{task_id}` | UUID path, no body | 204, empty body |
| POST | `/events` | ONCE/WEEKLY example below | 201 EventRead |
| GET | `/events` | `?limit=50&recurrence_type=WEEKLY` | 200 Page of EventRead (stored rules) |
| GET | `/events/{event_id}` | UUID path, no body | 200 EventRead |
| PATCH | `/events/{event_id}` | `{"title":"Updated lecture"}` | 200 updated EventRead |
| DELETE | `/events/{event_id}` | UUID path, no body | 204, empty body; whole weekly rule |
| PUT | `/energy/sleep-schedule` | Sleep schedule PUT example below | 200 SleepScheduleRead; creates or replaces schedule, always sets is_active=true |
| GET | `/energy/sleep-schedule` | Bearer, no body | 200 SleepScheduleRead; 404 if no schedule exists |
| PATCH | `/energy/sleep-schedule/toggle` | `{"is_active":false}` | 200 updated SleepScheduleRead; 404 if no schedule exists |
| DELETE | `/energy/sleep-schedule` | Bearer, no body | 204, empty body; disables schedule (sets is_active=false), 404 if none |

## Authentication example

Registration checks the existing Supabase Auth accounts before submitting signup. An existing email, confirmed or unconfirmed, returns **409** with `email_already_registered`; use login or resend-otp instead. This server-side lookup requires `SUPABASE_SERVICE_ROLE_KEY` in ignored `.env` and uses it only for the read-only Auth Admin endpoint, never application CRUD. `SUPABASE_PUBLIC_KEY` (or `SUPABASE_ANON_KEY`) remains the key for signup/login/verify/resend. If the private lookup cannot run, registration fails closed with 503 `registration_check_unavailable`. Email comparison is case-insensitive; the filtered lookup is bounded to 100 results and a full unmatched page fails closed. Supabase remains responsible for account uniqueness under concurrent signup. Returning an explicit 409 discloses account existence as requested; apply ingress rate limits to this public route.

`POST /auth/resend-otp` calls Supabase `/auth/v1/resend` with the server-controlled type `signup`. It requires neither an access token nor the private lookup key and performs no application database work. Existing unconfirmed accounts receive a code; confirmed or absent accounts get the same accepted response without mail or account creation. Respect 429/Retry-After; other failures are 422 malformed input, 400 provider rejection, 502 invalid/provider response, and 503 missing public Auth configuration or connectivity. Resend uses the same **Confirm signup** template as initial registration; there is no separate Supabase resend template.

```json
{"email":"student@example.com","password":"EXAMPLE_PASSWORD_REPLACE_ME","full_name":"Student Name"}
```

Only register accepts full_name/username; login accepts email/password. Registration password is 8–1024 characters; login is 1–1024; email has basic format and max 320 validation, with final validation delegated to Supabase. Confirmation-required signup returns `session: null`; submit the emailed code to `/auth/verify-otp`. The token must be a string containing exactly six ASCII digits; leading zeros are preserved. The backend fixes the verification type to `signup`; callers cannot supply another type. Successful verification returns the session below and provisions the own profile/default preferences. Profile metadata is validated and retained on first successful verification or verified login. An already claimed optional username leaves the new profile username null; update it later through profile PATCH.

For the hosted project, keep **Authentication → Sign In / Providers → Email → Confirm email** enabled. Set **Email OTP length** to **6** if the dashboard exposes this setting; the project must issue six-digit codes to match this endpoint. Configure **Email OTP Expiration** to your chosen duration, for example 600 seconds. In **Authentication → Email Templates → Confirm signup**, set the subject to `Your DAYLI verification code` and paste all of [the DAYLI HTML template](email-templates/dayli-signup-otp.html). The template uses `{{ .Token }}` and has no confirmation link. This is signup confirmation; the password login flow remains available. No application database URL, table, policy, trigger, or migration changes are needed. Dashboard settings are manual and were not applied by the agent.

Manual order: register → read the signup email → verify-otp → authorize Swagger with the returned access token → retrieve `/profiles/me` and `/preferences/me`. An incorrect, expired or reused code returns 401 with `invalid_otp`; malformed code/email or extra fields returns 422. Provider throttling returns 429; unavailable Auth/database dependencies return 503, and an invalid/unverifiable provider session returns 502. If Supabase confirms the code but database onboarding fails, log in with the original password once the database is available rather than reusing the consumed code. Supabase owns code generation, expiry and verification; the backend does not store OTPs.

To manually check duplicates and resending: register a new email once; submit register again with that email and expect 409; submit resend-otp with the email and expect 202; verify the code from the received email. After confirmation, register still returns 409 and resend returns 202 without another signup email. Do not interpret accepted as proof that mail was delivered. No real signup, resend, or OTP verification was executed by the agent.

```json
{
  "user":{"id":"00000000-0000-4000-8000-000000000001","email":"student@example.com"},
  "session":{"access_token":"ACCESS_TOKEN","refresh_token":"REFRESH_TOKEN","token_type":"bearer","expires_in":3600,"expires_at":1794823200},
  "confirmation_required":false
}
```

Tokens here are placeholders. Keep them private. Replace the refresh token after each successful refresh; refresh can succeed while application DB is unavailable. Auth APIs and responses use Cache-Control: no-store. Invalid credentials use a generic 401; upstream details are not exposed. Authentication/provider rate limits return 429, with Retry-After when supplied. The backend intentionally leaves rate limiting to Supabase/provider and deployment ingress; it adds no distributed rate-limit store.

## Profile and preferences examples

```json
{"id":"00000000-0000-4000-8000-000000000001","username":null,"full_name":"Student Name","created_at":"2026-11-16T08:00:00Z","updated_at":"2026-11-16T08:00:00Z"}
```

Username is optional, case-sensitive unique, max 100, and cannot be blank/padded. Full name is optional, max 200 and cannot be whitespace-only. Null clears either; owner/ID/timestamps cannot be edited.

Preferences PATCH example:

```json
{"timezone":"Africa/Cairo","notifications_enabled":true,"preferred_work_start":"09:00:00","preferred_work_end":"17:00:00"}
```

Preferences response:

```json
{"preference_id":"00000000-0000-4000-8000-000000000001","user_id":"00000000-0000-4000-8000-000000000001","timezone":"Africa/Cairo","notifications_enabled":true,"preferred_work_start":"09:00:00","preferred_work_end":"17:00:00","updated_at":"2026-11-16T08:00:00Z"}
```

Timezone must be supported IANA/UTC. Notifications is a JSON boolean. Work times use HH:MM:SS without offsets/fractions: both null or both non-null and unequal. Earlier end means overnight. PATCH validates combined old/new values, so clear both endpoints together.

## Task example and validation

```json
{"title":"Finish assignment","task_type":"FLEXIBLE","deadline":"2026-11-20T18:00:00+02:00","estimated_duration_minutes":60,"category":"STUDY","priority":5,"context":"Chapter 2"}
```

TaskRead adds task_id, verified user_id, status UNSCHEDULED, created_at/updated_at, and null fixed endpoints for FLEXIBLE. Every GET/PATCH returns this same shape; lists wrap it in Page. Titles are nonblank/max 500; context nullable/max 500. Categories are STUDY, WORK, PERSONAL, HEALTH, MEETING. Duration is positive integer minutes; priority is integer 1–5 (5 highest), never a calculated score. Deadline is optional and may be in the past; any supplied timestamp needs an offset and is returned in UTC.

FIXED also requires fixed_start_at and fixed_end_at, both offset-aware, end > start. FLEXIBLE requires both null. Switch FIXED to FLEXIBLE atomically:

```json
{"task_type":"FLEXIBLE","fixed_start_at":null,"fixed_end_at":null}
```

Lists additionally filter task_type, priority, deadline_from and deadline_to; deadline bounds are inclusive. Required fields cannot be explicitly null. Status/ID/owner/timestamps are output-only.

## Event examples and validation

ONCE:

```json
{"title":"Lecture","category":"STUDY","location":"Room 2","start_at":"2026-11-16T09:00:00+02:00","end_at":"2026-11-16T10:00:00+02:00"}
```

WEEKLY overnight work/university timetable:

```json
{"title":"Weekly shift","category":"WORK","recurrence_type":"WEEKLY","recurrence_first_date":"2026-11-16","recurrence_until_date":"2026-12-28","recurrence_start_time":"22:00:00","recurrence_end_time":"06:00:00","recurrence_end_day_offset":1,"recurrence_timezone":"Africa/Cairo","weekday":1}
```

EventRead returns the submitted shape with event_id, verified user_id, source MANUAL, status SAVED, created_at/updated_at and every unused shape field null. Weekday is derived (ISO Monday=1..Sunday=7); optional input must match first date. ONCE requires timestamp endpoints and no weekly fields; WEEKLY requires local rule fields and no one-time endpoints. Until is optional and cannot precede first date. Offset 0 is same-day; 1 is next-day/overnight, including explicit 24-hour local intervals. Multi-weekday timetables use separate rules.

First and bounded final endpoints reject DST ambiguity/nonexistence. The backend does not expand all intervening/future occurrences; consumers must resolve each interpreted endpoint in its timezone and validate positive UTC duration. Changing preferences timezone does not move an existing rule. PATCH changes the whole rule and must clear incompatible fields when switching recurrence type. A lecture/shift is an Event and never creates a duplicate Task.

## Sleep schedule examples and validation

PUT request body (`extra='forbid'`, `user_id` forbidden):

```json
{"bedtime":"23:00","wake_time":"07:00","timezone":"Africa/Cairo","repeat_days":[0,1,2,3,4,5,6],"bedtime_reminder_enabled":true,"reminder_minutes_before":15,"wake_alarm_enabled":true}
```

SleepScheduleRead response:

```json
{
  "id":"00000000-0000-4000-8000-000000000001",
  "bedtime":"23:00:00",
  "wake_time":"07:00:00",
  "timezone":"Africa/Cairo",
  "repeat_days":[0,1,2,3,4,5,6],
  "bedtime_reminder_enabled":true,
  "reminder_minutes_before":15,
  "wake_alarm_enabled":true,
  "is_active":true,
  "created_at":"2026-11-21T15:00:00Z",
  "updated_at":"2026-11-21T15:00:00Z"
}
```

`PUT /energy/sleep-schedule` creates or replaces the caller's schedule (one row per user; safe under concurrent requests relying on `uq_sleep_schedules_user_id`). PUT always sets `is_active=true` (re-enabling a disabled schedule).

Validation:
- `bedtime` and `wake_time` are `HH:MM(:SS)` local wall times without UTC offset or microseconds, and must differ. Overnight schedules (`bedtime > wake_time`) are valid.
- `timezone` must be a valid IANA timezone name without leading or trailing spaces.
- `repeat_days` is a non-empty list of strict integers `0..6`, deduplicated and stored sorted.
- `reminder_minutes_before` between 0 and 180 (default 15).
- `bedtime_reminder_enabled` and `wake_alarm_enabled` are strict booleans (default true).
- Extra fields and `user_id` in the body are rejected with 422.

Toggle and disable:
- `PATCH /energy/sleep-schedule/toggle` accepts `{"is_active": bool}` (strict boolean). Returns updated `SleepScheduleRead`; returns 404 if no schedule exists.
- `DELETE /energy/sleep-schedule` disables the schedule (sets `is_active=false`, keeps settings). Returns 204 with empty body. Idempotent on already-disabled schedule; returns 404 if no schedule exists.

## Pagination and errors

```json
{"items":[],"limit":50,"offset":0,"has_more":false}
```

Limit 1–100, default 50; offset 0–1,000,000. Next offset is previous offset plus items.length. No COUNT query is used. Empty lists are 200. Task ordering is deadline/null-last then ID; event ordering uses relevant first/start date for recurrence-filtered lists, otherwise created_at then ID. No related profile/preferences collections are loaded.

All errors follow:

```json
{"error":{"code":"invalid_request","message":"Request validation failed","details":[{"field":"body.priority","message":"Input should be less than or equal to 5"}]}}
```

Possible responses: 400 registration rejected; 401 missing/invalid/expired token or credentials; 404 missing/other-user resource; 409 uniqueness/profile conflict; 422 request or merged-record validation; 429 Auth rate limit; 502 Auth failure/invalid upstream session; 503 configuration/DB/Auth unavailable; 500 unexpected error. Invalid bearer responses include WWW-Authenticate: Bearer. Passwords, token values, raw request inputs, SQL and provider errors are excluded. Persisted text cannot contain U+0000.

## Curl usage

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/auth/login -H 'Content-Type: application/json' --data-binary '@login.json'
curl.exe http://127.0.0.1:8000/api/v1/profiles/me -H 'Authorization: Bearer REPLACE_ACCESS_TOKEN'
curl.exe -X POST http://127.0.0.1:8000/api/v1/tasks -H 'Authorization: Bearer REPLACE_ACCESS_TOKEN' -H 'Content-Type: application/json' --data-binary '@task.json'
curl.exe 'http://127.0.0.1:8000/api/v1/events?recurrence_type=WEEKLY&limit=50' -H 'Authorization: Bearer REPLACE_ACCESS_TOKEN'
curl.exe -X PUT http://127.0.0.1:8000/api/v1/energy/sleep-schedule -H 'Authorization: Bearer REPLACE_ACCESS_TOKEN' -H 'Content-Type: application/json' --data-binary '@sleep_schedule.json'
curl.exe http://127.0.0.1:8000/api/v1/energy/sleep-schedule -H 'Authorization: Bearer REPLACE_ACCESS_TOKEN'
```

Store example request JSON in temporary local files, keep real passwords/tokens out of source control, and use Swagger's Authorize button for interactive tests. The backend APIs are ready for teammate integration; no client-side implementation is supplied.
