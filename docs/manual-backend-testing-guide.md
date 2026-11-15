# Manual backend testing guide

This guide is for you to run manually using PowerShell, a browser, and Swagger UI. Its source is the existing [OpenAPI export](../code/backend/docs/openapi.json), [API reference](../code/backend/docs/api-reference.md), and request/response schemas in `code/backend/app/modules/user/schemas.py`, `code/backend/app/modules/scheduler/schemas.py`, and `code/backend/app/core/schemas.py`. Creating this guide does not run any of these commands or requests.

All example emails, passwords, tokens, UUIDs, connection details, and timestamps are illustrative. Replace them privately when needed. Never send passwords, database connection strings containing passwords, refresh/access tokens, or private signing keys in chat.

## 1. Prerequisites and testing order

You need Windows PowerShell, a browser, Python 3.12 or newer, the backend dependencies, and an existing configured database. The current workspace already has a virtual environment and local PostgreSQL setup. Reuse them; do not initialize/reset a database or reapply migrations for this guide.

There are two distinct testing environments:

| Environment | What you can do | What it requires |
|---|---|---|
| Native local PostgreSQL, without real Supabase Auth | Start FastAPI; open Swagger/OpenAPI; use public health endpoints; check missing-token errors and schema-only validation errors | Existing local database/schema and restricted runtime credentials; no Supabase token |
| Backend with real Supabase Auth | Successful registration/login/refresh, profiles/preferences, Task/Event CRUD, filtering and two-user isolation | Configured local Supabase stack or an explicitly chosen non-production Supabase project; application schema and runtime role already prepared in that same database |

The native local database's minimal Auth fixture is not a real login service. A hosted Supabase token alone does not make that local database ready for authenticated CRUD: its `auth.users` identities must match the Auth service. Do not manually copy Auth users or create another users table. Use a matched local Supabase Auth/database stack or the same hosted project's Auth/database for real-user testing.

Recommended order:

1. Prepare the environment and check local connectivity.
2. Start FastAPI, open Swagger, and check health.
3. Register User A, verify the emailed signup OTP, then test login and refresh.
4. Authorize Swagger; retrieve/update A's profile and preferences.
5. Create/read/list/update Tasks, keeping their IDs.
6. Create/read/list/update ONCE and WEEKLY Events, keeping their IDs.
7. Test pagination, filters, and invalid inputs.
8. Register/login User B and check isolation in both directions.
9. Inspect persistence; then delete only records you created for this manual session.
10. Stop FastAPI.

Use distinctive titles such as `Manual A assignment` and `Manual A lecture`. Keep manual test data separate from existing data. A DELETE endpoint permanently deletes its target, so leave deletion until the final cleanup step.

## 2. PowerShell environment setup

From the repository root:

```powershell
Set-Location -LiteralPath 'code/backend'
```

On a fresh machine only, if the virtual environment/dependencies are absent:

```powershell
py -3 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock
```

An existing `.env` must be preserved. If it is absent, copy `.env.example` once and fill in private values locally:

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

Configuration responsibilities:

| Variable | Meaning |
|---|---|
| `APP_ENV` | `development` for local work. Only explicit development permits loopback HTTP Supabase Auth. |
| `DATABASE_URL` | Existing restricted PostgreSQL runtime LOGIN; never postgres/table-owner/service-role credentials for CRUD. |
| `MIGRATION_DATABASE_URL` | Separate migration/admin connection. Not used for these manual API requests. |
| `SUPABASE_URL` | Trusted Auth project origin, such as `https://REPLACE_PROJECT_REF.supabase.co`; local Supabase uses its actual loopback origin. |
| `SUPABASE_PUBLIC_KEY` | The same project's publishable key or legacy anon key. |
| `SUPABASE_JWT_AUDIENCE` | `authenticated`. |
| `CORS_ORIGINS` | Optional exact browser origins; Swagger served by this backend does not need a separate frontend origin. |
| `BACKEND_MODULES` | Existing optional module registration; leave its current value alone. |

The backend loads `code/backend/.env`. Already set process environment variables override `.env`, so use the same environment in each relevant terminal and restart FastAPI after changing configuration. Do not print the complete environment or `.env` into a transcript.

For native local health/docs use without Auth, the Auth variables can remain unset; do not leave fake credential values in them. Successful authenticated operations require real configuration. No authentication bypass is available.

A local runtime URL has this form; use your existing actual database name, port, and restricted role:

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+psycopg://REPLACE_RESTRICTED_ROLE:URL_ENCODED_PRIVATE_PASSWORD@127.0.0.1:55432/REPLACE_LOCAL_DB_NAME
```

For remote PostgreSQL, the implementation requires `sslmode=verify-full` and a trusted certificate configuration. It rejects transaction-pooler port 6543; use direct or verified session-mode connectivity on port 5432. Configuration is explained further in the [backend README](../code/backend/README.md). If schema/runtime-role preparation is missing, pause authenticated testing and arrange that setup separately; this guide contains no schema-changing commands.

## 3. Check the existing local database

Before running the following yourself, privately confirm `DATABASE_URL` targets `127.0.0.1`, `localhost`, or `::1`. This section is for the existing local database, not a hosted Supabase connection.

```powershell
& '.\.venv\Scripts\python.exe' -m app.core.database_check
```

Expected successful output:

```text
Connection OK; restricted role and RLS prerequisites verified; no scoped user identity.
```

This checks runtime connectivity/role prerequisites; it does not prove two-user isolation. Failure can mean the server is stopped, connection details are wrong, the application schema is absent, or the runtime role is unsafe. Do not replace restricted credentials with admin credentials to get a pass.

If using the existing portable local PostgreSQL installation:

```powershell
& '.\.venv\Scripts\python.exe' -m scripts.local_postgres status
# Only if the existing server is stopped:
& '.\.venv\Scripts\python.exe' -m scripts.local_postgres start
```

If the script fails but the connectivity command succeeds, the database is reachable. Do not initialize another cluster or reset the current one.

## 4. Start FastAPI and open Swagger

In the backend PowerShell terminal:

```powershell
& '.\.venv\Scripts\python.exe' -m app.core.database_check& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Leave that terminal open. Expected startup includes `Application startup complete` and the listening address. If port 8000 is occupied, choose another unused port and substitute it throughout the guide.

