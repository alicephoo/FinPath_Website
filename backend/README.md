# FinPath Backend

See [README.md](../README.md) for the full-stack overview, deployment, and local setup, and [PROJECT_SPEC.md](../PROJECT_SPEC.md) for authoritative product behavior. This document covers backend-specific implementation details.

The backend is a FastAPI service with typed environment settings, separate
PostgreSQL runtime, registration, and migration connection configuration,
SQLAlchemy engine and session infrastructure, `/health` and `/ready` probes, and
20 registered SQLAlchemy models. The initial revision `7270e464aca9`,
authentication revisions `c7a4e2f19b30`, `e1b9c0d4a7f2`, and `f4a1c2d3e5b6`, the
registration grant revision `b9e4d2a7c1f3`, and the trip-status revision
`c4f7a1b9e2d6` are applied and verified on the dedicated `finpath_test`
database and the development `finpath` database. Migrations are explicit and
are never run by application startup.

The implemented APIs are authenticated and owner-scoped:

- Transactions under `/api/v1/transactions`, plus the read-only
  `/api/v1/transaction-categories`.
- Loans under `/api/v1/loans`, including repayments and append-only repayment
  corrections.
- Savings goals under `/api/v1/savings/goals`, including contributions,
  withdrawals, and archive/restore.
- The Dashboard overview under `/api/v1/dashboard`.
- Starting-balance financial settings under `/api/v1/users/me/financial-settings`.
- The Travel Budget API under `/api/v1/trips`, including planned expenses,
  actual spending, atomic JSON import, and completion/cancellation with
  allocation-aware accounting.
- The dashboard reports and CSV export under `/api/v1/reports`.

Tasks 3.0a Backend Architecture and 3.0b API Contracts are APPROVED AND
COMPLETE. The Task 3.6a.2 account-lifecycle and financial-immutability design is
APPROVED AND COMPLETE, and the Task 3.8b protection matrix is approved. The only
explicitly deferred runtime authorization gap is the `user_roles` write path for
Admin management. The User status check supports exactly `active`, `suspended`,
and `closed`.

## Requirements

- Python 3.11 or 3.12
- pip (included with Python)
- PostgreSQL 17 (the migrations, triggers, and tests target PostgreSQL 17;
  17.11 is used locally)

The project uses a local virtual environment and pip with `pyproject.toml` as
the dependency manifest. It does not require a globally installed tool.

## Install

