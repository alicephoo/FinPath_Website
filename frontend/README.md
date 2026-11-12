# FinPath Frontend

React + TypeScript + Vite frontend for FinPath.

## Development

```sh
npm install
npm run dev
```

The Vite development server normally runs at `http://localhost:5173`.

Available scripts:

| Script | Purpose |
|---|---|
| `npm run dev` | Start the Vite development server |
| `npm run build` | Run the TypeScript project check and production build |
| `npm run preview` | Preview the production build |
| `npm run test` | Run Vitest in watch mode |
| `npm run test:run` | Run all frontend tests once |

## Money and currency

FinPath uses **Myanmar Kyat (MMK) only**. Financial values are whole-Kyat
integers; there are no decimal subunits, cents, or exchange-rate conversions.
Do not reinterpret older USD-cent sample values as Kyat.

- Input parsing accepts only whole integers. Decimals, currency symbols, and
  thousands separators are rejected rather than rounded.
- Displayed amounts are grouped and labelled, for example `1,234 MMK` or
  `-25,000 MMK`. Editable form fields and CSV amount cells use raw integers
  such as `1234`.
- A single financial entry is limited to **1,000,000,000 MMK**. This applies to
  transaction income and expenses, loan principal and repayments, savings
  contributions and withdrawals, and trip spending.
- Settings and planned amounts are not individual entries and may exceed that
  limit up to JavaScript's safe-integer bound. This includes the starting
  balance, savings targets, daily-plan targets, and trip category budgets.
  Starting balances may be zero or negative.
- Money totals, divisions, and allocations use exact integer arithmetic.
  Calculations that would exceed the safe-integer range throw instead of
  returning an unsafe value.
- Daily savings plans with a blank daily amount allocate the target exactly
  across the schedule, so rounding never loses or invents Kyat. An explicit
  daily amount creates a fixed whole-Kyat amount per day.
- The dashboard CSV export is disabled until the deferred reports/export API
  exists. The sample report helper still formats whole-Kyat integers if it is
  reused later.

The Dashboard balance, transaction totals, recent transactions, loan totals, and
savings progress are loaded from the backend. The Transactions, Loans,
Savings Goals, and Travel Budget tabs are live and read and write records
through the backend.

## API configuration

The API base URL is configured with one Vite environment variable:

```sh
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

If it is not set, the frontend uses `http://localhost:8000/api/v1`. Copy
`.env.example` to `.env.local` to override it locally:

```sh
cp .env.example .env.local
```

The base URL includes the backend version prefix `/api/v1`. API client paths
are relative to it, for example `/transactions` or
`/dashboard/overview`.

Only `VITE_`-prefixed values are exposed to browser code. Never put passwords,
tokens, database credentials, or other secrets in a frontend environment
variable.

### Container API proxy

The production Nginx image renders `nginx.conf` from environment values:

- `PORT` — Nginx listen port, default `80`.
- `BACKEND_UPSTREAM` — API upstream, default `http://backend:8000`.
- `VITE_API_BASE_URL` — build-time browser API base, default `/api/v1`.

`/api/` is proxied to `BACKEND_UPSTREAM` with the original `/api/v1` path,
query string, method, body, cookies, `Set-Cookie`, `Authorization`,
`Idempotency-Key`, `If-Match`, and `X-CSRF-Token` preserved. `/health` is also
proxied, and other routes use the SPA fallback.

For Docker Compose, the Dockerfile defaults keep `PORT=80` and
`BACKEND_UPSTREAM=http://backend:8000`. Compose continues to build the browser
with the direct `http://localhost:8000/api/v1` API base, so existing local
behavior is unchanged. For Railway, use the relative `/api/v1` build base and
set, for example:

```text
BACKEND_UPSTREAM=http://finpath-backend.railway.internal:8000
```

Do not add a trailing slash to `BACKEND_UPSTREAM`.

`localhost` is the default API host so the local frontend
(`localhost:5173`) and API (`localhost:8000`) share a site for the HttpOnly
refresh cookie used by the authentication endpoints.

## Centralized API client

The client lives in `src/api`:

- `config.ts` — base URL configuration and URL construction.
- `client.ts` — `apiRequest` and the `apiClient` convenience methods.
- `errors.ts` — `ApiError` and field-error types.

```ts
import { apiClient } from './api/client'

type DashboardResponse = {
  data: {
    all_time_income_kyat: number
  }
}

const response = await apiClient.get<DashboardResponse>('/dashboard/overview')
console.log(response.data)
console.log(response.status)
console.log(response.headers.get('ETag'))
```

Supported methods are `get`, `post`, `put`, `patch`, and `delete`. Request
bodies are serialized as JSON. Successful JSON responses are returned in
`data`; empty responses return `data: undefined`; successful non-JSON
responses such as CSV are returned as text.

## Dashboard and starting balance

The Dashboard tab is connected to the backend through two services in
`src/api`:

- `financialSettings.ts` — `GET` and `PUT /users/me/financial-settings`.
- `dashboard.ts` — `GET /dashboard/overview?recent_limit=5`.

`DashboardReports.tsx` loads both endpoints in parallel through the
authentication context's `authorizedRequest`. The response shapes match the
backend account-balance, dashboard-overview, transaction, and savings-goal
resources; no dashboard figures are recalculated from mock data.

### Starting balance

- The form loads the authenticated caller's saved starting balance and effective
  date. The endpoint is `/users/me/financial-settings`, so the frontend cannot
  address another user's settings.
- Whole-Kyat MMK values only. Zero and negative starting balances are valid, and
  decimals are rejected before any request is sent.
- Saving sends **both** `starting_balance_kyat` and `effective_date` together.
  Clearing sends both as `null`. The starting balance is treated as the opening
  balance at the beginning of `effective_date`, so same-day transactions and
  loan cash movements affect the calculated balance.
- Every save sends the latest settings `ETag` in `If-Match` and stores the
  returned `ETag` for the next update.
- If the initial settings response has no `ETag`, the dashboard reloads the
  settings once before saving. If the header is still missing, the form shows an
  error instead of saving without a precondition.
- A stale revision (`412`) or missing precondition (`428`) reloads the latest
  settings and keeps the form open with a notice so the user can review and try
  again. Backend validation messages are shown for other save failures.
- A successful save refreshes the dashboard overview so the calculated balance
  reflects the new setting.

### Dashboard fields

The overview endpoint supplies the account balance and its configuration status,
all-time and current-month transaction income and expenses, recent transactions,
outstanding loan principal, active loan count, recorded loan repayments, actual
savings balance, combined savings targets, active savings-goal count, and each
savings goal's progress. Loan repayments and savings entries are separate from
transaction expenses. The backend-calculated balance includes borrowed loan
principal and unreversed loan repayments as cash movements, and subtracts the
recognized savings allocation. An active savings target reserves
`max(target, contributions) - withdrawals`; contributions inside the target do
not deduct twice, and archived goals reserve only actual savings. The allocation
is not an expense and is not added to Dashboard expense totals.

A `not_configured` balance is displayed as **Not configured**, never as
`0 MMK`. The dashboard shows the backend's calculated balance and does not
subtract transactions itself.

### Unsupported report panels

The overview endpoint does not return six-month cash flow, spending by category,
per-trip travel rows, per-loan details, or CSV export data. Those panels are
disabled with a clear explanation instead of showing sample figures. The
overview does include aggregate recognized travel expense in Total expenses.
The sample-data dashboard report builder in `src/data/dashboardReports.ts`
remains unused by the live Dashboard tab.

Loading, refresh, empty recent transactions, API/network error, authentication
failure, failed save, and stale-revision states are handled in the component.
The dashboard keeps tokens and financial data in React memory only; none of it
is written to `localStorage` or `sessionStorage`.

## Transactions

The Transactions tab is connected to the backend through `src/api/transactions.ts`:

- `GET /transaction-categories` — active predefined categories, optionally
  filtered by `type`.
- `GET /transactions` — owner-scoped list with `type`, `category_id`, inclusive
  `from`/`to`, `q`, `limit`, and opaque `cursor` parameters.