Open these addresses in your browser:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

In Swagger, expand an operation, click **Try it out**, replace the example/query/path values, then click **Execute**. Read the **Server response** status/body. The displayed documented response examples are not execution results.

All endpoint paths below include `/api/v1`.

### Public health endpoints: no Supabase token needed

| Order | Method/path | Authentication | Request body | Expected success | Expected failures |
|---|---|---|---|---|---|
| First | `GET /api/v1/health` | None | None | 200, `{"status":"ok"}` | Connection refused if FastAPI is not running; unexpected server failure 500 |
| Second | `GET /api/v1/health/ready` | None | None | 200, `{"status":"ready"}` | 503 for unavailable/misconfigured database or unsafe runtime role |

In a second PowerShell terminal:

```powershell
$manualBase = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod -Method Get -Uri "$manualBase/health"
Invoke-RestMethod -Method Get -Uri "$manualBase/health/ready"
```

Liveness can succeed while readiness fails. Successful readiness does not establish Auth configuration or user isolation.

## 5. Obtain a real Supabase access token yourself

Use a test account in your chosen local/non-production Supabase environment. You cannot use a project API key, database password, dashboard login, user UUID, or refresh token as an access token.

Preferred flow:

1. Configure the matching `SUPABASE_URL`, public key, and database privately.
2. Check in the Supabase dashboard that the Email provider/password login is enabled. Dashboard labels vary; look under **Authentication** for providers/sign-in settings.
3. Use `POST /api/v1/auth/register` below with an email address you control.
4. If `confirmation_required` is true, read the six-digit code from the signup email and submit it to `POST /api/v1/auth/verify-otp` as described below. Install the DAYLI Confirm signup template and keep Confirm email enabled first. For local Supabase, use the local mail interface reported by its existing setup.
5. Call `POST /api/v1/auth/login`.
6. Copy `session.access_token` from the successful backend response privately. Keep `session.refresh_token` for the refresh test.

The dashboard's **Authentication → Users** view can show that your account exists; it does not provide that user's current login session/access token.

Alternative if backend login is blocked by application database setup: obtain a token directly from Supabase Auth yourself, using the same project's public key and your test-account email/password. This is an Auth HTTP request, not a backend endpoint, and its response uses Supabase's own top-level token fields:

```powershell
$manualSupabaseUrl = Read-Host 'Supabase project origin, without /auth/v1'
$manualPublicKey = Read-Host 'Publishable or anon key'
$manualEmail = Read-Host 'Your confirmed test account email'
$manualSecret = Read-Host 'Test account password' -AsSecureString
$manualPassword = [System.Net.NetworkCredential]::new('', $manualSecret).Password
$manualAuthBody = @{ email = $manualEmail; password = $manualPassword } | ConvertTo-Json
$manualDirectSession = Invoke-RestMethod -Method Post `
    -Uri "$($manualSupabaseUrl.TrimEnd('/'))/auth/v1/token?grant_type=password" `
    -Headers @{ apikey = $manualPublicKey } `
    -ContentType 'application/json' -Body $manualAuthBody