From the project root:

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
test -f .env || cp .env.example .env
```

`.env` is ignored by Git. It contains local runtime, registration, and
migration credentials plus the access-token secret; never put real credentials
in the example file. The command preserves an existing `.env`. Runtime uses
`FINPATH_DATABASE_URL` as `finpath_app`; registration writes use the separate
`FINPATH_REGISTRATION_DATABASE_URL` as `finpath_registration`; Alembic requires
the separate `FINPATH_MIGRATION_DATABASE_URL` as `finpath_migrator` and never
falls back to runtime credentials. `FINPATH_ACCESS_TOKEN_SECRET` must be a long
random value; token issuance fails closed when it is empty.

## Run

With the virtual environment activated, from `backend/`:

```sh
uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`. The liveness endpoint is
`GET /health` and returns `{"status":"ok"}` without connecting to PostgreSQL.
The readiness endpoint is `GET /ready`: it returns `{"status":"ready"}` when a
database connection succeeds and `503 {"status":"unavailable"}` otherwise.
Interactive OpenAPI documentation is available at `/docs`.

The container entry point binds to `0.0.0.0` and uses `PORT` when supplied,
defaulting to `8000`; local Docker Compose therefore continues to use port
`8000`. Render supplies `PORT` automatically, so do not set it there. Use
`/health` as the health check path.

### Container image security

Build the image with `backend/` as the Docker build context so
`backend/.dockerignore` applies:

```sh
docker build --platform linux/amd64 -t <registry>/finpath-backend:<tag> ./backend
```

The image runs as the non-root `finpath` user. Application source and migration
files are root-owned and read-only to the runtime user. The only application
directory writable by `finpath` is `/app/var/email_outbox`, retained for the
local development outbox. The production no-email deployment configuration
does not use the outbox; it resolves to an unconfigured sender. Do not build
from the repository root, and do not omit `--platform linux/amd64` when
preparing an image for Render.

The database engine is configured with psycopg 3, connection pre-ping, a pool
size of 5, up to 10 overflow connections, and a 30-second checkout timeout.
Database sessions are available through the `get_db` FastAPI dependency and are
closed after each request. Operational database errors produce a generic
`503 database_unavailable` response in the documented error envelope without
exposing connection details.

No PostgreSQL server is required to import the application or run its health
tests. The engine does not connect until a database operation is requested.

## Observability

The backend emits structured JSON request logs, bounded domain events, and
in-process Prometheus metrics. This is request correlation and metrics only;
there is no distributed tracing.

### Configuration

| Setting | Default | Purpose |
|---|---|---|
| `FINPATH_LOG_LEVEL` | `INFO` | Level for the `app` logger tree. |
| `FINPATH_SLOW_REQUEST_THRESHOLD_MS` | `1000` | Duration at or above which a request is logged as slow and counted by `finpath_http_slow_requests_total`. |
| `FINPATH_METRICS_ENABLED` | `false` | Enables the `GET /metrics` endpoint. |
| `FINPATH_METRICS_TOKEN` | unset (`None`) | Bearer token required by `/metrics` when metrics are enabled; never authorizes when unset or empty. |

Metrics are disabled by default, and no production metrics configuration is
enabled. Enabling metrics without a token does not fail startup; the endpoint
simply refuses access.

### Request correlation

Each request resolves an `X-Request-ID` (1–128 characters, first character
alphanumeric, remainder limited to `A–Z`, `a–z`, `0–9`, `.`, `_`, `:`, `-`),
generating a UUID when the header is missing or malformed. The ID is stored in a
request context, echoed in the `X-Request-ID` response header, exposed through
CORS, and attached to every log record for that request. Each completed request
emits one JSON record with `timestamp`, `severity`, `logger`, `event`,
`message`, `request_id`, and bounded fields such as `method`, `route`,
`status_code`, `duration_ms`, `slow`, and `error_type`. Request/response bodies,
cookies, authorization headers, query strings, and database details are never
logged.

### Domain events

Domain events use an allowlisted vocabulary and fixed authentication-failure
reasons. Examples include `auth_login_succeeded`, `auth_login_failed`,
`transaction_created`, `loan_repayment_recorded`, `savings_goal_archived`,
`travel_spending_recorded`, and `report_exported`. `auth_login_failed` requires
one of `invalid_credentials`, `email_verification_required`,
`account_suspended`, or `account_closed`; other events reject a reason. Messages
are static and no user data is interpolated. Write-path events are emitted after
a successful commit, and idempotent replays and archive/restore no-ops are
suppressed. The request ID is attached by the logging context filter.

### Metrics

A fresh metrics registry is created per application instance after all routes
are registered. Three metrics are exposed in the Prometheus text format
(`text/plain; version=0.0.4; charset=utf-8`):

| Metric | Type | Labels |
|---|---|---|
| `finpath_http_requests_total` | Counter | `route`, `method`, `status` |
| `finpath_http_request_duration_seconds` | Histogram | `route`, `method` |
| `finpath_http_slow_requests_total` | Counter | `route`, `method` |

The histogram uses default buckets in seconds: `0.005, 0.01, 0.025, 0.05, 0.1,
0.25, 0.5, 1.0, 2.5, 5.0, 10.0`. Labels are bounded: `route` is the set of
registered route templates plus `unmatched` and `other`, `method` is a fixed
method set plus `other`, and `status` is a fixed set of expected status codes
plus `other`. Raw paths, path parameters, query strings, user identifiers, and
financial values never become labels. Each eligible request is recorded once
with its final status and duration; recording failures are swallowed and logged
as a static warning so metrics cannot change a response. The `/metrics` scrape
route is excluded from instrumentation.

Metrics are in-process only: counters reset on process restart and are not
shared across backend instances.

### `/metrics` endpoint

`GET /metrics` is excluded from the OpenAPI schema.

- **Disabled (default):** returns an empty `404`.
- **Enabled:** requires a single well-formed `Authorization: Bearer <token>`
  credential. A correct token returns the Prometheus exposition. Missing,
  malformed, duplicate, incorrect, or unconfigured credentials return an
  indistinguishable generic `401` with `WWW-Authenticate: Bearer`.
- The token is compared with `hmac.compare_digest` over UTF-8 bytes and is never
  authorized when unset or empty. The configured token is a masked `SecretStr`
  and is never logged or returned.
- The endpoint does not require browser CSRF or trusted-origin checks, and it
  never instruments its own scrape requests.

### Observability tests

```sh
pytest tests/test_observability.py
pytest tests/test_metrics.py
pytest tests/test_http_metrics.py
pytest tests/test_domain_events_postgres.py
```

`test_observability.py` covers request-ID resolution, structured logging, the
domain-event allowlist, and redaction. `test_metrics.py` covers registry
validation, counter/gauge/histogram semantics, rendering, and concurrency.
`test_http_metrics.py` covers HTTP instrumentation and the protected `/metrics`
endpoint. `test_domain_events_postgres.py` covers post-commit emission, replay
and no-op suppression, and failed-write absence against PostgreSQL; it skips
when the test database is not configured (finding M1).

## Authentication

Task 4.1 implements the approved authentication foundation under
`/api/v1/auth`. The routes are:

| Method and path | Purpose |
|---|---|
| `POST /auth/register` | Create an account, assign role ID `1` (`user`), and create one financial-settings row. The account is unverified when email verification is enabled and already verified when it is disabled for local development. Requires `Idempotency-Key`; returns `201` with no credentials or refresh cookie. |
| `POST /auth/login` | Verify credentials for an active account. When verification is enabled, an unverified account is rejected; otherwise return a 15-minute signed access token and CSRF token, and set the refresh cookie. |
| `POST /auth/refresh` | Validate Origin and CSRF, rotate the refresh token, and return a new access/CSRF pair. |
| `POST /auth/logout` | Validate Origin and CSRF, revoke the current session, clear the refresh cookie, and return `204`; repeat logout is safe. |
| `GET /auth/csrf` | Return a CSRF token bound to the active session family. |
| `GET /auth/me` | Return the authenticated identity, roles, status, verification state, and timezone. |
| `POST /auth/change-password` | Verify the current password, set a new password, and revoke other sessions. |
| `POST /auth/email-verification-requests` | Request/resend initial verification; requires `Idempotency-Key` and returns generic `202`. |
| `POST /auth/email-verification/consume` | Consume a single-use 24-hour verification token; requires `Idempotency-Key`. |
| `POST /auth/password-reset-requests` | Request a reset message; generic `202`, no account enumeration, and no token for unverified accounts. |
| `POST /auth/password-reset/consume` | Consume a single-use 30-minute reset token, set the new password, and revoke sessions; requires `Idempotency-Key`. |

Passwords are hashed with Argon2id and verified against the stored hash. Access
tokens are signed JWTs with validated signature, expiry, issuer, and audience;
they include the server-side session ID. Refresh, verification, and reset tokens
are high-entropy opaque values; only their SHA-256 hashes are stored. Refresh
tokens rotate on every use, a reused rotated token revokes the whole session
family, and logout/suspension/closure revoke sessions. Refresh, logout, and CSRF
retrieval validate the request Origin; refresh and logout also require a CSRF
token bound to the session family. Unverified logins return
`403 email_verification_required`, and suspended/closed accounts return
`403 account_suspended`/`403 account_closed`, all without credentials.
Password-reset and email-verification requests never reveal whether an account
exists. Auth responses use the shared success/error envelope and never include
stack traces, SQL details, passwords, or token values.

Email verification is controlled by `FINPATH_REQUIRE_EMAIL_VERIFICATION`
(default `true`). Local development may set it to `false`: registration then
stores `users.email_verified_at` immediately, creates no verification token,
sends no email, and returns `verification_status: "not_required"`, so the user
can sign in immediately. Existing unverified development accounts also become
login-capable without modifying their rows. With verification enabled, the
existing token/email flow and `403 email_verification_required` behavior are
unchanged. `FINPATH_ENVIRONMENT=production` (or `prod`) with verification
disabled fails at startup; never disable verification in production. When
verification is disabled, the resend route returns its generic `202` response
and does not send mail.

Refresh-cookie transport is controlled by `FINPATH_REFRESH_COOKIE_SECURE`. It
defaults to `false` for `FINPATH_ENVIRONMENT` values `development`, `local`,
`test`, and `testing`, and to `true` otherwise, so a plain-HTTP local frontend
and API can persist the cookie in browsers that do not treat `localhost` as a
secure context. The cookie stays `HttpOnly`, `SameSite=Lax`, and path-scoped to
`/api/v1/auth`. Production rejects an explicit `false` at startup.

### Registration database identity

Registration writes use the dedicated PostgreSQL login `finpath_registration`,
never `finpath_app`. The identity is intentionally minimal:

- No `SUPERUSER`, `CREATEDB`, `CREATEROLE`, `REPLICATION`, or `BYPASSRLS`.
- No database, schema, or table ownership and no membership in
  `finpath_migrator` or any other role.
- Column-level INSERT only for the approved `users`, `user_roles`,
  `user_financial_settings`, `auth_action_tokens`, and `idempotency_records`
  columns needed by registration.
- A non-`SECURITY DEFINER` trigger rejects any `user_roles.role_id` other than
  `1` from this identity, so public registration can never assign Admin.
- A second non-`SECURITY DEFINER` trigger restricts registration token writes to
  `token_type = 'email_verification'` with no `target_email`, so the identity
  cannot mint password-reset or email-change tokens.
- No `SELECT` on `users.email`, no access to financial tables, and no
  INSERT/UPDATE/DELETE on `auth_sessions`.

Provision the login once as a PostgreSQL superuser (use a unique strong random
password and keep it only in `.env`):

```sql
CREATE ROLE finpath_registration LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS PASSWORD '<registration_password>';
GRANT CONNECT ON DATABASE finpath TO finpath_registration;
GRANT CONNECT ON DATABASE finpath_test TO finpath_registration;
```

The migrations grant the approved schema/column privileges and install the
guard triggers. Revision `b9e4d2a7c1f3` adds only `INSERT (email_verified_at)`
on `users` so registration can create an already-verified account when
`FINPATH_REQUIRE_EMAIL_VERIFICATION=false`; the identity still has no `UPDATE`
privilege on that column. Do not grant table-level DML, ownership, role
membership, or Admin assignment to this identity. `finpath_app` remains
`SELECT`-only on `user_roles`; registration does not change runtime privileges.

### Email delivery

Development uses `FINPATH_EMAIL_BACKEND=outbox` by default, writing
verification and password-reset messages to an owner-only directory
(`FINPATH_EMAIL_OUTBOX_DIR`, default `var/email_outbox`) instead of delivering
them. The outbox is for local testing only and is not a delivery guarantee; the
account remains unverified if a message cannot be sent, and the user can request
another message. The no-email production configuration requires
`FINPATH_REQUIRE_EMAIL_VERIFICATION=false` together with the explicit
`FINPATH_ALLOW_UNVERIFIED_REGISTRATION=true` opt-in; it does not use the
outbox and does not require Resend. Because the feature is gated rather than
defaulted, production otherwise stays fail-closed.

Production uses the Resend HTTPS API (`https://api.resend.com/emails`) through
the existing `EmailSender` interface. Configure:

- `FINPATH_EMAIL_BACKEND=resend`
- `FINPATH_RESEND_API_KEY` — secret Resend API key; set it only in the
  deployment platform’s secret store, never in source or logs.
- `FINPATH_EMAIL_FROM` — a sender address on a domain or address verified in
  Resend, for example `FinPath <no-reply@your-verified-domain.example>`.

The Resend request uses a 10-second timeout. Provider rejection and network
failures raise the existing `EmailDeliveryError`; registration remains
unverified and the resend endpoint can be retried. Tokens, API keys, and message
contents are never logged.

Production startup fails clearly when Resend is selected without an API key or
sender address, and production email verification requires the Resend backend.
A verified Resend sending domain/address is required before real delivery can be
tested; automated tests mock the HTTP request and do not send email.

### Deferred authentication work

Email-change request/consume endpoints, Admin role management and the first-admin
bootstrap, shared rate limiting, MFA/passkeys/social login, and other production
operations remain deferred. Password reset is implemented because it is in
the approved API contracts. Runtime `user_roles` INSERT/UPDATE/DELETE remain
withheld from `finpath_app`; Admin role assignment requires a separately
approved privileged path and is not exposed through public registration.

## Transactions API

Task 4.2 implements owner-scoped income/expense records under `/api/v1`. Every
route requires a verified, active authenticated user through the Task 4.1
access-token dependency; `finpath_app` is the only runtime identity used.

| Method and path | Purpose |
|---|---|
| `GET /transaction-categories` | List active system-managed categories, optionally filtered by `type=income\|expense`. No category mutation route exists. |
| `GET /transactions` | List caller-owned transactions with `type`, `category_id`, inclusive `from`/`to`, description-only case-insensitive `q`, `limit` (1–100, default 50), and opaque `cursor` filters. |
| `POST /transactions` | Create a transaction. Requires `Idempotency-Key`; returns `201` with the resource and `ETag`. |
| `GET /transactions/{transaction_id}` | Read one caller-owned transaction and return its `ETag`. |
| `PATCH /transactions/{transaction_id}` | Update permitted fields. Requires the current `If-Match`; returns the updated resource and `ETag`. |
| `DELETE /transactions/{transaction_id}` | Delete a caller-owned transaction. Requires current `If-Match` and `Idempotency-Key`; returns `204`. |

Client-supplied `user_id` and unknown fields are rejected. All reads and writes
are scoped to the authenticated owner; another user's or an unknown record
returns `404 not_found`. `If-Match` is mandatory (`428 precondition_required`
when missing) and a stale or malformed validator returns
`412 stale_revision`. ETags are quoted strong validators derived from the
persisted `transactions.revision`, which starts at 1 and increments on update.
Creation and deletion use the shared scoped idempotency service: an identical
retry replays the original result, a different payload returns
`409 idempotency_key_reused`, concurrent retries are serialized, and a failed
operation rolls back its in-progress record.

