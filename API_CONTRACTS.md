# FinPath REST API Contracts

**Status: Task 3.0b API Contracts APPROVED AND COMPLETE. Task 3.0a Backend Architecture is APPROVED AND COMPLETE. This document remains the approved contract and contains no implementation. Backend infrastructure, all 20 SQLAlchemy table models, the Task 4.1 authentication foundation, the Task 4.2 categories/transactions implementation, the Task 4.3 loans/repayments implementation, the Task 4.4 savings-goal/contribution implementation, the Task 4.5 dashboard-overview implementation, the Task 4.6 starting-balance financial-settings implementation, the Task 4.7 Travel Budget implementation, and the Task 5.9 allocation-aware travel accounting implementation exist; savings withdrawals/corrections/daily plans, the remaining dashboard reports, and CSV export do not. PostgreSQL 17.11 is installed and used for development and migration verification. Task 3.6a.2 account-lifecycle and financial-immutability direction is APPROVED AND COMPLETE.**

This approved contract translates `BACKEND_ARCHITECTURE.md` and current frontend behavior into a versioned REST API. It is design-only; approval does not imply implementation or migration readiness.

## 1. API conventions

### Base URL, encoding, and identifiers

- Base path: `/api/v1`.
- JSON request and response bodies use UTF-8 and `application/json`. Successful JSON bodies use `{ "data": ... }`; paginated lists add a `pagination` object.
- IDs are server-generated UUIDs serialized as strings. Clients never supply or control a resource's `user_id`.
- Financial fields are integer **whole Kyat** with explicit `_kyat` names. JSON amounts must be safe integers (`Number.isSafeInteger` range); do not send decimal fractions, floats, or cents. The configurable `MAX_FINANCIAL_ENTRY_KYAT` defaults to `1_000_000_000` for an individual financial entry, not a database maximum or a cap on aggregates.
- The individual-entry limit applies to transaction income/expenses, loan principal and repayments, savings contributions/withdrawals, and trip-spending entries. Starting balance, savings target, daily-plan target amounts, and trip budget are settings/planned amounts rather than entries; do not apply the entry limit to them. Validate those fields against storage bounds and safe response representation. Any actual contribution inserted at completion remains an individual entry and must satisfy the configured limit; if the shortfall is too large for one entry, the user must record eligible contributions in valid entries before completing the day. Negative calculated account balances are allowed.
- Aggregate totals and calculated balances are not individual entries and may exceed the entry limit. Use exact, overflow-safe intermediate arithmetic for database aggregation (for example, PostgreSQL `NUMERIC` intermediates or checked row-wise accumulation); do not allow `BIGINT SUM` to overflow before validation. Before serializing a numeric amount, require it to be within the supported JSON safe-integer range. If a valid aggregate cannot be represented safely as a JSON number, return HTTP `500` with stable code `aggregate_out_of_range` in the standard error envelope. Never round, truncate, lose precision, or return an inaccurate success-shaped fallback. This does not change individual-entry limits or the MMK whole-Kyat model.
- Date-only values use `YYYY-MM-DD` and are not converted through UTC. Event timestamps use ISO 8601 UTC.
- Currency is MMK only. Do not silently relabel or arithmetically reinterpret historical USD-cent prototype values as Kyat.
- For all supported date-range filters, `from` and `to` are inclusive. Reject malformed or reversed ranges with `422 invalid_date_range`; use the same semantics for transaction and report endpoints.
- Lists accept `limit` (default 50, maximum 100) and an opaque `cursor`. Responses include `next_cursor` or `null`. Pagination and filters are always owner-scoped. Transaction `q` performs case-insensitive substring matching on description only (not full-text search); category and date filters remain separate.
- Normal authenticated access requires a verified email. Newly registered unverified users may use only registration, initial email verification, and verification-resend functionality; they cannot access financial features, dashboard data, or other normal application APIs. Verification tokens expire after 24 hours. An unverified login attempt with valid credentials returns `403 email_verification_required` without issuing access credentials or setting a refresh cookie. Protected requests also enforce verification state.
- Implementation configuration: `FINPATH_REQUIRE_EMAIL_VERIFICATION` defaults to `true` and preserves the policy above. A local development environment may disable it to create already-verified accounts and permit immediate login; registration then returns `verification_status: "not_required"`, creates no verification token, and sends no email, and the resend route returns generic `202` without sending mail. Existing unverified accounts are not rewritten and may sign in while the setting is disabled. Production (`FINPATH_ENVIRONMENT=production` or `prod`) with verification disabled fails at startup. This does not remove verification fields or endpoints or relax the production contract.
- `ETag` is returned by every GET of an individual mutable resource and by successful creation/update of a resource with a revision. For revision-protected resources, the validator is a numeric revision token: the server emits the canonical quoted strong form `"N"` derived from that resource's server-maintained monotonic `revision` (initially 1). Conditional-write requests may send the same revision as either `"N"` or the weak form `W/"N"`; both denote the same numeric revision. This weak-input acceptance is intentional application compatibility for CDN edges that weaken strong tags in transit; it is not standard HTTP strong `If-Match` comparison semantics. The server always emits the strong `"N"` form, and clients should normally echo the ETag they received rather than construct one. Append-only savings entries and reversal records do not have standalone ETags. The architecture specifies persisted revision fields and increments. Collection GET responses do not provide per-item validators; fetch an item to obtain its current ETag. Mutations to nested records also increment affected parent revisions in the same transaction; fetch the parent for its latest ETag if its representation is not returned.
- `GET /savings/goals/{goal_id}/daily-plan` returns an ETag derived from `savings_plans.revision`. Increment that revision atomically whenever any mutation changes the daily-plan GET representation, including day completion/miss, carry-forward or pending requirements, affected contributions/corrections, and plan creation/settings/rebuild. Successful mutations return the updated daily-plan ETag when that representation changes. Terminal-day status and snapshots remain immutable.
- `users.revision` changes whenever an ETag-protected user representation changes: profile, role, account status, active email, pending email, or email-verification status. Return the updated user ETag from the corresponding successful mutation.
- `If-Match` is required on `PUT`, `PATCH`, and `DELETE` of financial settings, transactions, loans, savings goals, daily plans, trips, budgets, and mutable spending records; on savings-goal archive/restore; and on Admin account status and Admin-role changes using the target user's ETag. Creating a daily plan uses `If-None-Match: *`. Conditional writes compare revision tokens numerically and accept both strong `"N"` and weak `W/"N"` forms as the same revision; the response still uses the strong `"N"` form. Missing required precondition returns `428 precondition_required`; stale, malformed, or non-numeric values return `412 stale_revision`. Append-only entries and loan repayments have no ordinary PATCH/DELETE operation; their correction operations use idempotency and parent-row locks instead of If-Match.

### Authentication and ownership