$manualAccessToken = $manualDirectSession.access_token
$manualRefreshToken = $manualDirectSession.refresh_token
Remove-Variable manualPassword, manualAuthBody, manualSecret
```

Do not print `$manualDirectSession`, record the command in a transcript, or save tokens in tracked files. Invalid credentials/unconfirmed accounts are rejected by Supabase. Obtaining a token this way does not repair a missing application schema/runtime role or mismatched database.

The existing backend accepts **ES256/RS256** access-token signing and verifies the trusted project's JWKS, issuer, audience, role, expiration, and user UUID. It rejects legacy HS256 tokens. Check the active signing algorithm in **Authentication → JWT Signing Keys** or the corresponding signing-key settings. An incompatible signing setup needs a separate planned configuration change; do not share the JWT secret/private key or generate a substitute token.

## 6. Registration, signup OTP verification, login, and refresh

These routes require a real configured Supabase Auth service for successful responses. They do not require an existing bearer access token in the request. Refresh requires the private refresh token in its body.

### A. `POST /api/v1/auth/register`

Authentication: none. Run before login when creating a new test account. Use an email you control; `student-a@example.com` below is a placeholder.

```json
{
  "email": "student-a@example.com",
  "password": "REPLACE_WITH_PRIVATE_TEST_PASSWORD",
  "username": "manual_student_a",
  "full_name": "Manual Student A"
}
```

Expected success: **201**, `AuthResponse`. When email confirmation is required:

```json
{
  "user": {
    "id": "00000000-0000-4000-8000-000000000001",
    "email": "student-a@example.com"
  },
  "session": null,
  "confirmation_required": true
}
```

When confirmation is not required and a verified session is returned, the response has the session shape shown under login. No application profile is created until a verified session is available. New verified onboarding creates the profile and default preferences; existing profile details are preserved. If the optional username is already claimed, onboarding can leave it null rather than blocking login.

Failures: 422 for malformed email, password shorter than 8 or longer than 1024 characters, invalid profile text, or extra fields; 400 when Supabase rejects registration; 429 for provider rate limits; 502 for provider failure/unverifiable returned session; 503 for missing Auth configuration/provider connectivity or required database unavailability. Existing confirmed or pending emails return 409 `email_already_registered` after a private Auth Admin lookup. Set `SUPABASE_SERVICE_ROLE_KEY` privately in `.env` for this lookup; it is never used for JWT verification or application CRUD. Missing/unavailable lookup credentials return 503 `registration_check_unavailable`; no signup is attempted. A missing Auth user in the configured application database can also produce 409 during onboarding.

### B. `POST /api/v1/auth/verify-otp`

Authentication: none. Run after registration returns `confirmation_required: true`, using the same email and the code from the **Confirm signup** email. Supabase Auth must be configured to issue six-digit codes. Paste [the DAYLI template](../code/backend/docs/email-templates/dayli-signup-otp.html) into **Authentication → Email Templates → Confirm signup**, keep **Confirm email** enabled, and set **Email OTP length** to 6 if this setting is available. Configure expiry in **Email OTP Expiration**; the template does not promise a fixed expiry time.

```json
{
  "email": "student-a@example.com",
  "token": "012345"
}
```

The example code is a placeholder. Enter your actual code as a string; preserve leading zeros. Do not supply a password, user ID or type. The backend sets `type: signup` itself and uses Supabase's `verifyOtp` HTTP endpoint. It does not store the code.

Expected success: **200**, the same `AuthResponse` shown under login, with `confirmation_required: false` and a verified session. The own profile and default preferences are provisioned through the existing transaction. Copy the returned access token privately into Swagger Authorize; verification already establishes the session, while login can be tested separately afterwards.

Failures: **401** with `invalid_otp` for an incorrect, expired or consumed code; **422** for malformed email, a non-string code, anything other than six ASCII digits, or extra fields; **429** for provider throttling; **502** for invalid/unverifiable Auth responses; **503** for missing/unavailable dependencies. If Supabase consumes the code but database onboarding fails, retry login with the original password after the database is available. Reusing the code will not fix database configuration.

This step requires real Supabase Auth for success. You may check malformed input locally without a valid token. It does not require any database migration or schema change.

### C. `POST /api/v1/auth/resend-otp`

Authentication: none. After registering an unconfirmed account, submit the same email without a password, token, or type:

```json
{"email":"student-a@example.com"}
```

Expected success: **202**:

```json
{"status":"accepted","message":"If this email has an unconfirmed account, a verification code will be sent."}
```

Supabase sends a signup confirmation code only for an existing unconfirmed account. Confirmed or unknown emails return the same accepted response without signup mail or account creation. The endpoint has no database onboarding or session. Verify the received code through `/auth/verify-otp`. Signup and resend use the same **Confirm signup** template; paste the updated DAYLI HTML there, following [the template guide](../code/backend/docs/email-templates/README.md).

Failures: 422 malformed email/extra fields, 400 provider rejection, 429 provider rate limits (respect Retry-After), 502 provider/invalid response, and 503 missing public Auth configuration/connectivity. Resend requires the public key and does not require `SUPABASE_SERVICE_ROLE_KEY`.

To check duplicate handling, register once and repeat the registration before confirmation: expect 409, then use resend instead. After confirmation, repeat registration and expect 409 again. Email checks ignore case. Provider account uniqueness remains authoritative for concurrent requests.

### D. `POST /api/v1/auth/login`

Authentication: none. Run after any required confirmation.

```json
{
  "email": "student-a@example.com",
  "password": "REPLACE_WITH_PRIVATE_TEST_PASSWORD"
}
```

Expected success: **200**:

```json
{
  "user": {
    "id": "00000000-0000-4000-8000-000000000001",
    "email": "student-a@example.com"
  },
  "session": {
    "access_token": "REPLACE_WITH_ACCESS_TOKEN",
    "refresh_token": "REPLACE_WITH_REFRESH_TOKEN",
    "token_type": "bearer",
    "expires_in": 3600,
    "expires_at": 1794823200
  },
  "confirmation_required": false
}
```

UUIDs, tokens, expiration values, and email come from your real session; `expires_at` can be null. Login accepts only email/password, not registration profile fields. It provisions a missing own profile/default preferences after verification.

Failures: 401 for incorrect credentials or an unconfirmed/rejected account; 422 for invalid email, empty password, password over 1024 characters, or extra fields; 429/502/503 for provider/configuration/dependency failures; 409 if onboarding cannot find the matching Auth user in the application database.

Optional PowerShell login without displaying the password/token response:

```powershell
$manualBase = 'http://127.0.0.1:8000/api/v1'
$manualEmail = Read-Host 'Your confirmed test account email'
$manualSecret = Read-Host 'Test account password' -AsSecureString
$manualPassword = [System.Net.NetworkCredential]::new('', $manualSecret).Password
$manualLoginBody = @{ email = $manualEmail; password = $manualPassword } | ConvertTo-Json
$manualLogin = Invoke-RestMethod -Method Post -Uri "$manualBase/auth/login" `
    -ContentType 'application/json' -Body $manualLoginBody
$manualAccessToken = $manualLogin.session.access_token
$manualRefreshToken = $manualLogin.session.refresh_token
$manualHeaders = @{ Authorization = 'Bearer ' + $manualAccessToken }
Remove-Variable manualPassword, manualLoginBody, manualSecret
```

### E. `POST /api/v1/auth/refresh`

Authentication: no bearer token required; provide the current refresh token. Run after successful login and replace the saved tokens before proceeding to CRUD.

```json
{
  "refresh_token": "REPLACE_WITH_CURRENT_REFRESH_TOKEN"
}
```

Expected success: **200**, the same `AuthResponse` shape as login, with a verified session and rotated token values. Save the newly returned access and refresh tokens. Refresh can succeed without application database availability; it does not perform database onboarding.

Failures: 422 for an empty refresh token, over 4096 characters, or extra fields; 401 for a rejected/expired/revoked refresh token; 429 for rate limits; 502 for invalid provider response/unverifiable session; 503 for missing Auth configuration or unavailable provider. Do not assume immediate reuse of an older token always fails; Supabase can apply a reuse grace interval.

PowerShell:

```powershell
$manualRefreshBody = @{ refresh_token = $manualRefreshToken } | ConvertTo-Json
$manualRefreshed = Invoke-RestMethod -Method Post -Uri "$manualBase/auth/refresh" `
    -ContentType 'application/json' -Body $manualRefreshBody
$manualAccessToken = $manualRefreshed.session.access_token
$manualRefreshToken = $manualRefreshed.session.refresh_token
$manualHeaders = @{ Authorization = 'Bearer ' + $manualAccessToken }
Remove-Variable manualRefreshBody
```

## 7. Configure authentication in Swagger

1. Click **Authorize** near the top of Swagger.
2. In the HTTP bearer input, paste only your actual access token. Do not include the word `Bearer`; Swagger adds it to the header.
3. Click **Authorize**, then **Close**.
4. Execute `GET /api/v1/profiles/me` to establish/retrieve your own profile before creating Tasks/Events.

The HTTP header used by PowerShell or another client is:

```text
Authorization: Bearer REPLACE_WITH_ACCESS_TOKEN
```

After refresh, replace the token in Swagger too. Use **Logout** inside the authorization dialog to test missing authentication or change users. Reauthorize when the access token expires. Never paste the refresh token or project public key into the bearer input.

Protected operations below require this verified access token and the matching configured database. With no bearer token, they return 401. With valid-looking credentials but missing Auth/database configuration, they can return 503 instead; do not interpret that as an ownership result.

## 8. Profile and preferences

Run these before scheduling inputs. All four routes require User A's real bearer access token.

| Order | Method/path | Example request body | Expected success | Expected failures |
|---|---|---|---|---|
| 1 | `GET /api/v1/profiles/me` | None | 200 `ProfileRead` below; a missing own profile is provisioned idempotently | 401 invalid/missing/expired token; 409 missing matching Auth user; 503 configuration/DB unavailable |
| 2 | `PATCH /api/v1/profiles/me` | `{"full_name":"Manual Student A Updated","username":"manual_student_a"}` | 200 complete updated `ProfileRead` | 422 invalid/extra/ownership fields; 409 username conflict; 401/503 authentication/dependency errors |
| 3 | `GET /api/v1/preferences/me` | None | 200 `PreferencesRead` below | 401; 409 profile/onboarding dependency conflict; 503 configuration/DB unavailable |
| 4 | `PATCH /api/v1/preferences/me` | `{"timezone":"Africa/Cairo","notifications_enabled":true,"preferred_work_start":"09:00:00","preferred_work_end":"17:00:00"}` | 200 complete updated `PreferencesRead` | 422 invalid timezone/boolean/combined work interval/extra fields; 401/409/503 as above |

Example `ProfileRead`:

```json
{
  "id": "00000000-0000-4000-8000-000000000001",
  "username": "manual_student_a",
  "full_name": "Manual Student A Updated",
  "created_at": "2026-11-16T08:00:00Z",
  "updated_at": "2026-11-16T08:05:00Z"
}
```

Example `PreferencesRead` after PATCH:

```json
{
  "preference_id": "00000000-0000-4000-8000-000000000004",
  "user_id": "00000000-0000-4000-8000-000000000001",
  "timezone": "Africa/Cairo",
  "notifications_enabled": true,
  "preferred_work_start": "09:00:00",
  "preferred_work_end": "17:00:00",
  "updated_at": "2026-11-16T08:05:00Z"
}
```

For a newly created profile, preferences initially use UTC, notifications false, and both work endpoints null. Profile username/full_name can be cleared with null. Username is case-sensitive unique, at most 100 characters, and cannot be blank or padded; full_name is at most 200 and cannot be whitespace-only.

Preferences timezone must be supported IANA/UTC. Notifications must be a JSON boolean. Work endpoints use local whole-second times without offsets. Both must be null or both supplied and unequal; an earlier end means an overnight window. To clear the window, PATCH both fields together:

```json
{"preferred_work_start":null,"preferred_work_end":null}
```

After each PATCH, run the corresponding GET and check persistence. Save your returned profile `id` privately for the two-user check.

PowerShell GET example:

```powershell
Invoke-RestMethod -Method Get -Uri "$manualBase/profiles/me" -Headers $manualHeaders
Invoke-RestMethod -Method Get -Uri "$manualBase/preferences/me" -Headers $manualHeaders
```

## 9. Task CRUD

Every Task endpoint requires a real bearer access token. First complete profile retrieval. Save the returned `task_id`; replace `{task_id}` with that actual UUID, without braces.

### Request examples

FLEXIBLE creation body:

```json
{
  "title": "Manual A assignment",
  "task_type": "FLEXIBLE",
  "estimated_duration_minutes": 60,
  "category": "STUDY",
  "priority": 5,
  "deadline": "2026-11-20T18:00:00+02:00",
  "context": "Manual testing chapter"
}
```

FIXED creation body for a second Task:

```json
{
  "title": "Manual A fixed study session",
  "task_type": "FIXED",
  "estimated_duration_minutes": 60,
  "category": "STUDY",
  "priority": 3,
  "fixed_start_at": "2026-11-16T09:00:00+02:00",
  "fixed_end_at": "2026-11-16T10:00:00+02:00"
}
```

Dates can be adjusted for your manual session; a past deadline is permitted by the implementation. Timestamps need UTC offsets and are returned in UTC. Categories are `STUDY`, `WORK`, `PERSONAL`, `HEALTH`, `MEETING`; importance is the integer field `priority`, 1-5. Estimated duration is a positive integer, at most 2147483647 minutes. FIXED endpoints must be present with end after start. FLEXIBLE endpoints must be null/omitted. Duration is not required to equal the fixed interval length.

### Endpoint sequence

| Order | Method/path | Example request body | Expected success | Expected failures |
|---|---|---|---|---|
| 1 | `POST /api/v1/tasks` | FLEXIBLE or FIXED body above | 201 complete `TaskRead` below | 422 invalid type/category/priority/duration/interval or forbidden fields; 409 missing profile; 401/503 authentication/dependency errors |
| 2 | `GET /api/v1/tasks/{task_id}` | None | 200 complete stored `TaskRead` | 404 missing/other-user ID; 422 malformed UUID; 401/503 |
| 3 | `GET /api/v1/tasks` | None; query example `?limit=2&offset=0&category=STUDY` | 200 page of complete `TaskRead` objects | 422 invalid query values/bounds; 401/503 |
| 4 | `PATCH /api/v1/tasks/{task_id}` | `{"title":"Manual A assignment updated","priority":4}` | 200 complete updated `TaskRead` | 404 missing/other-user ID; 422 invalid merged record/UUID/forbidden fields; 401/503 |
| Final cleanup | `DELETE /api/v1/tasks/{task_id}` | None | 204, empty body | 404 missing/other-user/already deleted ID; 422 malformed UUID; 401/503 |

Example `TaskRead` for the FLEXIBLE creation:

```json
{
  "title": "Manual A assignment",
  "task_type": "FLEXIBLE",
  "estimated_duration_minutes": 60,
  "category": "STUDY",
  "priority": 5,
  "deadline": "2026-11-20T16:00:00Z",
  "fixed_start_at": null,
  "fixed_end_at": null,
  "context": "Manual testing chapter",
  "task_id": "00000000-0000-4000-8000-000000000002",
  "user_id": "00000000-0000-4000-8000-000000000001",
  "status": "UNSCHEDULED",
  "created_at": "2026-11-16T08:00:00Z",
  "updated_at": "2026-11-16T08:00:00Z"
}
```

GET and PATCH use this same full response shape; changed values and timestamps reflect your actual record. Status is always server-managed `UNSCHEDULED` in Sprint 1. Do not submit ID, owner, status, or timestamps.

PATCH validates the combined stored/new record. For a FIXED Task, changing only its type to FLEXIBLE fails while stored fixed endpoints remain. This atomic switch succeeds:

```json
{"task_type":"FLEXIBLE","fixed_start_at":null,"fixed_end_at":null}
```

Omitted fields stay unchanged; null clears nullable fields such as deadline/context. Required values cannot be set to null. After PATCH, GET the Task again. After final deletion, GET it again and expect 404.

PowerShell creation example, using your existing `$manualHeaders`:

```powershell
$manualTaskBody = @{
    title = 'Manual A assignment'
    task_type = 'FLEXIBLE'
    estimated_duration_minutes = 60
    category = 'STUDY'
    priority = 5
} | ConvertTo-Json
$manualTask = Invoke-RestMethod -Method Post -Uri "$manualBase/tasks" `
    -Headers $manualHeaders -ContentType 'application/json' -Body $manualTaskBody
$manualTaskId = $manualTask.task_id
Invoke-RestMethod -Method Get -Uri "$manualBase/tasks/$manualTaskId" -Headers $manualHeaders
```