- `POST /transactions` — create a transaction.
- `GET /transactions/{id}` — read one transaction and its current `ETag`.
- `PATCH /transactions/{id}` — partially update a transaction with `If-Match`.
- `DELETE /transactions/{id}` — delete a transaction with `If-Match` and an
  `Idempotency-Key`.

### Categories and list

- The create and edit forms use only categories returned by the backend. There
  are no hardcoded category lists for live transactions. A category can only be
  selected when its `income`/`expense` type matches the transaction type.
- Loading categories, category request failures with a retry action, and an empty
  active-category list are shown in the page. Creating a transaction is disabled
  until at least one active category is available.
- List filters are sent to the backend rather than applied to a loaded page:
  transaction type, category, inclusive start/end dates, and a case-insensitive
  description search. Search is matched against descriptions only.
- The list loads 50 records at a time. **Load more transactions** follows the
  opaque `next_cursor` and keeps the current filters. The page reports how many
  records are loaded rather than claiming a total count.
- Transaction date, type, category, description, and whole-Kyat MMK amount are
  displayed from the API response. The list does not calculate dashboard totals;
  the Dashboard continues to use its own overview endpoint.

### Create, edit, and delete

- Create sends `description`, `type`, `category_id`, a whole-Kyat integer
  `amount_kyat`, and `transaction_date`. Zero, decimal, negative, and
  over-limit amounts are rejected before a request is sent; amounts are never
  rounded or silently converted.
- Each new create operation gets a fresh `Idempotency-Key`. If the request fails
  with an uncertain network error and the same unchanged form is submitted
  again, the same key is reused. Changing the payload creates a new key. The
  submit control is disabled and guarded against duplicate requests while a save
  is in progress.
- Editing first calls `GET /transactions/{id}` because collection responses do
  not include per-item ETags. The returned quoted strong `ETag` is sent as
  `If-Match` on `PATCH`.
- A `412 stale_revision` (or `428 precondition_required`) during editing keeps
  the form open, shows a notice, reloads the latest record and ETag, and updates
  the form for review instead of overwriting newer data.
- Deleting keeps the existing confirmation step, then fetches the current
  transaction to obtain its ETag. `DELETE` sends both `If-Match` and a generated
  `Idempotency-Key`. The row is removed only after a successful response. A
  retry of the same delete operation reuses the key; if the transaction was
  already deleted, the list is refreshed without repeating the write.
- Authentication failures, network failures, backend validation messages,
  loading, empty lists, and retry states are shown in the page. Tokens and
  transaction data remain in React memory only.

## Loans

The Loans tab is connected to the backend through `src/api/loans.ts`:

- `GET /loans` — owner-scoped loan list with backend-derived totals.
- `POST /loans` — create a loan.
- `GET /loans/{loan_id}` — read one loan and its current `ETag`.
- `PATCH /loans/{loan_id}` — partially update a loan with `If-Match`.
- `GET /loans/{loan_id}/repayments` — list a loan's repayment history.
- `POST /loans/{loan_id}/repayments` — record a principal-only repayment.
- `GET /loans/{loan_id}/repayments/{repayment_id}` — read one repayment.
- `POST /loans/{loan_id}/repayments/{repayment_id}/corrections` — append a
  full-reversal correction.

### Loan list and repayment history

- The list uses only backend loan records. **Original principal**, **Repaid**,
  and **Outstanding principal** are displayed from `principal_kyat`,
  `total_repaid_kyat`, and `outstanding_principal_kyat`. The page does not
  recompute an authoritative outstanding balance from partial repayment data.
- Repayment history is loaded on demand when a loan's history section is opened,
  because the loan list does not include repayment rows. Loading, empty, error,
  and retry states are shown inside that section.
- Corrected repayments remain visible in history and are labelled as reversed by
  a correction. Their amounts are already excluded from the backend's repaid and
  outstanding totals.
- A paid-off loan displays `0 MMK` outstanding and hides the repayment action.

### Create and edit loans