- Protected API requests authenticate with `Authorization: Bearer <short-lived-access-token>`. This header is not cookie authentication and is not sent automatically by browsers.
- `POST /auth/refresh`, `POST /auth/logout`, and `GET /auth/csrf` present the opaque rotating refresh token in a cookie named `finpath_refresh`; login sets it and successful logout clears it. Store only a cryptographic token hash server-side. The frontend sends cookie credentials only to login, refresh, logout, and CSRF-token retrieval (`credentials: "include"` for Fetch or equivalent).
- Cookie attributes: `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`. Do not set a broad `Domain`; use a host-only cookie. Deployment may require `SameSite=None; Secure` for a cross-site frontend/API, but that is not sufficient CSRF protection and requires explicit approval.
- Login, refresh, logout, and CSRF-token retrieval require an exact `Origin` match against the configured allowlist; reject absent/untrusted origins for browser requests. Refresh and logout additionally require a synchronizer CSRF token in `X-CSRF-Token`. Login and refresh return a fresh session-family-bound token in JSON; `GET /auth/csrf` uses the refresh cookie and allowed Origin to re-issue a token after page reload. Validate token binding server-side; never place it in a cookie. Login Origin validation prevents login CSRF before a token exists. Validate `Sec-Fetch-Site` when present as defense in depth. Never rely on SameSite alone.
- CORS allows only explicitly configured frontend origins, never wildcard origins with credentials. Return `Access-Control-Allow-Credentials: true` only for those origins, allow the `Authorization`, `Content-Type`, `Idempotency-Key`, `If-Match`, `If-None-Match`, and `X-CSRF-Token` headers, and expose `ETag` and `Retry-After`. Frontend sends `credentials: "include"` only for login, refresh, logout, and CSRF-token retrieval; ordinary protected calls use the access token in `Authorization` and do not need cookies. Preflight must not mutate state. Keep development and production origin lists separate.
- Account states are `active`, `suspended`, and `closed`. Active accounts may use normal features. Suspended accounts are temporarily blocked; an administrator may reactivate them subject to last-active-Admin protection. Closed accounts are blocked and terminal: normal self-service and administrator workflows cannot reactivate them. State transitions are `active -> suspended` or `closed`, and `suspended -> active` or `closed`. The process/actor for initiating closure is not a route in this contract and remains implementation work.
- Every protected request verifies session revocation/expiry, current account status, and email verification. Transition to `suspended` or `closed` revokes active sessions in the same transaction. Return `403 account_suspended`, `403 account_closed`, or `403 email_verification_required` as distinct responses when applicable; invalid credentials remain generic `401 authentication_failed`, and unknown, expired, or revoked sessions otherwise return `401 authentication_required`.
- Approved authentication defaults are Argon2id password hashing, minimum password length 12, 15-minute access tokens, opaque rotating refresh tokens with a 30-day absolute and 7-day inactivity lifetime, family-wide revocation on reuse, required initial email verification before normal access with 24-hour verification tokens, and 30-minute password-reset tokens. Do not impose mandatory uppercase, number, or symbol rules. The 7-day inactivity lifetime must be enforced using session logic; the actual session model has `created_at` and `last_refreshed_at` but no inactivity-expiry column. Revoked and expired session rows are retained through the 30-day reuse-detection window after refresh-token expiry; cleanup deletes them only when `refresh_expires_at <= now() - interval '30 days'` and never deletes active sessions.
- Use a shared external rate limiter such as Redis across backend workers; do not add a PostgreSQL rate-limit table. Fail closed for sensitive authentication operations if the limiter cannot be enforced and return `429 Too Many Requests` when limited. Initial thresholds: 5 failed login attempts per account per 15 minutes; 5 registrations per IP per hour; 3 password-reset requests per account per hour; and 3 verification resends per account per hour. Redis hosting/configuration remain operational decisions.
- The frontend clears access-token and CSRF-token memory on `403 account_suspended` or `403 account_closed`, stops retrying protected requests, and displays the account-state message. It must not loop through refresh or report a generic logged-out state. An administrator may reactivate a suspended account; a closed account cannot be reactivated through normal self-service or administrator workflows.
- Account closure retains the user tombstone and financial/audit history; no hard-delete user flow is provided. Email reuse is allowed only after the prior account's identity is safely released through an approved anonymization process. Personal-data retention duration and any erasure implementation remain subject to legal/product review. The SQLAlchemy `User` model and initial migration permit exactly `active`, `suspended`, and `closed`; transition enforcement remains implementation work.
- Every financial query and mutation is scoped by the authenticated user's ID and, for nested resources, by the parent ID. Never trust a body or query-string owner ID. An Admin has no implicit read access to another user's financial records.
- Admin routes require the Admin role and active account status. New users retain the required `user` role; `admin` is additive. Only an authorized Admin or the controlled one-time bootstrap can grant Admin. Last-active-Admin protection applies to concurrent role and status changes.
- Responses for inaccessible resource IDs should use the same `404 not_found` shape as nonexistent IDs to avoid disclosing another user's records.

### Retry safety and concurrency

- `Idempotency-Key` is required for registration; create operations for transactions, loans, savings goals, savings contributions/withdrawals/corrections, trips, budgets, and trip spending; loan repayment and repayment correction; daily-plan create/rebuild and complete/miss transitions; explicit trip completion, cancellation, transaction deletion, budget deletion, or trip-spending deletion; Admin role/status changes; and any other retryable write that could create a financial record or irreversible state transition. It is not required for ordinary reads or revision-protected metadata updates.
- Keys are opaque client-generated values of 16–128 printable ASCII characters. Scope them by principal (or a stable registration operation scope for unauthenticated registration), HTTP method, and normalized route. Persist a cryptographic key hash, canonical request fingerprint, in-progress/completed state, response status/body, and expiry. Reusing the same key and fingerprint returns the original response; reusing it with a different fingerprint returns `409 idempotency_key_reused`; a concurrent duplicate waits for or receives the same completed result and must not perform a second write.
- The canonical request fingerprint includes the request payload and the normalized presence and value of conditional headers (`If-Match` and/or `If-None-Match`), so omitted or different preconditions produce a different fingerprint. Processing order for a retryable mutation is: authenticate, enforce current account status and applicable authorization, and establish the principal/owner scope; look up the scoped idempotency key and compare fingerprints; if a matching completed record exists, replay its original status, body, and applicable stored response headers even if its supplied ETag is now stale; if the same key has a different fingerprint, return `409 idempotency_key_reused`. For a new operation, validate its required conditional header inside the write transaction, then execute the mutation and persist the idempotency result atomically. Preserve the documented `428 precondition_required` and `412 stale_revision` responses for new operations.
- Retain completed idempotency results for 24 hours after completion. While an expired record still exists, do not replay it; return `idempotency_key_expired`. Once it is deleted, the key may be reused. The cleanup schedule remains an implementation detail to specify; cleanup must not interfere with in-progress requests. Completed records require stored response headers; `{}` is valid if there are no applicable headers. Never persist/replay `Set-Cookie` or authentication tokens.
- The architecture defines `idempotency_records` with scoped uniqueness, request/response hashes, replay fields, and expiration. Financial mutation and idempotency result must commit atomically. Registration must not return or set credentials: it returns an account/verification summary, and the user obtains credentials through the separate login flow. This keeps replay records free of access tokens, refresh tokens, and `Set-Cookie` values.
- Database transactions and parent-row locks remain authoritative: lock the loan for repayment/principal changes/corrections; lock the savings goal before entries, corrections, plan rebuilds, and day transitions; use the PostgreSQL transaction-scoped advisory lock for Admin role/status changes. Do not add a coordination table. A retry after a transient database failure must not create another financial entry.
- Conflicting or invalid state transitions return a stable `409` error code. Do not expose partial updates.

## 2. Success and error response shapes

Example resource response:

```json
{
  "data": {
    "id": "8b5a41db-bdf0-4b79-aeee-82f7f14f1b49",
    "amount_kyat": 125000,
    "transaction_date": "2026-11-03"
  }
}
```

Example paginated response:

```json
{
  "data": [],
  "pagination": {
    "limit": 50,
    "next_cursor": null
  }
}
```

All errors use:

```json
{
  "error": {
    "code": "repayment_exceeds_principal",
    "message": "The repayment exceeds the loan's outstanding principal.",
    "field_errors": [],
    "request_id": "b97f0b18-9a9f-45cb-a117-38a229ad809e"
  }
}
```

`field_errors` is an array of `{ "field": "...", "code": "...", "message": "..." }`. Messages are safe for display; responses must not include stack traces, SQL, tokens, password hashes, or private data belonging to another user.

| HTTP status | Use |
|---|---|
| `200` | Successful read or update. |
| `201` | Resource created. |
| `204` | Successful deletion or logout with no response body. |
| `400` | Malformed request or invalid protocol-level input. |
| `401` | Missing, invalid, expired, or revoked authentication. |
| `403` | `account_suspended` or `account_closed` when the resolved account is blocked; `email_verification_required` for an unverified account attempting normal authenticated access; otherwise authenticated but not authorized. Check account status before reporting a revoked session when the token/session still identifies the account. |
| `404` | Unknown or inaccessible resource. |
| `409` | Duplicate, state, idempotency, or financial invariant conflict. |
| `412` | Stale `If-Match` revision. |
| `422` | Structurally valid request with invalid fields or financial values. |
| `428` | Required `If-Match` or `If-None-Match` precondition is missing. |
| `429` | Rate limit exceeded; include `Retry-After` when applicable. |
| `500` | Unexpected server failure with a request ID and no sensitive internals; also used with `aggregate_out_of_range` when exact aggregate results exceed safe JSON integer representation. |

Common stable error codes include `validation_error`, `invalid_date`, `invalid_date_range`, `amount_not_safe_integer`, `amount_limit_exceeded`, `aggregate_out_of_range`, `insufficient_savings_balance`, `repayment_exceeds_principal`, `repayment_already_corrected`, `daily_plan_day_not_due`, `daily_plan_day_already_final`, `earlier_plan_day_pending`, `savings_schedule_frozen`, `goal_archived`, `account_suspended`, `account_closed`, `email_verification_required`, `last_active_admin`, `idempotency_key_reused`, `idempotency_key_expired`, `stale_revision`, `precondition_required`, and `confirmation_required`. `aggregate_out_of_range` means an exact valid aggregate cannot be safely represented as a JSON integer; the error uses the standard `{ "error": { "code", "message", "field_errors", "request_id" } }` envelope and contains no sensitive implementation details.

## 3. Endpoint inventory

`Public` means no authenticated session is required. `User` means an active authenticated user. `Admin` means an active user with the Admin role. Financial routes are always scoped to the caller, including for Admins.

### Authentication