## 10. Event CRUD: ONCE and WEEKLY

Every Event endpoint requires a real bearer access token. Lectures/work shifts are Events and do not create duplicate Tasks. Save each returned `event_id`; use actual UUIDs in path inputs.

### ONCE creation body

```json
{
  "title": "Manual A lecture",
  "category": "STUDY",
  "location": "Room 2",
  "recurrence_type": "ONCE",
  "start_at": "2026-11-16T09:00:00+02:00",
  "end_at": "2026-11-16T10:00:00+02:00"
}
```

ONCE is the default if recurrence_type is omitted. End must follow start, timestamps need offsets, and weekly fields must be null/omitted.

### WEEKLY timetable creation body

```json
{
  "title": "Manual A weekly work shift",
  "category": "WORK",
  "location": "Office",
  "recurrence_type": "WEEKLY",
  "recurrence_first_date": "2026-11-16",
  "recurrence_until_date": "2026-12-28",
  "recurrence_start_time": "22:00:00",
  "recurrence_end_time": "06:00:00",
  "recurrence_end_day_offset": 1,
  "recurrence_timezone": "Africa/Cairo",
  "weekday": 1
}
```

November 16, 2026 is Monday, ISO weekday 1. If you change the first date, update weekday to match or omit weekday and let the backend derive it. ISO weekdays run Monday=1 through Sunday=7. For a lecture on Monday and Wednesday, create two separate weekly rules.

WEEKLY requires first date, local whole-second start/end times, end-day offset, and timezone. Until is optional but cannot precede first date. Use offset 0 for a same-day interval with end later than start; offset 1 supports overnight intervals. ONCE endpoints must be null/omitted. Changing preferences timezone does not change a rule's stored timezone.

### Endpoint sequence

| Order | Method/path | Example request body | Expected success | Expected failures |
|---|---|---|---|---|
| 1 | `POST /api/v1/events` | ONCE body above | 201 complete ONCE `EventRead` below | 422 invalid interval/category/shape or forbidden fields; 409 missing profile; 401/503 authentication/dependency errors |
| 2 | `POST /api/v1/events` | WEEKLY body above | 201 complete WEEKLY `EventRead` below | 422 missing rule fields, invalid dates/weekday/timezone/DST endpoints or mixed shapes; 409/401/503 as above |
| 3 | `GET /api/v1/events/{event_id}` | None | 200 complete stored `EventRead` | 404 missing/other-user ID; 422 malformed UUID; 401/503 |
| 4 | `GET /api/v1/events` | None; query example `?limit=2&recurrence_type=WEEKLY` | 200 page of complete `EventRead` objects | 422 invalid pagination/category/recurrence query values; 401/503 |
| 5 | `PATCH /api/v1/events/{event_id}` | `{"title":"Manual A lecture updated","location":"Room 3"}` | 200 complete updated `EventRead` | 404 missing/other-user ID; 422 invalid merged rule/interval/UUID/forbidden fields; 401/503 |
| Final cleanup | `DELETE /api/v1/events/{event_id}` | None | 204, empty body; entire stored rule if WEEKLY | 404 missing/other-user/already deleted ID; 422 malformed UUID; 401/503 |