- Create sends `name`, a whole-Kyat integer `principal_kyat`, and `start_date`.
  Zero, decimal, negative, and over-limit principals are rejected before a
  request is sent; amounts are never rounded or converted.
- Each new create operation gets a fresh `Idempotency-Key`. An unchanged retry
  after an uncertain network failure reuses the key, while changing the payload
  creates a new key. Duplicate submissions are guarded while a save is running.
- Editing first calls `GET /loans/{loan_id}` because collection responses do not
  include per-item ETags. The returned quoted strong `ETag` is sent as
  `If-Match` on `PATCH`.
- A `412 stale_revision` or `428 precondition_required` keeps the form open,
  shows a notice, reloads the latest loan and ETag, and updates the form for
  review instead of overwriting newer data.
- The backend rule that principal cannot be reduced below the total already
  repaid is preserved. A local check gives an immediate message using the
  backend-calculated `total_repaid_kyat`, and backend validation errors are also
  displayed.
- Successful creation or editing refreshes the loan list and any loaded
  repayment history.

### Repayments

- A repayment sends a positive whole-Kyat integer `amount_kyat`, a
  `repayment_date`, and an optional `note`. Invalid amounts are rejected before a
  request is sent, and amounts above the backend-calculated outstanding
  principal are rejected locally with that value.
- The backend remains authoritative for overpayment and concurrency checks. A
  `409 repayment_exceeds_principal` or other validation error is shown in the
  open repayment form, and no partial repayment is assumed.
- Each new repayment gets a fresh `Idempotency-Key`. The same key is reused only
  when retrying the same unchanged operation; another payload gets a new key.
  Duplicate submissions are guarded while the request is in flight.
- A successful repayment refreshes the loan's backend-calculated outstanding
  principal and reloads its repayment history.
- The existing Loans page has no repayment-correction action, so no correction
  workflow was added. The API service includes the correction endpoint for a
  future UI, but append-only correction UI behavior is deferred.
- The backend has no loan deletion endpoint, so the page has no delete action and
  does not simulate server deletion in frontend state.
- Authentication failures, network failures, backend validation messages,
  loading, empty lists, and retry states are shown in the page. Tokens and loan
  data remain in React memory only.

## Savings Goals

The Savings Goals tab is connected to the backend through `src/api/savings.ts`:

- `GET /savings/goals` — owner-scoped active-goal list with backend-derived
  actual and remaining balances.
- `POST /savings/goals` — create a savings goal.
- `GET /savings/goals/{goal_id}` — read one goal and its current `ETag`.
- `PATCH /savings/goals/{goal_id}` — partially update a goal with `If-Match`.
- `GET /savings/goals/{goal_id}/entries` — list contribution history.
- `POST /savings/goals/{goal_id}/entries/contributions` — record a contribution.

The list requests `status=active`; archived goals are not shown in this Phase 1
interface. Actual balance, target, remaining amount, and per-goal progress come
from backend fields and are never recalculated from the old sample data.

### Goal list and details

- **Actually saved**, **Target**, and **Target remaining** are displayed from
  `actual_balance_kyat`, `target_kyat`, and `remaining_kyat`. Progress is derived
  from the backend actual/target pair only for presentation.
- Loading, empty, API/network error, and retry states are shown in the page.
- Contribution history is loaded on demand when a goal's history section is
  opened, because the list response does not include entries. Loading, empty,
  error, and retry states are shown inside that section.
- A successful goal change or contribution refreshes the goal list, and a
  contribution also refreshes the open history so the displayed balance and
  entries reflect the backend.

### Create and edit goals

- Create sends `name`, a positive whole-Kyat integer `target_kyat`, and an
  optional `tone` (`mint`, `gold`, or `blue`). Targets are settings, not
  individual entries, so they use the safe-integer bound rather than the
  per-entry limit. Decimals, zero, negative, and unsafe values are rejected
  before a request is sent.
- Editing first calls `GET /savings/goals/{goal_id}` because list responses do
  not include per-item ETags. The quoted strong `ETag` is sent as `If-Match` on
  `PATCH`.
- A `412 stale_revision` or `428 precondition_required` keeps the form open,
  shows a notice, reloads the latest goal and ETag, and updates the form for
  review instead of overwriting newer data.