Amounts are positive whole-Kyat JSON integers with no rounding; zero, negative,
fractional, boolean, string, malformed, and unsafe values are rejected. The
configurable `FINPATH_MAX_FINANCIAL_ENTRY_KYAT` defaults to `1_000_000_000` and
is an individual-entry limit, not an aggregate cap. Dates must be real
local-calendar `YYYY-MM-DD` values. Categories must exist, be active, and match
the transaction's income/expense type; historical transactions remain readable
if a category is later retired. The service also exposes exact account-balance
calculation using the user's starting balance, transaction income minus
expenses, borrowed loan principal, and unreversed loan repayments dated
on or after the effective date; the starting balance is the opening balance at
the beginning of that date. It returns `not_configured` until both
settings exist, allows negative results, applies savings and travel allocation
layers, and raises `500 aggregate_out_of_range` rather than serializing an
unsafe JSON integer. Starting-balance management is not part of Task 4.2.

The frontend is integrated with these whole-MMK-Kyat endpoints for the
Transactions, Loans, Savings Goals, and Travel Budget tabs, and the Dashboard
uses the balance calculation together with the dashboard and report endpoints.

## Loans API

Task 4.3 implements owner-scoped loan and repayment records under `/api/v1`.
Every route uses the Task 4.1 authenticated-user dependency and the
`finpath_app` runtime identity.

| Method and path | Purpose |
|---|---|
| `GET /loans` | List caller-owned loans with original principal, total repaid, and outstanding principal. |
| `POST /loans` | Create a loan with `name`, `principal_kyat`, and `start_date`. Requires `Idempotency-Key`; returns `201` with the resource and `ETag`. |
| `GET /loans/{loan_id}` | Read one caller-owned loan with derived totals and return its `ETag`. |
| `PATCH /loans/{loan_id}` | Update `name`, `principal_kyat`, or `start_date`. Requires the current `If-Match`; returns the updated resource and `ETag`. |
| `GET /loans/{loan_id}/repayments` | List repayments for a caller-owned loan, newest first. |
| `POST /loans/{loan_id}/repayments` | Record a principal-only repayment. Requires `Idempotency-Key`; returns `201` with the repayment and its `ETag`. |
| `GET /loans/{loan_id}/repayments/{repayment_id}` | Read one repayment and its derived correction status with an `ETag`. |
| `POST /loans/{loan_id}/repayments/{repayment_id}/corrections` | Append a full-reversal correction with a required `reason`. Requires `Idempotency-Key`; returns `201` with the original repayment, correction, outstanding principal, and updated repayment `ETag`. |