Example ONCE `EventRead`:

```json
{
  "title": "Manual A lecture",
  "category": "STUDY",
  "location": "Room 2",
  "recurrence_type": "ONCE",
  "start_at": "2026-11-16T07:00:00Z",
  "end_at": "2026-11-16T08:00:00Z",
  "recurrence_first_date": null,
  "recurrence_until_date": null,
  "recurrence_start_time": null,
  "recurrence_end_time": null,
  "recurrence_end_day_offset": null,
  "recurrence_timezone": null,
  "event_id": "00000000-0000-4000-8000-000000000003",
  "user_id": "00000000-0000-4000-8000-000000000001",
  "source": "MANUAL",
  "status": "SAVED",
  "created_at": "2026-11-16T08:00:00Z",
  "updated_at": "2026-11-16T08:00:00Z",
  "weekday": null
}
```

Example WEEKLY `EventRead`:

```json
{
  "title": "Manual A weekly work shift",
  "category": "WORK",
  "location": "Office",
  "recurrence_type": "WEEKLY",
  "start_at": null,
  "end_at": null,
  "recurrence_first_date": "2026-11-16",
  "recurrence_until_date": "2026-12-28",
  "recurrence_start_time": "22:00:00",
  "recurrence_end_time": "06:00:00",
  "recurrence_end_day_offset": 1,
  "recurrence_timezone": "Africa/Cairo",
  "event_id": "00000000-0000-4000-8000-000000000005",
  "user_id": "00000000-0000-4000-8000-000000000001",
  "source": "MANUAL",
  "status": "SAVED",
  "created_at": "2026-11-16T08:00:00Z",
  "updated_at": "2026-11-16T08:00:00Z",
  "weekday": 1
}
```

GET/PATCH return these same complete shapes, with actual IDs/values/timestamps. Source/status stay server-managed MANUAL/SAVED. No generated occurrences or exception records are returned.

PATCH applies to the entire weekly rule. Changing only the type while keeping incompatible stored fields fails. Example conversion of an existing ONCE Event to WEEKLY:

```json
{
  "recurrence_type": "WEEKLY",
  "start_at": null,
  "end_at": null,
  "recurrence_first_date": "2026-11-16",
  "recurrence_start_time": "09:00:00",
  "recurrence_end_time": "10:00:00",
  "recurrence_end_day_offset": 0,
  "recurrence_timezone": "Africa/Cairo",
  "weekday": 1
}
```

To convert WEEKLY to ONCE, provide valid start_at/end_at and set all weekly rule fields, including weekday, to null in the same PATCH. After every PATCH, GET the record. After final deletion, GET it and expect 404.

## 11. Pagination and filtering

Requires a real bearer token and manually created records. Create at least three matching Tasks and three matching Events before checking multiple pages. Keep records unchanged while comparing pages.

Both list endpoints return this page structure; this is an exact empty-page example:

```json
{"items":[],"limit":2,"offset":0,"has_more":false}
```

For non-empty pages, `items` contains full TaskRead or EventRead objects shown above. There is no total/count field. Default limit is 50; allowed limit is 1-100 and offset 0-1000000. `has_more` indicates whether another item exists after this page. For the next page, increase offset by the number of returned items.

| Method/path example | Authentication/body | Expected result/failure |
|---|---|---|
| `GET /api/v1/tasks?limit=2&offset=0` | Bearer; no body | 200, at most two own Tasks; has_more true if more matching records exist |
| `GET /api/v1/tasks?limit=2&offset=2` | Bearer; no body | 200 next page; no overlap while data remains unchanged |
| `GET /api/v1/tasks?category=STUDY&task_type=FLEXIBLE&priority=5` | Bearer; no body | 200 own Tasks matching all filters |
| `GET /api/v1/tasks?deadline_from=2026-11-19T00:00:00Z&deadline_to=2026-11-21T23:59:59Z` | Bearer; no body | 200 matching Tasks; deadline boundaries are inclusive and exclude null deadlines |
| `GET /api/v1/events?limit=2&offset=0&category=WORK&recurrence_type=WEEKLY` | Bearer; no body | 200 matching own stored rules; one record per rule, not per occurrence |
| `GET /api/v1/events?recurrence_type=ONCE` | Bearer; no body | 200 own ONCE Events only |

Task ordering is deadline ascending, null last, then task_id. ONCE-filtered Event ordering is start_at/event_id; WEEKLY-filtered ordering is recurrence_first_date/event_id; unfiltered Event ordering is created_at/event_id. An empty result is 200, not 404.

Invalid pagination/category/type/priority/deadline inputs return 422; missing/invalid authentication returns 401; unavailable dependencies can return 503. Query priority is a number 1-5. Use UTC `Z` in URL timestamps, or URL-encode a plus offset as `%2B`; Swagger handles query encoding.

PowerShell list example:

```powershell
Invoke-RestMethod -Method Get `
    -Uri "$manualBase/tasks?limit=2&offset=0&category=STUDY" -Headers $manualHeaders