| Method and path | Access | Contract |
|---|---|---|
| `POST /auth/register` | Public | Require `Idempotency-Key`. Atomically create an account, required User role, one settings row, and the completed idempotency result. With verification enabled the account is unverified and returns `verification_status: "required"`; with verification disabled for local development it is created verified and returns `verification_status: "not_required"`. Return `201` with the user summary and user ETag; never issue credentials or set a refresh cookie during registration. |
| `POST /auth/login` | Public | Accept email/password; on success for an active account return a short-lived access token, CSRF token, and user summary; set refresh cookie. With verification enabled, an unverified account returns `403 email_verification_required`; with verification disabled for local development, an unverified account may sign in. Invalid credentials return generic `401 authentication_failed`; valid credentials for suspended/closed accounts return `403 account_suspended`/`403 account_closed`; failures issue no credentials. |
| `POST /auth/refresh` | Refresh cookie + CSRF | Validate Origin and CSRF header, rotate refresh token in its family, set the rotated refresh cookie, and return a new access token and CSRF token. Suspended/closed accounts return their distinct `403` state errors; unknown/expired/revoked token otherwise returns `401 authentication_required`. Reuse revokes the family. |
| `POST /auth/logout` | Refresh cookie + CSRF | Validate Origin and CSRF header, revoke the current session, clear refresh cookie, and return `204`; repeat logout is safe. Suspended/closed accounts return their distinct `403` state errors when identifiable; their sessions have already been revoked. |
| `GET /auth/csrf` | Refresh cookie + Origin | Return `{ "data": { "csrf_token": "..." } }` for the associated active session family. This read-only endpoint lets the frontend recover after a page reload; it does not refresh or otherwise extend the session. Suspended/closed accounts return their distinct `403` state errors. |
| `GET /auth/me` | User | Return authenticated identity, roles, status, verification state, and timezone. Suspended/closed/unverified accounts cannot use this protected route and receive their respective `403` state error. |
| `POST /auth/change-password` | User | Verify current password, set new password, revoke other sessions; require reauthentication for the current session. Available only to verified active accounts. |
| `POST /auth/email-verification-requests` | Public | Request initial-account verification; require idempotency key and return generic `202` without disclosing whether the email exists. |
| `POST /auth/email-verification/consume` | Public | Require `Idempotency-Key`; consume a single-use token to verify the initially registered address; increment `users.revision` and return the updated verification state and user ETag. |
| `POST /auth/password-reset-requests` | Public | Request a reset message; generic `202`, no account enumeration. Do not issue a reset token for an unverified account. |
| `POST /auth/password-reset/consume` | Public | Require `Idempotency-Key`; consume a single-use token for a verified account, set a new password, and revoke existing sessions. Password reset never bypasses email verification. |
| `POST /users/me/email-change-requests` | User | Require current password and requested address; keep the current email active, store a pending address, and send a verification token. Require `Idempotency-Key`; increment and return the user ETag because the protected user representation changes. |
| `POST /users/me/email-change/consume` | Public token + pending identity | Require `Idempotency-Key`; consume a one-time token bound to the user's pending address; atomically promote it to active email only if still available, then clear pending state. Increment and return the user ETag. Return generic invalid/expired-token errors. |

Initial email verification and changing a registered email are separate flows. Initial verification is required before normal authenticated access. Until successful verification, a user can only register, request/resend initial verification, and consume an initial verification token; normal application APIs, financial features, and dashboard data are inaccessible. Verification tokens expire after 24 hours. Successful initial verification changes `email_verified_at`, increments `users.revision`, and returns the updated user ETag; the user then signs in to establish a session. Email change never replaces the active email until the new address is verified; the architecture persists `pending_email` and an email-change token purpose bound to that address. Pending-address changes, verification-state changes, and address promotion/clearing all increment the same user revision.

Task 4.1 implements the authentication endpoints above except email-change request/consume. Shared rate limiting remains unimplemented. Email-change verification remains necessary if email changes are offered.

### Profile, administration, and financial settings

| Method and path | Access | Contract |
|---|---|---|
| `GET /users/me` | User | Return the caller's profile only, with the current user ETag. |
| `PATCH /users/me` | User | Update non-security profile preferences, including `timezone`; does not change email, roles, status, or verification state. Requires `If-Match`. |
| `GET /users/me/status` | User | Return the caller's own account status and role summary. Suspended/closed accounts cannot use normal protected features. |
| `GET /admin/users` | Admin | Paginated account list with identity, status, roles, and timestamps; no financial data. |
| `GET /admin/users/{user_id}` | Admin | Read one user's administrative profile only; never return financial records; return the user's ETag. |
| `PATCH /admin/users/{user_id}/status` | Admin | Require current `If-Match` and `Idempotency-Key`. Activate or suspend an account; this ordinary administrator workflow cannot reactivate `closed` accounts. Suspension revokes its sessions atomically; reject a change that would leave no active Admin. |
| `PUT /admin/users/{user_id}/roles/admin` | Admin | Require current `If-Match` and `Idempotency-Key`. Grant the additive Admin role. Record actor and change in the audit log; caller identity is not sufficient authority to self-promote. |
| `DELETE /admin/users/{user_id}/roles/admin` | Admin | Require current `If-Match` and `Idempotency-Key`. Remove Admin while retaining the required User role. Serialize with account suspension and reject removal of the last active Admin. Return the updated user ETag. |
| `GET /admin/audit-logs` | Admin | Paginated, filtered administrative/security events. Exclude unnecessary financial details and secrets. |
| `GET /users/me/financial-settings` | User | Return the caller's single starting-balance setting and balance configuration status. |
| `PUT /users/me/financial-settings` | User | Set or replace starting balance and effective date together; both may be null to clear the setting. Requires `If-Match`; settings row is created at registration, not client-created. Starting balance is a setting, not an individual financial entry, so the one-billion-Kyat entry limit does not apply; validate storage and safe integer bounds. |

`PATCH /users/me` includes optional `timezone` using an IANA timezone identifier. The approved registration default is `Asia/Yangon`; users may change it in settings. Store event timestamps in UTC and keep financial dates date-only. A timezone change never rewrites transaction dates, historical savings-entry dates, completed plan records, or any existing scheduled savings-plan dates, including pending dates. The new timezone applies to future plans created after the change; existing pending plans are not shifted, regenerated, or reinterpreted. Invalid or unknown IANA identifiers return `422 invalid_timezone`.

Profile preference request example: `{ "timezone": "Asia/Yangon" }`; `200` returns the updated profile and a new ETag. Financial settings request example: `{ "starting_balance_kyat": -250000, "effective_date": "2026-11-03" }`; `200` returns both values, derived account-balance status, and a new ETag. Both fields must be null together to clear the setting; one null and one non-null returns `422 validation_error`.

Task 4.6 implements `GET` and `PUT /users/me/financial-settings`. Both routes use the Task 4.1 authenticated-user dependency and operate only on the caller's single registration-created settings row; there is no client-supplied user identifier and no way to read or write another user's settings. `GET` returns the account-balance resource shape used by the Dashboard (`status`, `starting_balance_kyat`, `effective_date`, `amount_kyat`, `as_of_date`) and a quoted strong `ETag` derived from `user_financial_settings.revision`. `PUT` requires the current `If-Match`, replaces both fields together, increments the revision atomically, and returns the updated resource and ETag; missing preconditions return `428 precondition_required`, and malformed or stale values return `412 stale_revision`. A CDN edge may weaken the strong validator to `W/"N"` while preserving the revision, so `If-Match` accepts either `"N"` or `W/"N"` as the same numeric revision; `GET` continues to emit the canonical strong form. Sending both fields as null clears the setting and returns `not_configured` with a null amount. A starting balance may be zero or negative and is validated only against the safe JSON integer range, not the individual-entry cap; non-integer JSON values are rejected without rounding. `effective_date` accepts only a real local-calendar `YYYY-MM-DD` date. The Dashboard continues to use the shared balance service, so its configured balance is the saved starting balance plus transaction income minus expenses, plus borrowed loan principal, minus unreversed loan repayments dated strictly after the effective date, minus recognized travel expense. Recognized travel is an allocation layer independent of the effective-date boundary: active trips count the greater of planned budget and actual spending, while completed or cancelled trips count actual spending only. Savings entries remain excluded. No schema change or migration was required for financial settings because registration already creates the row and `finpath_app` already holds the required `SELECT` and column-level `UPDATE` privileges.

Admin status request body example: `{ "status": "suspended" }`; the status lifecycle also includes terminal `closed`, but this ordinary endpoint only activates or suspends and cannot reopen closed accounts. The current SQLAlchemy status check must be updated to include `closed` before migration. `200` returns the updated administrative account summary and ETag. Role grant via `PUT` returns `200` with the role assignment and updated user ETag; role removal returns `204` with the updated user ETag. These writes require Admin authorization, the target user's current `If-Match`, an `Idempotency-Key`, and an audit row in the same transaction. All Admin role/status mutations use the same PostgreSQL transaction-scoped advisory lock; re-read the target and active-Admin count under that lock and reject any change that would leave no active Admin with `409 last_active_admin`.