- Each new goal creation gets a fresh `Idempotency-Key`. An unchanged retry
  after an uncertain network failure reuses the key; changing the payload
  creates a new key. Duplicate submissions are guarded while a save is running.

### Contributions

- A contribution sends a positive whole-Kyat `amount_kyat`, an `entry_date`,
  and an optional `note`. Amounts above **1,000,000,000 MMK** are rejected
  before a request is sent; the backend remains authoritative for the configured
  entry limit.
- Each new contribution gets a fresh `Idempotency-Key`. The same key is reused
  only when retrying the same unchanged operation; another payload gets a new
  key. Duplicate submissions are guarded while the request is in flight.
- Contributions are available only for active goals. Backend errors such as
  `409 goal_archived` and validation messages are displayed in the open form.
- The contribution endpoint does not return an ETag, so the page re-reads the
  goal collection after success rather than assuming a new revision.

### Deferred savings operations

The backend has no savings-withdrawal, correction, archive/restore, or daily-plan
endpoints. Those controls were removed from the active workflow, and the page
states that they are unavailable in this Phase 1 integration. The page does not
simulate any of them in frontend state. `src/data/savingsGoalsMockData.ts`
remains only for legacy sample dashboard helpers and their tests; the live
Savings Goals tab does not use it.

The backend has no savings-goal deletion endpoint, so the page has no delete
action.

## Travel Budget

The Travel Budget tab is connected to the backend through `src/api/travel.ts`:

- `GET /trips` — owner-scoped trip list with derived `budget_kyat` and
  `spending_kyat` totals.
- `POST /trips` — create a trip.
- `GET /trips/{trip_id}` — read one trip and its current `ETag`.
- `PATCH /trips/{trip_id}` — update trip details with `If-Match`.
- `POST /trips/{trip_id}/complete` — mark an active trip completed with
  `If-Match` and `Idempotency-Key`; unused allocation is released and actual
  spending remains recognized.
- `DELETE /trips/{trip_id}` — cancel the trip after explicit confirmation. The
  trip is hidden from the active list, unused allocation is released, and its
  actual spending history is preserved for financial totals.
- `GET|POST /trips/{trip_id}/budgets` — list or create planned expenses.
- `GET|PATCH|DELETE /trips/{trip_id}/budgets/{budget_id}` — read (with ETag),
  update, or remove a planned expense. Removing a budget keeps its spending as
  unbudgeted history.
- `GET|POST /trips/{trip_id}/spending` — list or create actual spending.
- `GET|PATCH|DELETE /trips/{trip_id}/spending/{spending_id}` — read (with ETag),
  update, or delete a spending entry.
- `POST /trips/import` — validate and import a JSON document atomically.

### Planned expenses and totals

- A planned expense has a category/name and a whole-Kyat `budget_kyat`. It is a
  plan, not an individual entry, so it may exceed the **1,000,000,000 MMK**
  per-entry limit up to the safe-integer bound. Zero is allowed.
- Estimated total is the sum of the trip's planned expenses, actual spending is
  the sum of its spending entries, and remaining is
  `estimated − actual`. Progress is `actual / estimated`.
- Actual spending may exceed a category or overall budget. The UI shows an
  over-budget warning and keeps the action available instead of blocking it.
- **Actual spending** entries require a description, a positive whole-Kyat
  amount within the per-entry limit, a date, and a category. Multiple entries
  per category are supported, and the category's spent/remaining/progress values
  are recalculated from the returned records.
- Trip spending is never written as a transaction. However, it is not ignored
  by the Dashboard: recognized travel expense is deducted from the available
  account balance and included in Dashboard Total expenses.
- For an **active** trip, recognized travel expense is
  `max(planned budget, actual spending)`. The allocation is deducted once, and
  actual spending above the plan adds only the excess.
- For a **completed** or **cancelled** trip, recognized travel expense is the
  total actual spending only. Completing or cancelling therefore returns any
  unused allocation while preserving genuine expenses.