```

## 12. Invalid inputs and expected errors

Change one input at a time. Confirm failures leave the existing record unchanged by GETting it again. Do not deliberately interrupt databases, corrupt tokens, or flood the provider to manufacture infrastructure failures.

### Checks possible without real Supabase credentials

| Request/action | Expected result |
|---|---|
| Swagger Logout, then `GET /api/v1/profiles/me`, `/preferences/me`, `/tasks`, or `/events` | 401 and `WWW-Authenticate: Bearer` |
| `POST /api/v1/auth/register` with `{"email":"bad","password":"short"}` | 422 schema validation before contacting Auth |
| `POST /api/v1/auth/login` with `{"email":"bad","password":""}` | 422 schema validation before contacting Auth |
| `POST /api/v1/auth/refresh` with `{"refresh_token":""}` | 422 schema validation before contacting Auth |

Protected validation checks below should use a valid real token; without it, authentication/dependency errors can happen before field validation.

### Authenticated input checks

| Endpoint/action | Invalid example | Expected result |
|---|---|---|
| `PATCH /profiles/me` | `{"id":"00000000-0000-4000-8000-000000000006"}` | 422 forbidden field |
| `PATCH /profiles/me` | `{"full_name":"   "}` or `{"username":" padded "}` | 422 |
| `PATCH /profiles/me` | A username already owned by the other manual account | 409; previous profile remains unchanged |
| `PATCH /preferences/me` | `{"timezone":"Invalid/Zone"}` or `{"notifications_enabled":"yes"}` | 422 |
| `PATCH /preferences/me` | Clear only one endpoint of a stored non-null work window | 422 merged interval validation |
| `POST /tasks` | Valid Task body changed to priority 0/6, duration 0, category OTHER, or title whitespace-only | 422 |
| `POST /tasks` | Add `"user_id"`, `"status"`, generated ID, or timestamps to a valid body | 422 |
| `POST /tasks` | FIXED without both endpoints; or FLEXIBLE with a fixed endpoint | 422 |
| `PATCH /tasks/{task_id}` | `{"priority":null}` | 422 required value cannot be null |
| `POST /events` | ONCE end equal to/before start, or timestamps without offsets | 422 |
| `POST /events` | WEEKLY until before first date; weekday not matching first date; missing timezone; negative day offset | 422 |
| `POST /events` | Mix ONCE start_at/end_at with weekly fields | 422 |
| `PATCH /events/{event_id}` | Add source/status/user_id, or leave an invalid combined interval | 422 |
| Task/Event GET/PATCH/DELETE by ID | `not-a-uuid` as path input | 422 with a valid token |
| Task/Event GET/PATCH/DELETE by ID | Well-formed UUID that does not exist, with an otherwise valid body where needed | 404 |
| Either collection GET | limit=0/101, offset=-1/1000001, category=OTHER | 422 |
| Task collection GET | Upper deadline bound earlier than lower bound | 422 |

Path prefixes in this table are `/api/v1`.

For a manual DST rejection check, use a valid WEEKLY body with recurrence_timezone `America/New_York`, first date `2026-03-08`, weekday 7, start `02:30:00`, end `03:30:00`, and end-day offset 0; omit until. Expected 422 because the first start falls in the spring DST gap. An analogous first-date fold example is `2026-11-01`, weekday 7, start `01:30:00`, end `02:30:00`, offset 0: expected 422 for ambiguity.

Weekly validation checks first and bounded final occurrence endpoints. It does not certify every intervening/future DST transition or expand occurrences. Later scheduling consumers must resolve each interpreted occurrence themselves.

Example validation error shape:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "Request validation failed",
    "details": [
      {
        "field": "body.priority",
        "message": "Input should be less than or equal to 5"
      }
    ]
  }
}
```

All backend errors use `error.code`, `error.message`, and `error.details` (an array, sometimes empty). Exact validation messages/field locations depend on the rejected rule. Other useful statuses:

- 400: registration rejected by Auth.
- 401: invalid/missing/expired bearer token, or rejected login/refresh credentials.
- 404: absent or other-user Task/Event.
- 409: uniqueness or profile/Auth-user dependency conflict.
- 422: request or combined-record validation.
- 429: Auth rate limit; honor Retry-After if present.
- 502: failed/invalid/unverifiable Auth provider response.
- 503: configuration, restricted runtime role, database, or Auth availability issue.
- 500: unexpected backend error; record the route/status and sanitized response for investigation.

Invalid bearer checks require working verifier configuration to establish a meaningful 401. With an incompatible signing algorithm, login can return 502 for an unverifiable upstream session, while protected requests reject its token with 401. Do not change security rules to make that token work.

## 13. Verify two-user isolation

This requires two real accounts in the same configured Supabase project. Do not use administrator/service-role tokens for either account.

1. With User A authorized, create at least one Task and one Event. Save their IDs and A's profile ID. Set a recognizable profile name/preferences value.
2. Register/confirm/login User B. Log out A in Swagger and authorize using B's access token, or use separate normal/private browser sessions. Maintain separate PowerShell token/header variables if using PowerShell.
3. As B, list Tasks/Events: A's saved IDs must not appear.
4. As B, GET A's Task/Event by ID: expect 404.
5. As B, PATCH A's Task/Event using a valid body such as `{"title":"Manual cross-user attempt"}`: expect 404.
6. As B, DELETE A's Task/Event: expect 404.
7. As B, GET `/profiles/me` and `/preferences/me`: IDs must identify B, not A. PATCH B's profile/preferences and then switch back to A; A's values must remain unchanged.
8. As B, supplying A's ID/user_id in profile/preferences/Task/Event request bodies must return 422. Ownership is derived from the token.
9. Reauthorize A. GET A's saved Task/Event and check they still exist and have their original values. Repeat the resource checks in reverse using B's records.

There are no `/profiles/{user_id}` or `/preferences/{user_id}` endpoints. A 404 from an invented route does not prove ownership enforcement; use the actual `/me` APIs and cross-user body rejection checks.

Record only status codes and non-secret IDs/titles for your comparison. A dependency error such as 503 is not evidence of isolation. Dashboard/administrator table access is not an end-user RLS test.

## 14. Inspect persisted data safely

Start with authenticated GET requests: after POST/PATCH, repeat GET by ID and collection GET. Reload Swagger and repeat GET to confirm the record is stored rather than just displayed in an earlier response. A frontend cache is not involved.