### Categories and transactions

| Method and path | Access | Contract |
|---|---|---|
| `GET /transaction-categories` | User | List active system-managed predefined categories, optionally filtered by `type=income\|expense`; users cannot create categories and no category mutation route is provided. |
| `GET /transactions` | User | List caller-owned transactions; supports `type`, `category_id`, inclusive `from`/`to`, and `q` (case-insensitive substring match on description only). |
| `POST /transactions` | User | Create an income or expense transaction. |
| `GET /transactions/{transaction_id}` | User | Read one caller-owned transaction. |
| `PATCH /transactions/{transaction_id}` | User | Update permitted transaction fields; validate category/type consistency and use `If-Match`. |
| `DELETE /transactions/{transaction_id}` | User | Delete a caller-owned transaction using `If-Match`; reports are recalculated from remaining transactions. |

Transaction fields: `id`, `description`, `type` (`income` or `expense`), `category_id`, `category` (display snapshot/name), `amount_kyat`, `transaction_date`, `created_at`, and `updated_at`. `description` is required and trimmed; preserve the current 80-character limit. Amounts must be positive, safe integers within the configurable individual-entry limit. Category type must match transaction type.

### Loans and repayments

| Method and path | Access | Contract |
|---|---|---|
| `GET /loans` | User | List caller-owned loans with principal, total repaid, and outstanding principal. |
| `POST /loans` | User | Create a loan with name, original principal, and start date. |
| `GET /loans/{loan_id}` | User | Read one caller-owned loan and its derived totals. |
| `PATCH /loans/{loan_id}` | User | Update allowed loan details; principal changes lock the loan and cannot go below valid repayments. |
| `GET /loans/{loan_id}/repayments` | User | List repayments scoped to the caller and loan. |
| `POST /loans/{loan_id}/repayments` | User | Require `Idempotency-Key`; add a principal-only repayment. Lock loan, validate outstanding principal and safe totals, then insert atomically; return the repayment ETag and increment the loan revision. |
| `GET /loans/{loan_id}/repayments/{repayment_id}` | User | Read one repayment under the caller-owned loan; return an ETag for its derived correction status. |
| `POST /loans/{loan_id}/repayments/{repayment_id}/corrections` | User | Require `Idempotency-Key`; append a full reversal with reason, preserving the original repayment facts. Lock the loan; allow at most one reversal of an original repayment. Increment both repayment and loan revisions atomically. |

Loan fields include `id`, `name`, `principal_kyat`, `start_date`, `total_repaid_kyat`, `outstanding_principal_kyat`, and timestamps. Repayment fields include `id`, `amount_kyat`, `repayment_date`, optional `note`, `created_at`, and immutable correction status. A correction record includes `id`, `original_repayment_id`, `amount_kyat` (equal to the reversed repayment), `reason`, `created_at`, and `created_by_user_id`. Return the original repayment unchanged alongside its correction status and the correction record. Corrections are append-only and cannot themselves be corrected; to replace an incorrect payment, reverse it and create a new repayment with a separate idempotency key. A repeat with the same key returns the original correction response; duplicate correction of the same repayment returns `409 repayment_already_corrected`.

Principal and each repayment are positive whole-Kyat amounts subject to the configurable entry limit; this does not cap the total repaid or outstanding-principal aggregate. Outstanding principal is original principal less unreversed repayment rows. Validate principal changes against the checked total of valid repayments. Lock the loan for repayment, principal change, and correction in one transaction so concurrent operations cannot overpay or corrupt totals. Loan deletion is absent and restricted by default. Repayments and corrections are not transaction expenses, but the account balance treats borrowed principal as a cash inflow on `start_date` and each unreversed repayment as a cash outflow on `repayment_date`. The architecture persists full-reversal correction records with a unique original-repayment relationship.

Task 4.3 implements these loan, repayment, and correction endpoints under `/api/v1/loans` with owner scoping, strict whole-Kyat and date validation, ETags/`If-Match` on loan updates, scoped idempotency for creation/repayment/correction, loan locking for financial mutations, derived total-repaid and outstanding-principal totals, and append-only full-reversal corrections. Loan deletion remains unimplemented by design.

### Savings goals, entries, and daily plans