- Trip spending is never counted twice: moving from active to completed changes
  recognized expense from the greater amount to actual spending only.

### Completing and cancelling

- **Complete trip** asks for confirmation and shows the unused amount that will
  be returned. Completing is terminal for the allocation but preserves the trip,
  planned expenses, and spending history.
- A completed trip shows a **Completed** badge and a notice stating how much
  unused budget was returned. Repeated completion requests do not change
  accounting again.
- **Delete trip** confirms that unused allocation will be released and recorded
  spending will remain in financial totals. Deletion is implemented as
  cancellation, so spending rows are not erased.
- The Dashboard refreshes authoritative figures when its tab becomes active
  again, so travel changes made in the Travel Budget tab appear without a full
  page reload.

### Create, edit, and delete

- Editing a trip, planned expense, or spending entry first reads the individual
  record because list responses do not include per-item ETags, then sends the
  quoted strong `ETag` as `If-Match`.
- A `412 stale_revision` or `428 precondition_required` keeps the form open and
  asks the user to reload instead of overwriting newer data.
- New records use a fresh `Idempotency-Key`; duplicate submissions are blocked
  while a request is in flight.
- Loading, empty, success, and error states are shown in the page. An empty list
  displays “No trips planned yet” rather than sample data.

### JSON import

- The selected file is parsed and validated in the browser with
  `parseTripImportDocument`, then sent to `POST /trips/import` with an
  `Idempotency-Key`.
- Format: `{"trips":[{name, destination, start_date, end_date,
  budgets:[{category, budget_kyat}], spending:[{description, category,
  amount_kyat, spending_date, budget_category?}]}]}`.
- Invalid JSON, missing fields, invalid dates, negative/zero spending, unsafe
  integers, and spending above **1,000,000,000 MMK** are rejected before the
  request. The backend revalidates the whole document and saves nothing if any
  row is invalid.
- Exact duplicates are skipped and reported: trips match on owner, case-insensitive
  name/destination, and dates; budgets match on trip and case-insensitive
  category; spending matches on trip, case-insensitive
  description/category, amount, and date. The success message reports created
  and skipped counts so valid rows are never silently discarded.

## Authentication

The authentication screens and session state live in `src/auth`:

- `api.ts` — registration, verification, login, CSRF, refresh, current-user,
  and logout requests.
- `AuthContext.tsx` — in-memory session state, startup restore, single-flight
  refresh, authenticated-request helper, login, and logout.
- `AuthScreens.tsx` — login, registration, email verification, and session
  restoration screens.

Registration matches the backend schema and accepts only **email and
password**. The backend does not currently store a display name. Passwords must
be at least 12 characters.

The existing dashboard shell is protected: Dashboard, Transactions, Loans,
Savings Goals, and Travel Budget are only mounted after a successful login or
session restore. Registration does not sign the user in; the account must be
verified and then signed in normally.

### Token and cookie handling

- Access and CSRF tokens are kept in React memory only.
- Tokens are never written to `localStorage`, `sessionStorage`, or another
  browser-persistent store.
- Login, refresh, logout, and CSRF-token retrieval send
  `credentials: "include"` so the backend can use its HttpOnly `finpath_refresh`
  cookie.
- On startup the frontend calls `GET /auth/csrf`, then
  `POST /auth/refresh` with `X-CSRF-Token`, then `GET /auth/me`.
- Protected requests can use `authorizedRequest`; a `401` triggers at most one
  single-flight refresh and one retry. There is no background refresh timer and
  no retry loop.
- Logout sends the refresh cookie and CSRF token to `POST /auth/logout`, then
  clears local authentication state even if the request fails.

The API client options remain available for feature services:

- `accessToken` adds `Authorization: Bearer <token>`.
- `credentials: 'include'` enables cookies for auth/CSRF requests.
- `csrfToken` adds `X-CSRF-Token`.
- `signal` passes an `AbortSignal` for cancellation.
- `headers` supplies any other required header.

### Local email verification

Email verification is enabled by default. For local development, set
`FINPATH_REQUIRE_EMAIL_VERIFICATION=false` in `backend/.env` and restart the
backend. Registration then creates an already-verified account, the UI returns
to the login screen with a success message, and the outbox steps below are not
needed. Set the value to `true` (or remove it) to exercise the verification
flow.