Optional SQL inspection is for a trusted local database operator using an existing authorized inspection connection. Do not create roles, disable RLS, alter tables, or query Auth passwords/tokens. Inspect only your manual records with SELECTs and bounded results.

If using the portable local PostgreSQL binaries, open psql with your actual local inspection username/database/port. `-W` prompts for the password without putting it in command arguments:

```powershell
& '.\.local\pgsql\bin\psql.exe' -h 127.0.0.1 -p 55432 `
    -U REPLACE_LOCAL_INSPECTION_USERNAME -d REPLACE_LOCAL_DB_NAME -W
```

Alternatively, use your existing local pgAdmin connection. Replace the example UUID below with your actual profile `id`, then run a read-only transaction:

```sql
BEGIN READ ONLY;

SELECT id, username, full_name, created_at, updated_at
FROM public.profiles
WHERE id = '00000000-0000-4000-8000-000000000001'::uuid;

SELECT preference_id, user_id, timezone, notifications_enabled,
       preferred_work_start, preferred_work_end, updated_at
FROM public.user_preferences
WHERE user_id = '00000000-0000-4000-8000-000000000001'::uuid;

SELECT task_id, user_id, title, task_type, priority, status,
       estimated_duration_minutes, deadline, fixed_start_at, fixed_end_at
FROM public.tasks
WHERE user_id = '00000000-0000-4000-8000-000000000001'::uuid
ORDER BY created_at DESC, task_id
LIMIT 20;

SELECT event_id, user_id, title, recurrence_type, start_at, end_at,
       recurrence_first_date, recurrence_until_date,
       recurrence_start_time, recurrence_end_time,
       recurrence_end_day_offset, recurrence_timezone, source, status
FROM public.events
WHERE user_id = '00000000-0000-4000-8000-000000000001'::uuid
ORDER BY created_at DESC, event_id
LIMIT 20;

COMMIT;
```

An unscoped restricted runtime SQL connection can return no owned rows because `auth.uid()` is null; that is expected. Do not forge SQL identity claims or use a privileged role as the API runtime. Use the authenticated API to inspect your user's rows. A trusted administrator inspection connection may see records independently of RLS, so it demonstrates persistence only, not authorization.

For an already prepared hosted test project, you can personally inspect the four public application tables in the Supabase dashboard **Table Editor**, filtering by your manual user/record IDs. Do not edit/delete existing records there. The WEEKLY `weekday` response field is derived from recurrence_first_date; it is not a stored database column.

For cleanup, return to Swagger as the owning user and DELETE only the Task/Event IDs you created during this session. Expect 204, then 404 when GETting them. Do not delete profiles/Auth users/preferences or reset/drop databases as part of this guide.

## 15. Stop the server and clear the manual session

In the terminal running Uvicorn, press **Ctrl+C**. Expected output includes application shutdown/server process finished. This stops FastAPI and closes its connections; it does not delete database data. Leave an existing/shared PostgreSQL server running.

Log out Swagger authorization and close the browser session. Close the second PowerShell terminal containing token variables, or remove those variables when finished. Exit psql with `\q`. Do not save session responses containing tokens into tracked files.

## 16. Supabase dashboard details for a later approved setup

Database deployment and Auth configuration are separate from manual testing. This section describes what is needed; it does not authorize a remote connection, migration, signing-key change, or account operation.

| Purpose | What to obtain/check yourself | Where to look | Secret handling |
|---|---|---|---|
| Identify the target | Project URL/reference and whether it is a test or production project | Project settings/API settings or Connect | Project URL/reference can be shared without passwords |
| Enable Auth delegation | Publishable API key, or legacy anon key | API Keys/settings | Configure privately as SUPABASE_PUBLIC_KEY; not a user access token |
| Verify access tokens | Active ES256 or RS256 signing algorithm and trusted project URL/JWKS | Authentication signing-key settings | Algorithm/key ID are sufficient for discussion; no JWT secret or private signing key is needed |
| Allow email/password signup/login | Email provider enabled; signup and confirmation settings; redirect/Site URL and email delivery configuration if confirmation is used | Authentication providers/sign-in settings, URL configuration, email settings | Share only non-secret settings if troubleshooting |
| Plan database deployment | Direct/session-mode host, port 5432, database name, username, network accessibility and TLS CA/trust details | Connect dialog; database/settings/network/SSL configuration | Share connection details only with password removed; set passwords locally |
| Apply approved application migration later | Separate migration/admin connection with permission for application tables/policies/functions/grants; current schema/migration state | Existing project database and Connect details | MIGRATION_DATABASE_URL stays private; any remote DDL requires explicit later approval |
| Run normal CRUD safely | Dedicated restricted runtime LOGIN inheriting authenticated, without superuser/BYPASSRLS/admin memberships or application-table ownership | Existing role setup, or a separately approved provisioning step | DATABASE_URL uses this role's private password; never migration/admin credentials |
| Manually obtain user sessions | Confirmed test-account email/password and login through the real Auth service | Authentication Users plus your signup/login flow | Keep test passwords/access/refresh tokens on your machine; no need to send them to me |

To deploy the application database later, the existing shared Alembic migration creates the approved public application schema and security objects in the selected database. It is not a CSV/data upload, and it does not create a duplicate Auth/users table. Supabase already manages `auth.users`; real signup creates Auth accounts, and verified backend onboarding creates their application profiles/default preferences.

The application database, public API key, and token issuer must belong to the same intended environment. Separate migration and restricted runtime connections are required. A Supabase service-role/secret API key is not needed for the current registration/login/CRUD implementation, and private signing material is not needed for ES256/RS256 verification.

For a future deployment discussion, provide only the project URL/reference, target environment, password-redacted connection details, active signing algorithm, relevant Auth settings, and whether the application tables/runtime role already exist. Keep actual credentials in your private local configuration. Remote changes need a separate explicit instruction after reviewing the target and migration; nothing in this guide performs them.