| Method and path | Access | Contract |
|---|---|---|
| `GET /savings/goals` | User | List caller-owned goals with actual balance, target, progress, and optional plan summary. Query `status` accepts `active`, `archived`, or `all`; default is `active`. Archived history is available with `status=archived` or `all`. |
| `POST /savings/goals` | User | Create a goal. |
| `GET /savings/goals/{goal_id}` | User | Read one goal and its derived actual balance. |
| `PATCH /savings/goals/{goal_id}` | User | Update name, target, or presentation tone on an active goal; use `If-Match`. Restore an archived goal before changing metadata. |
| `POST /savings/goals/{goal_id}/archive` | User | Require `Idempotency-Key` and current `If-Match`; mark goal archived, preserving all entries, corrections, plan settings, day states, and snapshots. No financial history is deleted. |
| `POST /savings/goals/{goal_id}/restore` | User | Require `Idempotency-Key` and current `If-Match`; reactivate an archived goal. Historical entries and schedule remain unchanged. |
| `GET /savings/goals/{goal_id}/entries` | User | List append-only actual contributions, withdrawals, and compensating entries. |
| `POST /savings/goals/{goal_id}/entries/contributions` | User | Require `Idempotency-Key`; append a contribution to an active goal. An optional scheduled-day association is accepted only for a pending matching day. Increment the goal revision atomically; if pending-day calculations or the daily-plan GET representation change, also increment the plan revision and return its updated ETag. |
| `POST /savings/goals/{goal_id}/entries/withdrawals` | User | Require `Idempotency-Key`; append a withdrawal to an active goal only if the resulting actual savings balance is non-negative. Increment the goal revision atomically; increment the plan revision only if the daily-plan GET representation changes. |
| `POST /savings/goals/{goal_id}/entries/{entry_id}/correction` | User | Require `Idempotency-Key`; append an opposite-type compensating entry with a required reason to an active goal. Never edit/delete the original. A replacement is a separate entry. Increment the goal revision atomically and increment the plan revision if pending-day calculations or its GET representation change. |
| `GET /savings/goals/{goal_id}/daily-plan` | User | Return settings and at most 365 scheduled days, including status and carried-forward requirements; return the ETag derived from `savings_plans.revision`. |
| `PUT /savings/goals/{goal_id}/daily-plan` | User | Require `Idempotency-Key`; when creating a plan, require `If-None-Match: *` and return `412` if one already exists; when replacing, require current plan `If-Match`. Create/rebuild atomically. Before any completed/missed day, retain entries, replace pending schedule rows, and reconcile contributions; after a terminal day, schedule-defining settings are frozen. Increment the plan revision atomically and return its new ETag. |
| `POST /savings/goals/{goal_id}/daily-plan/days/{day_id}/complete` | User | Require `Idempotency-Key`; complete only if the scheduled date is today in the user's configured timezone. Reconcile eligible contributions and create only a positive shortfall contribution, exactly once. Increment the plan revision for the changed day and any pending carry-forward requirements; return the updated plan ETag. |
| `POST /savings/goals/{goal_id}/daily-plan/days/{day_id}/miss` | User | Require `Idempotency-Key`; mark only an overdue pending day (scheduled date before today in user's timezone) missed. Carry forward only its unpaid requirement; do not create actual savings for planned or missed amounts. Increment the plan revision for the changed day and any pending carry-forward requirements; return the updated plan ETag. |

Goal fields: `id`, `name`, `target_kyat`, `tone`, `status` (`active` or `archived`), `actual_balance_kyat`, and optional `daily_plan`. Actual savings is the checked sum of contributions less withdrawals. Planned amounts, missed amounts, and outstanding commitments are excluded. A target is not an individual financial entry and is not subject to the one-billion-Kyat entry cap, but must fit storage and the safe JSON integer range.

Entry fields: `id`, `entry_type` (`contribution` or `withdrawal`), `amount_kyat`, `entry_date`, optional `note`, optional `plan_day_id`, optional `correction_of_entry_id`, optional `correction_reason`, and `created_at`. Entries are append-only. A correction does not change completed/missed status or its historical completion snapshot; actual savings reports use the corrected entry ledger.

Plan fields: `start_date`, `duration_days`, `daily_amount_kyat`, and `days`. Duration is an integer from 1 through **365 days (one year)**. The daily-plan ETag is based on the plan revision, which increments atomically whenever this returned representation changes, including terminal transitions, pending carry/requirement recalculation, affected savings entries/corrections, and plan settings/rebuild. For a pre-terminal rebuild, keep all entries, detach old pending-day references, replace the pending schedule, reconcile existing contributions by goal/date once, and create no contribution during rebuild. Completion records only `max(0, required_amount_kyat - eligible_contributions)`. A missed day carries its unpaid requirement forward; a missed amount is never itself savings. Completed/missed day states and reconciliation snapshots are historical and immutable; later corrections may affect actual totals but do not rewrite terminal snapshots.

Daily-plan dates are date-only calendar dates fixed when the plan is created. The server calculates `today` from the user's current IANA timezone; clients cannot submit or override `today`. New users default to `Asia/Yangon`, and users may change timezone. A change does not rewrite existing financial dates, completed historical plan records, or existing scheduled plan dates; the new timezone applies to plans created afterward. A day is completable only when `planned_date = today`; it is missable only when `planned_date < today`; future days remain pending. A later day cannot be completed while an earlier scheduled day remains overdue and pending. Return `409 earlier_plan_day_pending` and, when appropriate, identify the earliest blocking date as `blocking_date` in `field_errors`. Resolving that earlier day under the existing miss/carry-forward rules allows progression. A repeated transition with the same idempotency key returns its original response; a different key against a terminal day returns `409 daily_plan_day_already_final`.

For each pending scheduled date, eligible contribution is the net unreversed contribution entries for that goal with `entry_date = planned_date`, plus any contribution explicitly associated with that pending day, counting each entry once. Corrections to pending-day contributions update the net eligible amount and recompute that day's shortfall and later pending carry under the goal lock; withdrawals affect actual goal balance but do not satisfy or reduce a scheduled contribution requirement. After a day becomes completed or missed, its status and `actual_amount_kyat` snapshot never change. A later unlinked contribution remains actual savings but does not rewrite a terminal snapshot or settle a missed obligation. Corrections to terminal-day entries update actual ledger balance only; they do not rewrite historical day status/snapshot. New entries cannot be attached to a terminal day. Archive blocks all new entries, corrections, plan mutations, and transitions; restore is required before further activity.

Carry is calculated sequentially: `required(day) = daily_amount + carry_in(day)`. On a missed day, `unpaid(day) = max(0, required(day) - eligible_contributions(day))`, and the next chronological day receives `carry_in(next) = unpaid(day)`. Consecutive missed days therefore carry the prior unpaid amount plus the next daily base. On a completed day, completion inserts only `shortfall = max(0, required(day) - eligible_contributions(day))` as a contribution, and its unpaid carry out is zero. Contributions above the requirement remain actual savings and do not lower future daily base amounts. Completing/missing is serialized with contributions, withdrawals, corrections, and rebuilds using a parent-goal lock.

Task 4.4 implements the savings-goal create/list/read/update endpoints and the contribution list/create endpoints under `/api/v1/savings/goals` with owner scoping, strict whole-Kyat and date validation, ETags/`If-Match` on goal updates, scoped idempotency for goal and contribution creation, goal-row locking for contributions and metadata changes, and a derived actual balance from append-only entries. Goal responses include `remaining_kyat = max(0, target_kyat - actual_balance_kyat)`; this is a derived presentation field, not stored state. Contributions may exceed the goal target because the contract does not cap savings entries at the target. Withdrawals, corrections, archive/restore, daily plans, plan-day transitions, and scheduled-day association remain unimplemented.

### Trips, budgets, and spending

| Method and path | Access | Contract |
|---|---|---|
| `GET /trips` | User | List caller-owned trips with budget and spending totals. |
| `POST /trips` | User | Create a trip with name, destination, and date range. |
| `GET /trips/{trip_id}` | User | Read one caller-owned trip and nested totals. |
| `PATCH /trips/{trip_id}` | User | Update trip details; end date cannot precede start date. |
| `POST /trips/{trip_id}/complete` | User | Mark an active trip completed with `If-Match` and `Idempotency-Key`; release unused allocation while keeping actual spending. |
| `DELETE /trips/{trip_id}` | User | Require explicit confirmation (`{ "confirm": true }`) and `Idempotency-Key`; cancel the trip, hide it from the active list, release unused allocation, and preserve budgets/spending history. |
| `GET /trips/{trip_id}/budgets` | User | List budgets scoped to the trip. |
| `POST /trips/{trip_id}/budgets` | User | Create a category budget; budget is a planned allocation, not an expense. |
| `GET /trips/{trip_id}/budgets/{budget_id}` | User | Read one budget scoped to the caller and trip; return its ETag. |
| `PATCH /trips/{trip_id}/budgets/{budget_id}` | User | Update a same-trip budget. Existing spending remains attached if applicable. |
| `DELETE /trips/{trip_id}/budgets/{budget_id}` | User | Delete only the budget. Preserve its spending by clearing the budget reference and retaining the spending category snapshot. |
| `GET /trips/{trip_id}/spending` | User | List trip-owned spending, including unbudgeted entries. |
| `POST /trips/{trip_id}/spending` | User | Create trip spending with description, category, amount, date, and optional same-trip budget ID. |
| `GET /trips/{trip_id}/spending/{spending_id}` | User | Read one spending record scoped to the caller and trip; return its ETag. |
| `PATCH /trips/{trip_id}/spending/{spending_id}` | User | Update a same-trip entry; validate owner, parent, and optional budget relationship. |
| `DELETE /trips/{trip_id}/spending/{spending_id}` | User | Delete one caller-owned trip-spending entry. |
| `POST /trips/import` | User | Validate and atomically import a JSON `trips` document; exact duplicates are skipped and reported. Any invalid row rejects the whole document with field-level errors. |

Trip fields: `id`, `name`, `destination`, `start_date`, `end_date`, `status` (`active`, `completed`, or `cancelled`), and derived `budget_kyat` / `spending_kyat` totals. Budget fields: `id`, `category`, `budget_kyat`. Spending fields: `id`, `description`, `category`, optional `category_budget_id`, `amount_kyat`, `spending_date`. Budgets may be zero; individual spending entries must be positive and within the configured entry limit. Trip spending has no transaction link, but recognized travel expense affects Dashboard expenses and the account balance: active trips recognize `max(budget, spending)`, while completed/cancelled trips recognize actual spending only. The recognized total is counted exactly once.

Task 5.8 implements the trip, planned-expense (category-budget), and actual-spending routes under `/api/v1/trips` with owner scoping, strict whole-Kyat and real-calendar date validation, ETags/`If-Match` on trip and nested updates/deletes, scoped idempotency, and trip-row locking for nested mutations. Trip totals are derived, not stored: estimated total is the budget sum and actual spending is the spending sum, so spending may exceed the plan. Deleting a budget clears its spending's budget reference while preserving the category snapshot. The import route validates the entire document before writing, serializes imports per user with a transaction-scoped advisory lock, and skips exact duplicates (trip by owner/name/destination/dates, budget by trip/category, spending by trip/description/category/amount/date). Imported trip spending is stored only in the trip tables and never creates a transaction or a separate accounting row.

Task 5.9 adds explicit trip status and allocation-aware accounting. New trips are `active`; existing rows default to `active`. Active trips recognize the greater of planned budget and actual spending, while completed or cancelled trips recognize actual spending only. Completion releases unused allocation and is idempotent because recognition is derived from status and child rows rather than stored entries. Deletion is a cancellation: the trip is hidden from the default list, unused allocation is released, and budgets/spending are preserved so genuine expenses are not erased. The API adds `travel_expense_kyat` and `total_expense_kyat` to the Dashboard response and subtracts the same recognized total once in `calculate_account_balance`. The `c4f7a1b9e2d6` migration adds the status column, check constraint, and runtime column grant without changing existing data.

### Dashboard, reports, and CSV

| Method and path | Access | Contract |
|---|---|---|
| `GET /dashboard/overview` | User | Return a consistent Phase 1 snapshot of balance state, transaction totals, recent transactions, loan totals/counts, and actual savings progress. Optional `recent_limit` is 1–50 and defaults to 5. |
| `GET /reports/account-balance` | User | Return the manually configured balance and eligible-transaction calculation. Deferred; not implemented by Task 4.5. |
| `GET /reports/cash-flow?from=YYYY-MM-DD&to=YYYY-MM-DD` | User | Return monthly transaction income and expenses; `from` and `to` are inclusive. Deferred; not implemented by Task 4.5. |
| `GET /reports/expenses-by-category?from=YYYY-MM-DD&to=YYYY-MM-DD` | User | Return expense transaction totals by category; date bounds are inclusive. Deferred; not implemented by Task 4.5. |
| `GET /reports/export.csv` | User | Export the same caller-scoped Dashboard report rows as CSV (`text/csv; charset=utf-8`) with a UTF-8 BOM and `Content-Disposition: attachment; filename="finpath-dashboard-report.csv"`. Deferred; not implemented by Task 4.5. |

The account balance response explicitly distinguishes configuration state:

```json
{
  "data": {
    "status": "not_configured",
    "starting_balance_kyat": null,
    "effective_date": null,
    "amount_kyat": null,
    "as_of_date": "2026-11-03"
  }
}
```

When configured, `status` is `configured`, both starting-balance fields are non-null, and `amount_kyat` is the derived balance. The formula is starting balance plus transaction income minus transaction expenses, plus borrowed loan principal received on `loans.start_date`, minus unreversed loan repayments on `loan_repayments.repayment_date`, with each movement dated **strictly after** the effective date, minus recognized travel expense. Movements on or before that date are excluded. Negative results are valid. Savings transfers remain excluded. Recognized travel is not date-filtered by the effective date: active trips subtract `max(planned budget, actual spending)` and completed/cancelled trips subtract actual spending only. A fully reversed repayment is not a cash outflow. Loan principal and repayments are counted here once and are never also transaction expenses.

Task 4.5 implements the Phase 1 summary subset of `GET /dashboard/overview`. The implemented response uses these fields:

```json
{
  "data": {
    "account_balance": {
      "status": "configured",
      "starting_balance_kyat": 800000,
      "effective_date": "2026-10-31",
      "amount_kyat": 825000,
      "as_of_date": "2026-11-03"
    },
    "all_time_income_kyat": 50000,
    "all_time_expense_kyat": 25000,
    "travel_expense_kyat": 0,
    "total_expense_kyat": 25000,
    "current_month_income_kyat": 50000,
    "current_month_expense_kyat": 25000,
    "outstanding_loan_principal_kyat": 100000,
    "active_loan_count": 1,
    "loan_repayments_kyat": 10000,
    "actual_savings_balance_kyat": 30000,
    "savings_target_kyat": 100000,
    "active_savings_goal_count": 2,
    "savings_goals": [],
    "recent_transactions": []
  }
}
```

`recent_transactions` returns the caller's newest transactions first, ordered by `transaction_date` descending and then `id` descending, using the same transaction resource shape as `GET /transactions`. `savings_goals` returns the caller-owned goals with the same derived `actual_balance_kyat` and `remaining_kyat` fields as the Savings API. `active_loan_count` counts loans with outstanding principal greater than zero because the current loan data has no separate status field. Savings totals sum all caller-owned goals, including archived goals; `active_savings_goal_count` filters to active goals.

The dashboard reads authoritative owner-scoped records in one repeatable-read transaction. Use checked integer arithmetic. `all_time_expense_kyat` and `current_month_expense_kyat` include transactions only; `travel_expense_kyat` is the recognized travel total and `total_expense_kyat` is the checked sum of all-time transaction expenses plus recognized travel. Loan repayments are separate, savings is actual contributions less withdrawals, and per-trip travel rows are separate. The account balance includes loan cash flow (borrowed principal minus unreversed repayments) exactly once, in addition to starting balance and transaction income/expenses, and subtracts the same recognized travel total once. Never add planned savings to expenses or the balance, and never add trip spending twice. Six-month cash flow, expense categories, per-trip rows, the separate report endpoints, and CSV export remain deferred and are not returned by the overview.

Report and resource endpoints use only the filters listed in their endpoint description. `q` is supported only by `GET /transactions`, where it is a case-insensitive substring match against transaction description; it is not full-text search. Date range filters use inclusive local-calendar dates and reject `from > to`.

CSV columns preserve the existing Dashboard report shape: `Section`, `Record`, `Date or period`, `Category`, `Amount (MMK)`, `Comparison (MMK)`, `Status or notes`. Amounts are whole Kyat, not decimal values. Preserve CSV quoting/escaping for commas, quotes, and line breaks. Apply formula-injection protection to every untrusted text cell: if leading whitespace/control characters are followed by `=`, `+`, `-`, or `@`, prefix the text with an apostrophe before normal CSV escaping. Do not apply that transformation to numeric financial fields. This protects descriptions, categories, goal/trip names, destinations, notes, and other user-controlled text without corrupting amounts. The export must not expose other users' data.

## 4. Request and response examples

### Authentication lifecycle

Registration accepts `{ "email": "...", "password": "..." }`, requires `Idempotency-Key`, and returns `201` with `{ "data": { "id": "...", "roles": ["user"], "status": "active", "verification_status": "required" } }`. It never issues credentials or a refresh cookie. The account may only use registration, initial email verification, and verification-resend functionality until verification succeeds. A password reset does not bypass the verification requirement.

Login accepts `{ "email": "...", "password": "..." }`. On success for an active account whose verification requirement is satisfied, `200` returns `{ "data": { "access_token": "...", "csrf_token": "...", "token_type": "Bearer", "expires_in": 900, "user": {} } }` and sets the refresh cookie; the 900-second access-token lifetime is approved. When verification is enabled, valid credentials for an unverified account return `403 email_verification_required` and never issue credentials or set the refresh cookie; local development may disable verification as described in section 1. The frontend keeps the CSRF token in memory and sends it in `X-CSRF-Token`; after reload it calls `GET /auth/csrf` with credentials to reacquire one. Refresh uses the cookie and CSRF header, rotates the refresh cookie, and returns new access and CSRF tokens. Logout uses the refresh cookie and CSRF header, revokes the session, clears the refresh cookie, and returns `204`. Suspended and closed account responses follow the distinct `403 account_suspended` and `403 account_closed` policies below.

### Create a transaction

```http
POST /api/v1/transactions
Authorization: Bearer <access-token>
Content-Type: application/json
Idempotency-Key: 6c1a2911-2db1-44b5-9546-74c592a4d292
```

```json
{
  "description": "Market",
  "type": "expense",
  "category_id": "465b2a01-a014-42d7-aeac-b8b8b21ec94a",
  "amount_kyat": 12500,
  "transaction_date": "2026-11-03"
}
```

`201 Created` returns the created record with server-generated `id`, `category` display name, timestamps, and `ETag`. A supplied `user_id` is not accepted as an ownership selector.

### Record a repayment

```http
POST /api/v1/loans/504fdcfe-5f84-4fbd-83c6-40d57da315bc/repayments
Authorization: Bearer <access-token>
Content-Type: application/json
Idempotency-Key: 5d621669-0e22-47c6-83d6-e5d7e07b282a
```

```json
{
  "amount_kyat": 20000,
  "repayment_date": "2026-11-03",
  "note": "Cash payment"
}
```

If a concurrent repayment or principal edit means the amount exceeds outstanding principal, return `409 repayment_exceeds_principal`; do not insert a partial repayment.

### Append a savings contribution

```http
POST /api/v1/savings/goals/0d52213f-10ed-4648-9e45-971d301df387/entries/contributions
Authorization: Bearer <access-token>
Content-Type: application/json
Idempotency-Key: 1e4fac63-972c-45ea-9fd5-d50f5fc34299
```

```json
{
  "amount_kyat": 5000,
  "entry_date": "2026-11-03",
  "note": "Weekly deposit"
}
```

An optional `plan_day_id` may only associate a contribution with a matching pending day. Completion reconciles eligible existing entries, creates only the shortfall, and is serialized with all competing savings mutations.

### Complete or miss a savings-plan day

Both operations require an active, non-archived goal, an Idempotency-Key, and caller ownership of the goal and day. There is no client-supplied date or amount.

```http
POST /api/v1/savings/goals/{goal_id}/daily-plan/days/{day_id}/complete
Authorization: Bearer <access-token>
Content-Type: application/json
Idempotency-Key: 8aca9f44-5f6e-471e-b0aa-59720bb3c262
```

`200 OK` returns `{ "data": { "day": { "id": "...", "status": "completed", "completion_date": "2026-11-03", "required_amount_kyat": 5000, "eligible_contributions_kyat": 3000, "actual_amount_kyat": 5000 }, "created_contribution": { ... } | null, "goal_actual_balance_kyat": 5000 }`. `created_contribution` is null when eligible existing contributions meet/exceed the requirement; otherwise it is the shortfall entry. Repeating the same key and payload returns the original response.

The miss endpoint returns `{ "data": { "day": { "id": "...", "status": "missed", "actual_amount_kyat": null, "reconciled_contributions_kyat": 3000, "unpaid_carry_forward_kyat": 2000 }, "created_contribution": null } }`. `reconciled_contributions_kyat` and `unpaid_carry_forward_kyat` are immutable snapshots; missed amounts never become savings. A not-yet-due completion or non-overdue miss returns `409 daily_plan_day_not_due`; a terminal day with another key returns `409 daily_plan_day_already_final`; an archived goal returns `409 goal_archived`.

### Correct a loan repayment

```http
POST /api/v1/loans/{loan_id}/repayments/{repayment_id}/corrections
Authorization: Bearer <access-token>
Content-Type: application/json
Idempotency-Key: 14c71af5-57ab-4b54-a4c5-97e64ec407cb
```

```json
{
  "reason": "Payment was recorded twice"
}
```

`201 Created` returns `{ "data": { "original_repayment": { "id": "...", "amount_kyat": 20000, "repayment_date": "2026-11-03", "corrected": true }, "correction": { "id": "...", "original_repayment_id": "...", "amount_kyat": 20000, "type": "reversal", "reason": "Payment was recorded twice", "created_by_user_id": "...", "created_at": "2026-11-03T04:00:00Z" }, "outstanding_principal_kyat": 100000 } }`. The original repayment's financial fields are immutable; only its derived correction status and revision change. Reversal and valid-total recomputation occur atomically under the loan lock. The response includes the updated repayment ETag; fetch the loan to obtain its new ETag. Require a non-empty trimmed reason up to 200 characters. Return `409 repayment_already_corrected` for a second correction attempt; return `404` for another user's loan or repayment. The correction's amount equals the original repayment and is not a new positive repayment; it does not consume extra principal. To replace an erroneous repayment, post the reversal then submit a separate repayment with its own idempotency key.

### Archive or restore a savings goal

```http
POST /api/v1/savings/goals/{goal_id}/archive
Authorization: Bearer <access-token>
If-Match: "12"
Idempotency-Key: 50343101-8b2f-4efb-88be-bf57a54cb43a
Content-Type: application/json
```

```json
{ "confirm": true }
```

`200 OK` returns the goal with `status: "archived"` and a new ETag. Archive changes availability, not financial history; entries, corrections, daily plan, and completed/missed snapshots remain readable. Require confirmation and reject absent/false confirmation with `422 confirmation_required`. Archived goals reject entry, correction, and plan mutations with `409 goal_archived`. `POST /restore` has the same preconditions and returns `status: "active"`; it permits future activity but never rewrites archived history. Permanent erasure is not part of this API while retention/erasure requirements remain undecided.

### Blocked-account responses

When an otherwise identifiable account is suspended, login with valid credentials, refresh, logout, and protected routes return `403 account_suspended`. A closed account is likewise blocked and returns `403 account_closed`; neither state grants normal application access. The response body uses the standard error envelope:

```json
{
  "error": {
    "code": "account_suspended",
    "message": "This account cannot access the application. Contact support for assistance.",
    "field_errors": [],
    "request_id": "b97f0b18-9a9f-45cb-a117-38a229ad809e"
  }
}
```

Status is `403`. For `account_closed`, the server must not offer a normal reactivation path. On either blocked-account response the frontend clears access and CSRF tokens, clears the refresh cookie when the request reaches the auth API, stops retries, and shows the account-status message. Suspension/closure has already revoked active server-side sessions. Unknown/expired/revoked sessions for other accounts remain `401 authentication_required`; invalid login credentials remain generic `401 authentication_failed`.

### Email verification lifecycle

- Initial verification is required: registration creates the account with an unverified active email, returns `verification_required`, and does not issue protected credentials. The public verification-request/resend endpoint accepts `{ "email": "..." }` and returns generic `202`; consume accepts `{ "token": "..." }` and returns `{ "data": { "email_verified": true } }` with the updated user ETag. Tokens expire after 24 hours. Successful verification enables later login; it does not create a session.
- Email change: the authenticated request accepts `{ "new_email": "...", "current_password": "..." }`; it returns `202` with `verification_pending` and does not change the active email, plus the updated user ETag. The consume endpoint returns the new active email plus verification timestamp and user ETag only after atomic promotion.
- Do not mark an unverified address as verified. Email change must not promote an unverified replacement. Unverified users cannot use normal APIs, financial features, or dashboard data; a password reset does not bypass this gate.
- Errors include `409 email_already_in_use`, `422 invalid_or_expired_verification_token`, and the distinct blocked-account responses `403 account_suspended` and `403 account_closed`; request endpoints remain generic to prevent account enumeration.

## 5. Authorization matrix

| Operation | Public/unauthenticated | Active User | Active Admin |
|---|---:|---:|---:|
| Register, login, refresh, request/consume verification or reset token | Yes, subject to rate limits | Yes | Yes |
| Read/update own profile and own financial settings | No | Own account only | Own account only |
| Read/write transactions, loans, savings, trips, and reports | No | Own records only | Own records only |
| Manage account status, role assignments, and audit logs | No | No | Yes, administrative data only |
| Read another user's financial records | No | No | No |
| Use protected routes while suspended | No | No | No |
| Use normal authenticated routes while email is unverified | No | No | No |
| Bootstrap first Admin | No public route | No | Controlled one-time CLI only |

Administrative operations are audited with actor, target, action, timestamp, and a limited non-sensitive context. Audit records must not include passwords, tokens, or unnecessary financial descriptions/amounts. Status/role changes and the one-time bootstrap event are written in the same transaction as the mutation; failure to write the audit event rolls the mutation back.

### Account lifecycle and financial immutability

The supported account states are `active`, `suspended`, and `closed`. `active` permits normal access. `suspended` is a temporary administrative block and may be reactivated by an authorized administrator. `closed` is a terminal tombstone: it blocks normal access, revokes active sessions, retains financial/audit history, and cannot be reactivated through normal self-service or administrator workflows. Allowed state transitions are `active -> suspended` or `closed`, and `suspended -> active` or `closed`. This contract does not define a closure endpoint or who initiates closure; that route/service detail is deferred. The SQLAlchemy model and initial migration permit all three statuses; transition enforcement remains implementation work.

Immutability applies to historical events, not every financial table. The complete approved per-table lifecycle, permitted operations, minimum runtime privileges, exceptions, cleanup, and FK behavior are in the **Approved 20-table financial data protection matrix** in `BACKEND_ARCHITECTURE.md`; that matrix is the canonical source:

- Loan repayment facts cannot be edited or deleted. Correct them by appending one full-reversal row to `loan_repayment_corrections`; correction rows are append-only and cannot themselves be corrected. A repayment's derived revision may change when its correction status changes, but its original financial facts do not.
- Savings entries cannot be edited or deleted. Correct an entry only by appending the existing opposite-type correction entry with its reason and reference; retain the correction chain. Completed/missed daily-plan status and snapshots are immutable. Pending schedule rows may be recalculated/rebuilt according to the daily-plan rules.
- Administrative audit events are append-only.
- Idempotency records are intentionally mutable for in-progress/completed transitions and expiry cleanup. Expiry cleanup must not remove or disrupt in-progress operations; it must follow the 24-hour replay and expired-key contract.
- Other resources may be edited/deleted only where explicitly allowed by their endpoint contract, with owner scoping, ETag/revision checks, atomic financial validation, and history safeguards. This does not make all transaction, loan, savings, trip, or plan tables blanket-immutable.

The policies remain the approved contract. Tasks 3.8c and 3.8d added database protection functions/triggers, checks, per-table/column privileges, and a restricted cleanup function to the initial Alembic revision `7270e464aca9`. Task 3.8e applied and verified that revision on the dedicated `finpath_test` database, and Task 4.0 applied and verified it on the development `finpath` database. Task 4.1 added the authentication foundation and registration-identity guards in revisions `c7a4e2f19b30`, `e1b9c0d4a7f2`, and `f4a1c2d3e5b6`, applied on both databases. A local-development email-verification configuration change added revision `b9e4d2a7c1f3`, which grants the registration identity only `INSERT (email_verified_at)` and is also applied on both databases. Real PostgreSQL 17 tests exercise the definitions using isolated schemas and the actual `finpath_app` and `finpath_registration` identities.

Both service validation and database-level protection are required for these invariants: repayment correction amount equals its original repayment; savings correction amount/type obey the matching-amount and opposite-type rule; original repayment and savings facts cannot be changed or deleted; terminal savings-plan days cannot be changed or deleted; and `savings_entries.plan_day_id` may be detached only in an eligible pending-plan rebuild. Task 3.8c supplies narrowly scoped trigger definitions for these cross-row and lifecycle invariants in the initial revision. They permit only the documented repayment-revision and savings-detachment exceptions and preserve the audit target FK behavior. Approved trip/budget cascades remain governed by their existing FK behavior.

Restrict `INSERT` columns as well as `UPDATE` columns where needed. Normal registration must not permit setting privileged roles, bypassing account verification, or assigning arbitrary ownership identifiers. Financial ownership and relationship fields require service validation and database constraints; column privileges alone do not enforce business authorization. Runtime grants must be per-table/per-column, with no broad `GRANT ALL`, and role/reference-data management remains restricted.

Do not grant unrestricted runtime `DELETE` merely to support cleanup. The initial revision defines the zero-argument `SECURITY DEFINER` function `cleanup_expired_operational_records()` with a fixed `pg_catalog` search path and schema-qualified references. `PUBLIC` has no EXECUTE privilege; `finpath_app` has only EXECUTE, not direct DELETE on operational tables. The function deletes only expired action tokens, sessions whose `refresh_expires_at` is at least 30 days in the past, and completed idempotency rows whose stored expiry has passed and whose completion was at least 24 hours earlier (both conditions required). In-progress/recent idempotency rows, active sessions, and all financial/audit history are preserved. No cleanup schedule is introduced.

The approved `user_roles` matrix permits authorized Admin role-assignment DML. Task 3.8d explicitly deferred runtime `INSERT`/`DELETE` for `finpath_app` because requests share that identity, so PostgreSQL privileges cannot distinguish Admin operations from registration. Task 4.1 added the separate least-privilege `finpath_registration` identity, which can insert only role ID `1` (`user`) through a non-`SECURITY DEFINER` guard; `finpath_app` still has `SELECT` only on `user_roles`. A separately approved design is still required before enabling Admin role assignment. Admin role management and first-admin bootstrap remain blocked; public registration cannot assign Admin and is implemented through the registration identity.

Approved roles (`user`, `admin`) and the eleven predefined transaction categories are initialized by a separate idempotent provisioning CLI, `cd backend && .venv/bin/python -m app.db.seed`, run with the migration identity after the initial revision is applied. It uses stable identifiers, inserts only missing rows, never overwrites existing data, and is not exposed as an API. The first Admin remains a separate controlled bootstrap operation and never uses a default password or hardcoded credential.

PostgreSQL Row-Level Security is deferred. Requests share the `finpath_app` database identity, which does not identify an end user or Admin to PostgreSQL. Owner-scoped access and Admin authorization therefore remain mandatory service responsibilities. Do not implement RLS or add an actor-identity mechanism without later approval.

Retain `admin_audit_logs.target_user_id ON DELETE SET NULL`. No user hard-delete workflow is approved; closure is a terminal tombstone that retains financial history. Its potential to mutate an append-only audit row is a future review item only if a hard-delete or legal-erasure process is proposed.

## 6. Frontend contract mapping

| Existing frontend data | API representation and integration requirement |
|---|---|
| Transactions (`id`, description, type, category, `amountCents`, date) | Use UUID `id`, category ID/name, `amount_kyat`, and `transaction_date`. Add API service mapping and server validation. |
| Loans and nested repayments (`principalCents`, `amountCents`) | Use `principal_kyat`, `amount_kyat`, `repayment_date`; derive total repaid and outstanding principal server-side. Keep repayments separate from transactions. |
| Savings goals, entries, daily plan (`targetCents`, `amountCents`, day history) | Use `_kyat` amounts, append-only entries, server-derived actual balance, and the 365-day plan contract. Preserve day history and reconcile without duplicate contributions. |
| Trips, budgets, spending (`amountCents`) | Use `_kyat` fields and explicit trip status; keep spending trip-owned and distinct from transaction expense rows. Recognized travel affects Dashboard expenses and balance once. Preserve spending when a single budget is deleted or the trip is cancelled. |
| Dashboard (`accountBalanceCents: number \| null`, report rows) | Map to explicit `account_balance.status`, configured fields or `not_configured`, and integer Kyat report totals. Do not substitute zero for an unset starting balance. |
| CSV download | Call the authenticated report-export endpoint; preserve filename/download UX while server-side export applies owner scoping and formula-injection protection. |

The current frontend and backend both use whole MMK Kyat. Earlier prototype mocks used integer USD cents, but the live feature tabs now send and receive `_kyat` fields through the authenticated API; the legacy sample-data helpers in `src/data/dashboardReports.ts` are not used by the live Dashboard tab. Do not reinterpret historical USD-cent values as Kyat. No persisted USD-cent data migration is required because the prototype mocks were never persisted.

## 7. Decisions and implementation dependencies

### Approved defaults and remaining decisions

- **Password complexity — unresolved.** The 12-character minimum is approved; requirements beyond that are not. Do not impose mandatory uppercase, number, or symbol rules.
- **Personal-data retention and erasure — unresolved.** Retention duration awaits legal/product review. Financial and audit history is retained on account closure; email reuse is allowed only after identity is safely released through an approved anonymization process.
- **PostgreSQL development — verified.** PostgreSQL 17.11 is installed locally. The initial revision is applied and verified on both `finpath_test` and the development `finpath` database.
- **Idempotency cleanup — implemented.** The 24-hour retention/replay semantics, expired-record behavior, and exact deletion predicate are approved and implemented: a completed record is deleted only when `state = 'completed'` AND `completed_at <= now() - interval '24 hours'` AND `expires_at <= now()`. The cleanup schedule remains to be specified.
- **Shared rate-limiter operations — deployment detail.** Use Redis or another shared external limiter; hosting and connection configuration are not selected.
- **Account closure — implementation detail.** The lifecycle and terminal `closed` state are approved and the model/migration status constraints permit all three approved statuses. The closure route/actor and handling of active loans, savings goals, and trips remain to be specified during API/service implementation.
- **Database financial protections and runtime privileges — implemented and verified.** Tasks 3.8c/3.8d added and tested triggers, constraints, column grants, and restricted cleanup against isolated PostgreSQL 17 schemas; Task 3.8e applied and verified the revision on `finpath_test`, and Task 4.0 applied and verified it on the development `finpath` database. `user_roles` runtime writes remain deferred as described above. Task 4.2 implements the transaction service behavior; Task 4.3 implements the loan, repayment, and correction service behavior; other financial service behavior remains to be implemented.
- **Cleanup scheduling — unresolved implementation detail.** The cleanup function's allowed predicates are fixed in the migration; its invocation schedule remains unspecified and must not weaken token/session expiry, the 30-day reuse-detection window, or the 24-hour idempotency behavior.
- **Cookie/CORS deployment — unresolved operational details.** Confirm exact frontend origins and whether cross-site `SameSite=None; Secure` is necessary; retain Origin allowlisting and CSRF validation.

### Architecture alignment and implementation dependencies

- **Idempotency:** implement scoped fingerprints, atomic replay, 24-hour retention, expired-key behavior, and mandatory completed response headers (use `{}` when none apply).
- **Authentication/CSRF:** implement the documented CSRF-token binding, Origin allowlist, refresh/logout cookie attributes, CORS headers, and current account-status precedence. Preserve deterministic `403 account_suspended` and `403 account_closed` responses.
- **Rate limiting:** use a shared external limiter, enforce approved thresholds, and fail closed for sensitive authentication operations. Do not add a database table.
- **ETag implementation:** use persisted monotonic revisions, require preconditions on the documented endpoints, and increment affected parent revisions atomically with nested mutations.
- **Admin audit:** role/status changes and bootstrap write audit events in the same transaction. Keep correction reasons in their append-only financial correction records; do not log unnecessary financial details.
- **Account closure:** implement the approved terminal tombstone and session revocation. No closure endpoint or service implementation exists.
- **Frontend currency:** resolved. The live feature tabs use whole MMK Kyat `_kyat` fields and the authenticated API. Historical prototype mocks used USD cents but were never persisted, so no conversion rate or data migration is required.

## 8. Recommended implementation sequence

1. Tasks 3.0a architecture and 3.0b API Contracts are approved and complete. Design approval does not mark implementation or migration work complete.
2. **Task 3.8b.1:** reconcile the approved protection matrix and implementation status across project documentation.
3. **Task 3.8c — complete:** add and test approved PostgreSQL financial protection triggers and required constraints in the initial revision.
4. **Task 3.8d — complete except deferred `user_roles` writes:** per-table/column grants and the restricted cleanup function are in the initial revision; approved `user_roles` INSERT/DELETE remain deferred pending a separately approved privileged path.
5. **Task 3.8e — complete:** full runtime-role bypass resistance, approved exceptions, cleanup boundaries, privileges, reference-data seeding, and migration-parity verification using real PostgreSQL. The revision was applied and verified on `finpath_test` and, in Task 4.0, on `finpath`.
6. **Task 4.0 — complete:** development initialization applied revision `7270e464aca9` to `finpath` with `finpath_migrator`, seeded 2 roles and 11 categories idempotently, and verified schema, protections, privileges, and that `finpath_test` remained unchanged.
7. **Task 4.1 — complete:** self-registration through the dedicated `finpath_registration` identity, atomic User-role/settings creation, email verification, login, signed access tokens, refresh rotation/reuse detection, logout, password reset, change-password, and suspended/closed-account enforcement. Email-change endpoints, Admin bootstrap/role management, and shared rate limiting remain future work. Admin role management depends on the separately approved privileged `user_roles` write path.
8. Implement financial services and APIs, reports, CSV privacy, and frontend API integration; preserve all approved ledger separation, immutability, and ownership rules.

The privileged `user_roles` write path remains future work for Admin management; registration is implemented through the separate least-privilege identity, and the authentication revisions are verified on `finpath_test` and applied on `finpath`.

No backend implementation, dependency, database, or frontend change is part of this document.