Loan names are required and trimmed to 70 characters. Loan principal and each
repayment are positive whole-Kyat JSON integers within the configurable
`FINPATH_MAX_FINANCIAL_ENTRY_KYAT` limit; zero, negative, fractional, boolean,
string, malformed, and unsafe values are rejected. Dates are strict
local-calendar `YYYY-MM-DD` values. Repayment notes are optional and limited to
80 characters; correction reasons are required, trimmed, and limited to 200
characters. Client-supplied ownership fields and unknown fields are rejected,
and another user's loan, repayment, or correction target returns `404
not_found`.

Outstanding principal is derived as original principal minus unreversed
repayment rows; total repaid and outstanding are never stored separately.
Repayment insertion locks the loan, computes the current valid repayment total
after the lock, and rejects an amount above the outstanding principal with `409
repayment_exceeds_principal`, so concurrent repayments cannot overpay. A
successful repayment increments the loan revision. `PATCH` locks the loan,
requires the current `If-Match`, and rejects a principal below the valid
repayment total with `422 validation_error` and
`principal_below_repayments`. Repayment facts are immutable; a correction
appends one full-reversal row, increments both the repayment and loan
revisions atomically, and a second correction returns `409
repayment_already_corrected`. Repayments and corrections are not transaction
expenses. The account balance treats loan principal as a cash inflow on
`start_date` and each unreversed repayment as a cash outflow on
`repayment_date`; a full reversal removes that repayment from the cash-flow
total.

## Savings API

Owner-scoped savings goals, contributions, withdrawals, and archive/restore are
implemented under `/api/v1/savings/goals`. Every route uses the Task 4.1
authenticated-user dependency and the `finpath_app` runtime identity. Goal,
contribution, withdrawal, archive, and restore writes require
`Idempotency-Key`; goal updates and archive/restore also require the current
`If-Match`. Goal create, read, update, archive, and restore return a quoted
strong `ETag` derived from the goal revision; contribution and withdrawal
writes do not return an `ETag`.

| Method and path | Purpose |
|---|---|
| `GET /savings/goals` | List caller-owned goals with target, actual balance, and remaining amount. Query `status` accepts `active`, `archived`, or `all`; default is `active`. |
| `POST /savings/goals` | Create a goal with `name`, `target_kyat`, and optional `tone`. Requires `Idempotency-Key`; returns `201` with the resource and `ETag`. |
| `GET /savings/goals/{goal_id}` | Read one caller-owned goal with its derived actual and remaining amounts; return its `ETag`. |
| `PATCH /savings/goals/{goal_id}` | Update `name`, `target_kyat`, or `tone` on an active goal. Requires the current `If-Match`; returns the updated resource and `ETag`. |
| `POST /savings/goals/{goal_id}/archive` | Archive a goal. Requires `If-Match`, `Idempotency-Key`, and `{"confirm": true}`; returns the archived resource and `ETag`. |
| `POST /savings/goals/{goal_id}/restore` | Restore an archived goal. Requires `If-Match`, `Idempotency-Key`, and `{"confirm": true}`; returns the active resource and `ETag`. |
| `GET /savings/goals/{goal_id}/entries` | List append-only entries for a caller-owned goal, newest first. |
| `POST /savings/goals/{goal_id}/entries/contributions` | Append a contribution to an active goal with `amount_kyat`, `entry_date`, and optional `note`. Requires `Idempotency-Key`; increments the goal revision and returns `201` with the entry. |
| `POST /savings/goals/{goal_id}/entries/withdrawals` | Append a withdrawal to an active goal with `amount_kyat`, `entry_date`, and optional `note`. Requires `Idempotency-Key`; increments the goal revision and returns `201` with the entry. |

Goal names are required and trimmed to 80 characters; optional presentation
tones are trimmed to 16 characters and an empty tone is stored as null.
Contribution and withdrawal notes are optional and limited to 100 characters.
Savings targets must be positive whole-Kyat JSON integers within the safe
integer range but are settings rather than individual financial entries, so the
configurable `FINPATH_MAX_FINANCIAL_ENTRY_KYAT` limit does not apply to them.
Contributions and withdrawals must be positive whole-Kyat JSON integers within
that entry limit. Dates are strict local-calendar `YYYY-MM-DD` values.
Client-supplied ownership fields and unknown fields are rejected, and another
user's goal or entry target returns `404 not_found`.

Actual savings is derived from append-only entries as the checked sum of
contributions less withdrawals. `remaining_kyat` is `max(0, target_kyat -
actual_balance_kyat)`. Contributions may exceed the target because the approved
contract does not cap savings entries at the goal target; an overfunded goal
reports zero remaining. Contribution and withdrawal insertion lock the parent
goal, verify that it is active, append the immutable entry, and increment the
goal revision atomically. A withdrawal above the current actual balance is
rejected with `409 withdrawal_exceeds_balance`; concurrent withdrawals cannot
overdraw because the goal row is locked. Archived goals reject new
contributions, withdrawals, and metadata updates with `409 goal_archived`.

Savings contributions and withdrawals are not transaction expenses. They affect
the available balance only through the recognized savings allocation: an active
goal reserves `max(target_kyat, contributions) - withdrawals`, while an
archived goal reserves its actual balance. Archive/restore require
`If-Match`, `Idempotency-Key`, and an explicit `{"confirm": true}`; a missing
confirmation returns `422 confirmation_required`. Repeating the same
archive/restore action with a new key is a no-op rather than another revision
bump. Savings corrections, daily plans, plan-day transitions, and scheduled-day
association remain unimplemented.

## Dashboard API

Task 4.5 implements the authenticated Phase 1 dashboard overview at
`GET /api/v1/dashboard/overview` through the Task 4.1 authenticated-user
dependency and the `finpath_app` runtime identity. The endpoint returns one
owner-scoped snapshot containing the account-balance state, all-time and
current-month transaction income/expense totals, newest-first recent
transactions, loan outstanding principal/repayment totals with the active-loan
count, and actual savings balance, target, active-goal count, and per-goal
progress. The optional `recent_limit` query parameter accepts 1–50 and defaults
to 5.

| Field group | Contents |
|---|---|
| Account balance | `status` (`configured`/`not_configured`), starting balance, effective date, derived `amount_kyat`, and `as_of_date`. |
| Transactions | `all_time_income_kyat`, `all_time_expense_kyat` (transactions only), `travel_expense_kyat` (recognized travel), `total_expense_kyat` (transaction plus recognized travel), `current_month_income_kyat`, `current_month_expense_kyat`, and `recent_transactions`. |
| Loans | `outstanding_loan_principal_kyat`, `active_loan_count`, and `loan_repayments_kyat`. Active means outstanding principal is greater than zero. |
| Savings | `actual_savings_balance_kyat`, `savings_target_kyat`, `active_savings_goal_count`, and `savings_goals` with derived per-goal remaining amounts. |

All values reuse the existing services rather than duplicating financial logic:
the transaction balance service, a checked transaction-totals helper, the
transaction listing query, the loan service, the savings service, and the shared
travel-recognition calculation. Income and expenses remain transaction-only;
loan repayments and savings contributions are never added to transaction
expenses. The account balance does include loan cash flow — borrowed principal
and unreversed repayments — exactly once, and subtracts the recognized savings
allocation once without treating it as an expense. An active goal reserves
`max(target, contributions) - withdrawals`; contributions within the target do
not deduct twice, and archiving releases the unused remainder. Recognized travel
expense is included once in both `total_expense_kyat` and the account balance:
an active trip counts the greater of its planned budget and actual spending,
while completed or cancelled trips count actual spending only. A missing
starting balance or effective date preserves `not_configured` instead of
assuming zero, and unsafe aggregate
results return `500 aggregate_out_of_range` rather than truncating. The whole
response is read in one repeatable-read transaction so it cannot mix pre- and
post-mutation totals. The overview response does not embed per-trip travel rows
or per-loan details; those records remain available through the separate
`/api/v1/trips` and `/api/v1/loans` APIs. Cash-flow, expense-category, and CSV
reporting are provided by the separate Reports API below. The `b9e4d2a7c1f3` and
`c4f7a1b9e2d6` revisions add grants and trip status; no new accounting table was
required.

## Reports API

Owner-scoped dashboard reports and CSV export are implemented under
`/api/v1/reports` through the Task 4.1 authenticated-user dependency and the
`finpath_app` runtime identity. Every query is scoped to the authenticated
owner and runs in a repeatable-read snapshot so a response cannot mix pre- and
post-mutation rows.

| Method and path | Purpose |
|---|---|
| `GET /reports/cash-flow` | Monthly income and expense totals for the requested period, including zero-activity months. |
| `GET /reports/expenses-by-category` | Expense totals grouped by category name for the requested period. |
| `GET /reports/export.csv` | Download a UTF-8 CSV report assembled from the current overview, reports, transactions, loans, savings, and travel records. |

`from` and `to` are inclusive `YYYY-MM-DD` bounds. Omitting both returns the
current month plus the previous five months; supplying only one bound returns
`422 invalid_date_range`. Amounts are checked whole-Kyat JSON integers.
`/reports/export.csv` returns `text/csv` with a
`Content-Disposition: attachment; filename="finpath-dashboard-report.csv"` and
`Cache-Control: no-store`. The CSV includes summary, cash-flow, category,
transaction, loan, savings, and travel sections. Untrusted text cells are
escaped and protected against spreadsheet formula injection, while numeric
cells remain separate unquoted integers so the file is not corrupted.

## Financial Settings API

Task 4.6 implements the caller's starting-balance setting at
`GET` and `PUT /api/v1/users/me/financial-settings` through the Task 4.1
authenticated-user dependency and the `finpath_app` runtime identity. Both
routes operate only on the single settings row created during registration;
there is no client-supplied user identifier, so a caller can never read or
write another user's settings.

| Method and path | Purpose |
|---|---|
| `GET /users/me/financial-settings` | Return the caller's starting balance, effective date, derived balance status/amount, and ETag. |
| `PUT /users/me/financial-settings` | Replace both starting-balance fields together. Requires the current `If-Match`; returns the updated resource and ETag. |

The response uses the same account-balance shape as the Dashboard:
`status`, `starting_balance_kyat`, `effective_date`, `amount_kyat`, and
`as_of_date`. `GET` returns a quoted strong `ETag` derived from the settings
revision. `PUT` locks the settings row, verifies the supplied `If-Match`
revision, updates both fields, increments the revision, and returns the new
ETag. Missing preconditions return `428 precondition_required`; malformed or
stale values return `412 stale_revision`. Sending both fields as null clears
the setting and returns `not_configured` with a null amount. Supplying only one
null field, omitting either field, or sending an unknown field returns
`422 validation_error`.

Starting balance may be zero or negative and is a setting rather than an
individual financial entry, so the configurable
`FINPATH_MAX_FINANCIAL_ENTRY_KYAT` limit does not apply. Values must be whole
JSON integers within the safe integer range; floats, strings, booleans, and
unsafe values are rejected without rounding. `effective_date` accepts only a
real local-calendar `YYYY-MM-DD` date. The Dashboard reads the saved values
through the shared transaction balance service, preserving the cash-movement
formula and the inclusive opening-balance date boundary. The starting balance
is the opening balance at the beginning of its effective date, so same-day
transactions and loan cash movements are included. No schema
change or migration was required because registration already creates the row
and `finpath_app` already has the necessary `SELECT` and column-level `UPDATE`
privileges.

## Travel API

Task 4.7 implements the authenticated Travel Budget API under `/api/v1/trips`
through the authenticated-user dependency and the `finpath_app` runtime
identity. Every route scopes records to the caller's own trips, so a caller can
never read or write another user's plans or spending.

| Method and path | Purpose |
|---|---|
| `GET /trips` | List caller-owned trips with derived budget and spending totals. |
| `POST /trips` | Create a trip; requires `Idempotency-Key`. |
| `GET /trips/{trip_id}` | Read one trip and its current ETag. |
| `PATCH /trips/{trip_id}` | Update trip details; requires `If-Match`. |
| `POST /trips/{trip_id}/complete` | Mark an active trip completed; requires `If-Match` and `Idempotency-Key`. Unused allocation is released and actual spending remains recognized. |
| `DELETE /trips/{trip_id}` | Cancel the trip; requires `If-Match`, `Idempotency-Key`, and `{"confirm": true}`. The trip is hidden from the active list, unused allocation is released, and budgets/spending are preserved. |
| `GET\|POST /trips/{trip_id}/budgets` | List or create planned expenses (category budgets). |
| `GET\|PATCH\|DELETE /trips/{trip_id}/budgets/{budget_id}` | Read, update, or remove one planned expense; updates/deletes require `If-Match`. |
| `GET\|POST /trips/{trip_id}/spending` | List or create actual spending. |
| `GET\|PATCH\|DELETE /trips/{trip_id}/spending/{spending_id}` | Read, update, or delete one spending entry; updates/deletes require `If-Match`. |
| `POST /trips/import` | Validate and atomically import a JSON `trips` document; requires `Idempotency-Key`. |

Trip totals are derived from the stored child rows: `budget_kyat` is the sum of
planned expenses and `spending_kyat` is the sum of spending entries. Budgets are
plans rather than individual entries, so they may be zero or exceed the
configured per-entry limit up to the safe-integer bound. Spending entries must be
positive whole-Kyat amounts within `FINPATH_MAX_FINANCIAL_ENTRY_KYAT`, with a
real calendar date. Spending may exceed a category or the overall plan; the API
returns the totals and the UI warns instead of rejecting the write.

Each trip has a `status` of `active`, `completed`, or `cancelled`; existing trips
default to `active`. The shared `calculate_recognized_travel_expense` function is
the single source of truth for accounting:

- **Active:** `max(planned budget, actual spending)`.
- **Completed or cancelled:** actual spending only.

The recognized total is included once in the Dashboard's `travel_expense_kyat`
and `total_expense_kyat`, and subtracted once by `calculate_account_balance`.
Recording spending within an allocated budget therefore does not change the
balance again; spending above the plan adds only the excess. Completing or
cancelling releases unused allocation without erasing genuine spending.
Repeated completion is idempotent because recognition is derived from status and
rows rather than stored accounting entries.

Nested mutations lock the parent trip row so totals and revisions remain
consistent. Deleting a budget clears its spending entries' `category_budget_id`
and keeps their category snapshot, so historical spending is not lost. Trip
spending is stored only in `trip_spending_entries`; it never creates a
transaction or changes transaction expense totals.

The import route validates the entire document before writing anything. Invalid
rows reject the whole request with field-level errors such as
`trips[0].spending[1].amount_kyat`. Imports are serialized per user with a
transaction-scoped advisory lock, and exact duplicates are skipped and counted.
Imported trips are active, and duplicate detection prevents a second import from
creating duplicate accounting. The `c4f7a1b9e2d6` migration adds the trip-status
column, its check constraint, and the runtime `UPDATE (status)` grant without
changing existing rows or deleting data.

The focused tests are in `tests/test_travel_postgres.py`:

```sh
pytest tests/test_travel_postgres.py
```

They cover persistence across reloads, derived totals and over-budget spending,
invalid amounts and dates, ownership isolation, atomic invalid imports,
duplicate skipping, ETag/idempotency behavior, immediate Dashboard allocation,
excess-only over-budget recognition, unused-budget return on completion,
idempotent completion, cancellation preserving actual spending, multiple trips,
and import accounting without double counting.

## Alembic

Alembic requires `FINPATH_MIGRATION_DATABASE_URL` through Pydantic Settings
and uses its own SQLAlchemy engine. Missing migration credentials cause
Alembic to fail; runtime credentials are never used as a fallback. The URL is
not written to `alembic.ini` or migration revisions. Migrations are explicit
and never run by application startup. Initial revision `7270e464aca9`
represents all 20 architecture tables. Task 4.1 added `c7a4e2f19b30`
(registration identity grants and role guard), `e1b9c0d4a7f2` (revoke public
EXECUTE on the role-guard function), and `f4a1c2d3e5b6` (registration token
guard). The local-development verification configuration added
`b9e4d2a7c1f3` (registration `INSERT (email_verified_at)` grant), and Task 5.9
added `c4f7a1b9e2d6` (trip status column, check constraint, and runtime
`UPDATE (status)` grant). All six revisions are applied and verified on both
`finpath_test` and `finpath` with `finpath_migrator`; the current single head is
`c4f7a1b9e2d6`. `finpath_app` is not a database or schema owner, has no
database/schema CREATE privilege, and is not a member of the migration role.
Tasks 3.8c/3.8d trigger, column privilege, and cleanup definitions plus the
Task 4.1 registration-identity guards are verified on both databases.

### Development database initialization

Task 4.0 initialized the development database from its verified empty state.
The procedure is:

```sh
cd backend
.venv/bin/alembic upgrade head
.venv/bin/python -m app.db.seed
.venv/bin/alembic current
```

Prerequisites:

- `FINPATH_MIGRATION_DATABASE_URL` points at `finpath` as `finpath_migrator`;
  all migration and seed commands use this identity.
- `FINPATH_DATABASE_URL` points at `finpath` as `finpath_app`.
- The test URLs (`FINPATH_TEST_*`) point only at `finpath_test` and are never
  used for development initialization.
- Preflight confirms `finpath` has no application tables and no
  `alembic_version` table.

The approved result is the current Alembic head (`c4f7a1b9e2d6`), 20
application tables plus `alembic_version`, 2 roles, and 11 categories. Running
the seed again inserts zero rows. `finpath_test` is not modified by development
initialization.

### Reference-data seed

After the initial revision is applied, seed the approved roles and predefined
transaction categories with the migration identity:

```sh
cd backend
.venv/bin/python -m app.db.seed
```

`FINPATH_MIGRATION_DATABASE_URL` must point at the target database as
`finpath_migrator`; the command rejects the runtime `finpath_app` identity. It
inserts only `user` and `admin` plus the eleven approved categories, uses
stable identifiers, never overwrites or deletes existing rows, and is safe to
re-run. The first Admin is a separate, explicit bootstrap operation and is
never created by this seed or with a default password.

### Architecture table inventory

The complete architecture inventory remains 20 tables:

- Registered in SQLAlchemy metadata: `users`, `roles`, `user_roles`,
  `admin_audit_logs`, `auth_sessions`, `auth_action_tokens`,
  `idempotency_records`, `user_financial_settings`, `transaction_categories`,
  `transactions`, `loans`, `loan_repayments`, `loan_repayment_corrections`,
  `savings_goals`, `savings_plans`, `savings_plan_days`, `savings_entries`,
  `trips`, `trip_category_budgets`, and `trip_spending_entries`.

The first-release transaction categories are system-managed predefined
categories; users cannot create categories.

Trip model classes are defined once in `app/models/travel.py` and registered
through `app/models/__init__.py`; no separate duplicate `trip.py` is needed.
`savings_plans.goal_id` is unique as specified by the architecture: a plan is
edited/rebuilt in place before terminal days exist, so rebuilding does not
create a second plan for the goal.

`user_roles.granted_by_user_id` follows the architecture's `ON DELETE SET NULL`
action. Savings correction rows can reference another correction through the
same-owner self-FK, allowing the correction chain described in the
architecture. The unique partial index permits at most one correction per
referenced entry. Approved account closure retains a user tombstone and
financial/audit history rather than hard-deleting the user; personal-data
retention duration and any erasure process remain unresolved.

## Approved design defaults and remaining decisions

The approved design targets exactly 20 tables and PostgreSQL 17, uses a
transaction-scoped advisory lock for last-active-Admin protection (no
coordination table), and defaults new users to `Asia/Yangon`. Event timestamps
are UTC; financial dates remain date-only. Authentication defaults include
Argon2id, a 12-character minimum password, 15-minute access tokens, rotating
refresh tokens with 30-day absolute and 7-day inactivity lifetimes, 24-hour
email-verification tokens, 30-minute password-reset tokens, and family
revocation on refresh-token reuse. Shared external rate limits are 5 failed
login attempts per account per 15 minutes, 5 registrations per IP per hour,
3 password-reset requests per account per hour, and 3 verification resends per
account per hour. Completed idempotency results are retained for 24 hours;
expired existing records are not replayed, and completed response headers are
required (an empty JSON object is valid). No rate-limiting table is part of the
schema.

These are approved design decisions. The Task 4.1 authentication foundation
implements Argon2id hashing, the 12-character minimum, 15-minute access tokens,
rotating refresh tokens with 30-day absolute and 7-day inactivity lifetimes,
24-hour email-verification tokens, 30-minute password-reset tokens, family
revocation on reuse, and the email-verification gate. Task 4.2 implements the
Transactions API, owner scoping, ETag preconditions, idempotent creation and
deletion, whole-Kyat entry validation, category/type checks, and the
transaction-derived balance service. Task 4.3 implements the Loans API,
repayment recording, derived outstanding principal, repayment ETags, and
append-only full-reversal corrections under a loan lock. Task 4.4 implements
the Savings goals, contributions, and withdrawals API plus archive/restore,
derived actual/remaining balances, goal ETags, and goal-row locking. Task 4.5
implements the Dashboard overview API, reusing the transaction balance/totals,
loan outstanding, and savings balance calculations in one repeatable-read
snapshot. Task 4.6 implements the starting-balance financial-settings API with
owner scoping, ETag-protected replacement, paired null clearing, and safe
whole-Kyat validation. Task 4.7 implements the Travel Budget API. The Reports
API implements six-month cash flow, expense categories, and CSV export.
Shared external rate limiting, savings corrections/daily plans, Admin role
management, MFA/passkeys/social login, and other deferred security work remain
unimplemented.
Unverified users cannot access normal application APIs, financial features, or
dashboard data; verification tokens expire after 24 hours. Exact aggregates that exceed
safe JSON integer representation return `500 aggregate_out_of_range`. The
authenticated identity includes a timezone that defaults to `Asia/Yangon`;
daily savings plans and their scheduled-day semantics are not implemented, so
no plan-day transition errors are produced. Other deferred items include
password complexity beyond the minimum, personal-data retention,
Redis operations, idempotency cleanup scheduling, account-closure handling
for active financial plans, and deployment details for append-only database
permissions/triggers (the policy scope is approved). Account states are
`active`, `suspended`, and terminal `closed`; suspended and closed accounts
cannot access normal features, and sessions are revoked when either state is
entered. Closed accounts cannot be reactivated through normal self-service or
administrator workflows. Financial/audit history is retained; no user or
financial-history hard-delete flow is provided.

Financial immutability is scoped: loan repayment facts and their correction
records are append-only, savings entries are append-only, terminal daily-plan
snapshots are immutable at the schema level, and audit records are append-only.
Savings corrections and daily-plan APIs are not implemented. Idempotency
records remain mutable for processing, completion, and expiry cleanup. Other
resources retain only the edits/deletions allowed by their API contract. Tasks
3.8c/3.8d added trigger/function definitions, required checks, per-table/column
privileges, and a restricted cleanup function to the initial revision; Task
3.8e applied and verified it on `finpath_test`, and Task 4.0 applied and
verified it on the development `finpath` database. Isolated PostgreSQL 17 tests
exercise the definitions using the actual `finpath_app` role. Service
authorization, owner scoping, ETags, and locking are implemented in the
services and covered by the PostgreSQL test suite.

## Financial data protection status

The approved 20-table protection matrix is maintained in
`BACKEND_ARCHITECTURE.md`; `API_CONTRACTS.md` references the same source. The
models and initial migration contain existing constraints, indexes, and FK
behavior. The initial revision also defines financial immutability, terminal
savings-plan, correction, and closed-user triggers, per-table/column
privileges for all 20 tables, and restricted cleanup. These are applied and
verified on both `finpath_test` and the development `finpath` database, which
Task 4.0 initialized with the approved revision and seeded reference data.

Tasks 3.8c, 3.8d, and 3.8e are complete except the deferred `user_roles`
writes. PostgreSQL 17 tests exercise the migration DDL and effective
privileges using isolated schemas and `finpath_app`; they cover all 20 tables,
protected-column denials, approved repayment/savings exceptions, trip/budget
behavior, and cleanup boundaries. `user_roles` runtime INSERT/DELETE remain
deferred by explicit decision because the shared runtime identity cannot
distinguish Admin from registration; registration and Admin role management
are blocked until a separately approved privileged path exists. The cleanup
function deletes only expired tokens, sessions whose `refresh_expires_at` is
at least 30 days in the past (preserving the 30-day reuse-detection window and
never deleting active sessions), and completed idempotency records only when
both `completed_at <= now() - interval '24 hours'` and `expires_at <= now()`.
It preserves in-progress/recent idempotency records and all financial history.
PostgreSQL RLS is deferred because the shared `finpath_app` identity does not
identify the authenticated user or Admin; owner scoping and Admin authorization
remain service responsibilities. Preserve `admin_audit_logs.target_user_id
ON DELETE SET NULL`; revisit its append-only interaction only if a hard-delete
or legal-erasure workflow is proposed.

Account closure is a terminal tombstone that retains financial history; there
is no approved user hard-delete flow. Closure initiation and handling active
loans, savings goals, and trips, personal-data retention duration, and exact
cleanup invocation scheduling remain implementation or product/legal work. See
`BACKEND_ARCHITECTURE.md` and `API_CONTRACTS.md` for the maintained details.

## Test

From `backend/` with the virtual environment activated:

```sh
pytest
```

The Task 4.1 auth tests are in `tests/test_auth_postgres.py`; they exercise the
real HTTP routes against PostgreSQL and cover registration, email verification,
login/account status, access-token validation and spoofing, refresh rotation and
reuse detection, logout, password reset, idempotency, and database-security
checks for the registration identity. Focused runs:

```sh
pytest tests/test_auth_postgres.py
pytest tests/test_security.py tests/test_migrations.py tests/test_pg_test_env.py
```

Savings, report, and travel coverage lives in
`tests/test_savings_postgres.py`, `tests/test_reports_postgres.py`, and
`tests/test_travel_postgres.py`. Focused runs:

```sh
pytest tests/test_savings_postgres.py
pytest tests/test_reports_postgres.py
pytest tests/test_travel_postgres.py
```

The savings tests cover contributions, withdrawals (including
`409 withdrawal_exceeds_balance`), archive/restore with `If-Match`,
`Idempotency-Key`, and confirmation, ownership isolation, and stale revisions.
The report tests cover authentication, inclusive ranges and empty months,
owner-scoped expense categories, and CSV escaping/formula protection.
Database-security tests assert SQLSTATE `42501` for forbidden registration
writes (including Admin role assignment and password-reset/email-change token
minting), and `23503` for a permitted email-verification token with an unknown
user. Do not weaken or skip these tests to make the suite pass.

Verified CI snapshot from run
[`43174534132`](https://github.com/alicephoo/FinPath_Website/actions/runs/43174534132)
on 2026-11-12: 250 backend tests passed in 55.46 s against PostgreSQL 17. See
the root [README.md](../README.md#running-tests) for full-stack test setup and
`PROJECT_SPEC.md` for the completed evidence record.

### PostgreSQL test database

PostgreSQL tests never use the development database. They require a dedicated
database configured through `FINPATH_TEST_DATABASE_URL` (runtime role
`finpath_app`), `FINPATH_TEST_MIGRATION_DATABASE_URL` (migration role
`finpath_migrator`), and `FINPATH_TEST_REGISTRATION_DATABASE_URL` (registration
role `finpath_registration`). Provision it once as a PostgreSQL superuser:

```sh
psql -d postgres -c "CREATE DATABASE finpath_test OWNER finpath_migrator;"
psql -d postgres -c "GRANT CONNECT ON DATABASE finpath_test TO finpath_app;"
psql -d postgres -c "GRANT CONNECT ON DATABASE finpath_test TO finpath_registration;"
```

Then add all three variables to `.env`; `.env.example` documents the expected
shape. The test database name must differ from the development database, and
tests fail if it matches or if a system database is configured. Tests create
uniquely named disposable schemas and drop them afterwards. They never create,
delete, or migrate databases, and they do not sweep orphaned schemas. When the
variables are unset, PostgreSQL tests skip with these setup instructions rather
than falling back to the development database.