When verification is enabled, development email is written to the backend
outbox, not delivered. After registering (or requesting a new token), read the
newest JSON file and copy its `token` value into the verification screen:

```sh
ls -lt backend/var/email_outbox
jq -r '.token' backend/var/email_outbox/<file>.json
```

The outbox directory is owner-only and for local testing. Verification tokens
expire after 24 hours, and multiple resend files may exist, so use the newest
message. Do not expose these tokens through a public frontend route or commit
them.

### Local origin and cookie notes

The backend accepts the frontend origins `http://localhost:5173` and
`http://127.0.0.1:5173`. Run the frontend at one of those origins so the
backend's Origin check passes. Match the API host to the frontend host: if the
frontend uses `127.0.0.1`, set
`VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1` so the refresh cookie is sent
to the same host. The default base URL uses `localhost`.

The refresh cookie is `Secure` outside local development. Because a plain-HTTP
development server is not always treated as a secure context, the backend
defaults `FINPATH_REFRESH_COOKIE_SECURE` to `false` when
`FINPATH_ENVIRONMENT` is `development`, `local`, `test`, or `testing`, and to
`true` otherwise. This lets browsers that reject `Secure` cookies on
`localhost` persist the refresh session without local overrides. Production
rejects an explicit insecure setting at startup. See `backend/README.md` for
backend authentication configuration.

## Financial API headers

The shared client does not generate idempotency keys or manage revisions.
Feature services supply those values through headers:

```ts
await apiClient.post('/transactions', body, {
  accessToken,
  headers: {
    'Idempotency-Key': idempotencyKey,
  },
})

await apiClient.patch(`/transactions/${id}`, body, {
  accessToken,
  headers: {
    'If-Match': etag,
  },
})
```

Response headers remain available through `response.headers`, including
`ETag` returned by successful resource reads and writes.

## API errors

All HTTP failures are thrown as `ApiError`. Network failures are also
`ApiError`, distinguished by a `null` status and `isNetworkError: true`.
Request aborts are rethrown as the original `AbortError` and are not converted
into HTTP errors.

```ts
import { ApiError } from './api/errors'

try {
  await apiClient.get('/transactions')
} catch (error) {
  if (error instanceof ApiError) {
    console.log(error.status)       // 401, 403, 404, 409, 412, 422, 428, 503, ...
    console.log(error.code)         // backend error code
    console.log(error.message)      // safe human-readable message
    console.log(error.fieldErrors)  // validation details
    console.log(error.requestId)    // backend request identifier
    console.log(error.headers)      // response headers
    console.log(error.isNetworkError)
  }
}
```

The client does not display notifications or retry requests automatically.

## Tests

Run the focused transaction, loan, savings, API, authentication, and dashboard
component tests:

```sh
npm run test:run -- src/api src/auth src/components/TransactionsSection.test.tsx src/components/LoansSection.test.tsx src/components/SavingsGoalsSection.test.tsx src/components/TravelBudgetSection.test.tsx src/components/DashboardReports.test.tsx
```

Run the full frontend suite and production build:

```sh
npm run test:run
npm run build
```

The API client, transaction, loan, savings, financial-settings, and dashboard
API tests mock `fetch`. The transaction, loan, savings, travel, and dashboard
component tests mock the authenticated-request helper and API services, so none
of them requires a running backend.

Current baseline: 17 test files and 221 passing tests; the production
`tsc -b && vite build` succeeds. Task 5.7 also passed live checks against the
local FastAPI backend for goal creation/replay, list/detail, stale and missing
preconditions, valid updates, contribution creation/replay, history, and
over-limit validation. Task 5.8 travel tests cover persistence, totals,
over-budget support, entry limits, ownership isolation, and invalid imports.
Task 5.9 tests cover allocation-aware Dashboard totals, no double counting,
excess-only over-budget recognition, unused-budget return on completion,
idempotent completion, cancellation preserving spending, and import accounting.
