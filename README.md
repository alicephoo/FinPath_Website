# FinPath

FinPath is a personal finance and goal-planning application prototype for tracking income, expenses, loans, savings goals, and travel budgets. Administrative account management and audit logs remain planned and are not implemented.

## Project Status

The React + TypeScript frontend is under `frontend/`. It has separate Dashboard, Transactions, Loans, Savings Goals, and Travel Budget tabs. The Dashboard provides reports, CSV export, and setup/editing for the manual starting balance; it has no feature-record CRUD controls. Dashboard figures are loaded from the backend and include a calculated account balance based on a saved starting balance and effective date. Transactions and loan cash movements dated after that date affect the balance, and recognized travel expense is applied as an allocation layer. FinPath does not connect to banks. The Dashboard also includes transaction cash flow and category totals, loan principal and separate repayments, actual savings progress (not daily-plan amounts), and recognized travel separately from transaction expenses. All monetary amounts are whole Myanmar Kyat (MMK) integers and date-only financial records use local-calendar `YYYY-MM-DD` values. Transactions, loans, savings, and travel planned expenses and spending persist in PostgreSQL and reload after refresh, logout/login, and backend restart. Trip-owned spending is unlinked from Transactions; an active trip reserves the greater of its planned budget and actual spending, and a completed or cancelled trip recognizes actual spending only. Loan repayments are displayed separately rather than counted as transaction expenses. See [PROGRESS.md](PROGRESS.md) for verified status and the next task.

## Navigation and Feature Ownership

The top navigation has separate **Dashboard**, **Transactions**, **Loans**, **Savings Goals**, and **Travel Budget** tabs. Feature-record management is available in its respective tab. The Dashboard provides cross-feature summaries, reports, CSV export, and setup/editing for the manually entered starting balance; it does not duplicate feature CRUD forms.

Dashboard reports use the same backend data managed by feature tabs. The starting balance is user-entered and effective at the end of its selected date; the Dashboard applies only later transaction income, expenses, loan proceeds, and unreversed loan repayments, and distinguishes this calculated balance from net transaction flow. Recognized travel and the savings allocation are then deducted as allocation layers: active trips reserve the greater of planned budget and actual spending, while completed or cancelled trips recognize actual spending only; an active savings goal reserves `max(target, contributions) - withdrawals`, contributions within the target do not deduct twice, and archiving releases the unused remainder. Export is CSV only and is generated from current report state. CSV text fields beginning with spreadsheet formula characters are prefixed safely, including when whitespace or control characters come first; numeric financial cells remain numeric and the existing CSV escaping and UTF-8 BOM are preserved. Daily savings plans are limited to 365 days (one year) when created or edited. Loan principal repayments are shown separately and excluded from transaction expenses; trip-owned spending has no transaction link and is never written as a transaction.

## Planned Stack

- Frontend: React, TypeScript, Tailwind CSS 3, Vite
- Backend: Python, FastAPI
- Database: PostgreSQL with SQLAlchemy and Alembic
- Frontend tests: Vitest, React Testing Library, jest-dom, and jsdom
- Backend tests: Pytest; end-to-end tests: planned

The frontend is implemented through the backend-integrated Phase 1 feature set and has 270 passing automated unit and component tests across 19 files. The API client/service boundary, authenticated requests, loading states, empty states, and transport-error states are in place. The Task 3.1 FastAPI skeleton and Task 3.2 PostgreSQL/SQLAlchemy configuration remain the backend foundation. The backend defines all 20 SQLAlchemy tables. The initial revision `7270e464aca9` and the Task 4.1 authentication revisions `c7a4e2f19b30`, `e1b9c0d4a7f2`, and `f4a1c2d3e5b6`, the registration grant revision `b9e4d2a7c1f3`, and the Task 5.9 trip-status revision `c4f7a1b9e2d6` are applied and verified on the dedicated `finpath_test` database and on the development `finpath` database, which is seeded with 2 roles and 11 categories. Task 4.1 implements the backend authentication foundation: registration through the dedicated least-privilege `finpath_registration` identity, email verification (required by default, with an explicit no-email MVP opt-in), login, signed access tokens, rotating refresh tokens with reuse detection, logout/session revocation, account-status enforcement, change-password, and password reset. Task 4.2 implements the authenticated Transactions API under `/api/v1/transactions` and the read-only `/api/v1/transaction-categories` endpoint, with owner-scoped CRUD, filtering/pagination, ETags and `If-Match`, scoped idempotency, strict whole-Kyat validation, category/type checks, and transaction-derived account-balance calculations. Task 4.3 implements the authenticated Loans API under `/api/v1/loans`: owner-scoped loan creation, listing, retrieval, and updates with derived total-repaid and outstanding-principal values, plus idempotent repayment recording and append-only full-reversal corrections. Task 4.4 implements the Savings goals and contributions API under `/api/v1/savings/goals`: owner-scoped goal creation, listing, retrieval, and updates, plus idempotent contribution recording and a derived actual/remaining balance from append-only entries. Task 4.5 implements the authenticated Phase 1 Dashboard API at `/api/v1/dashboard/overview`: it returns the account-balance state, all-time and current-month transaction totals, recent transactions, loan outstanding/active counts, and actual savings progress by reusing the existing transaction, loan, and savings calculations in one repeatable-read snapshot. Task 4.6 implements the authenticated starting-balance financial-settings API at `/api/v1/users/me/financial-settings`: it retrieves the caller's saved starting balance/effective date and derived balance status, and replaces or clears both fields together with `If-Match` protection. Task 4.7 implements the authenticated Travel Budget API under `/api/v1/trips`: owner-scoped trip, planned-expense (category budget), and actual-spending CRUD with ETags/`If-Match`, scoped idempotency, trip-row locking, over-budget support, and atomic JSON import with duplicate skipping. Task 5.9 adds explicit trip status (`active`, `completed`, `cancelled`) and travel accounting: active trips recognize the greater of planned budget and actual spending, completed or cancelled trips recognize actual spending only, and the shared recognized-travel calculation feeds both Dashboard total expenses and the available account balance exactly once. Savings withdrawals/corrections/daily plans, the remaining dashboard reports/CSV export, Admin management, email-change endpoints, rate limiting, and Playwright end-to-end testing are not implemented. Production email delivery uses the Resend HTTPS API when configured. Runtime `user_roles` writes remain deliberately withheld from `finpath_app`; registration writes use the separate registration identity, which can only assign role ID `1` (`user`). The frontend and backend both use whole MMK Kyat; the earlier USD-cent prototype values have been replaced. Tailwind CSS 3 is used to remain compatible with the available Node.js 18 runtime. Development remains local-first; deployment configuration is prepared but not executed.

## Local Development

Prerequisites: Node.js 18 or newer and npm. From the project root, install the frontend dependencies and start the Vite development server:

```sh
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite (normally `http://localhost:5173/`). To run the TypeScript check and production build:

```sh
npm run build
```

Run the frontend test suite once with:

```sh
npm run test:run
```

`npm run test` starts Vitest in watch mode.

The Python/FastAPI backend setup instructions are in [backend/README.md](backend/README.md). It provides environment configuration, PostgreSQL/SQLAlchemy session infrastructure, the 20-table schema and Alembic migrations, reference-data seeding, a database-independent health endpoint, the Task 4.1 authentication foundation, and the authenticated Transactions, Loans, Savings goals/contributions, Dashboard overview, Starting Balance Settings, and Travel Budget APIs. Authentication endpoints are under `/api/v1/auth`; transaction and category endpoints are under `/api/v1/transactions` and `/api/v1/transaction-categories`; loan, repayment, and correction endpoints are under `/api/v1/loans`; savings-goal and contribution endpoints are under `/api/v1/savings/goals`; the dashboard overview is under `/api/v1/dashboard/overview`; the caller's starting-balance setting is under `/api/v1/users/me/financial-settings`; and trip, planned-expense, spending, and import endpoints are under `/api/v1/trips`. Registration uses the dedicated `finpath_registration` database identity, while login, verification, session, transaction, loan, savings, dashboard, financial-settings, travel, and password operations use `finpath_app`. Development email is written to an owner-only local outbox rather than delivered; production email delivery uses Resend when configured. Savings withdrawals/corrections/daily plans and the remaining dashboard reports/CSV export remain unimplemented, and runtime `user_roles` writes are withheld from `finpath_app` pending separately approved Admin role management. The frontend and backend both use whole MMK Kyat and are integrated for all five feature tabs.

Authentication configuration requires `FINPATH_REGISTRATION_DATABASE_URL` and a long random `FINPATH_ACCESS_TOKEN_SECRET` in addition to the runtime and migration URLs. `FINPATH_REQUIRE_EMAIL_VERIFICATION` defaults to `true`; local development may set it to `false` for immediate verified registration/login. Production remains fail-closed when verification is disabled unless `FINPATH_ALLOW_UNVERIFIED_REGISTRATION=true` is explicitly set for a no-email MVP. The development outbox is not a production email provider; a production deployment that requires email verification uses Resend with `FINPATH_EMAIL_BACKEND=resend`, `FINPATH_RESEND_API_KEY`, and `FINPATH_EMAIL_FROM` (a verified sender domain/address). Never commit `.env` or any real credentials, tokens, or passwords.

Keep local credentials and other secrets out of source control. Never store plain-text passwords. Backend endpoints must authenticate requests, enforce ownership and role permissions, block suspended users, and independently validate financial amounts, dates, and relationships. Invalid loan repayment totals must be rejected server-side; admins must not automatically gain access to private financial records. These controls must not rely on frontend validation or UI visibility.

## Docker Compose (local full-stack)

The Docker Compose stack runs PostgreSQL, the FastAPI backend, and the production
React build together. It is separate from the Homebrew/local development setup:
it uses its own PostgreSQL container and named volume, and it does not read or
modify the local PostgreSQL databases or `backend/.env`.

Prerequisites:

- Docker Desktop (or Docker Engine with Compose v2).
- The Docker daemon running.
- A project-root `.env` file for Compose.

Create the Docker environment file from the documented example and replace every
placeholder with unique local values:

```sh
cp .env.docker.example .env
```

The root `.env` is used only by Docker Compose. It contains the PostgreSQL
superuser password, the runtime/migration/registration role passwords, the
application database URLs (using the Compose service name `db`), and the access
token secret. Do not reuse the local Homebrew credentials, and do not commit
`.env`. The backend's local development `.env` remains in `backend/`.

Start the complete application with one command:

```sh
docker compose up --build
```

The backend container runs `alembic upgrade head` and the idempotent reference
data seed before starting Uvicorn. This is controlled by
`FINPATH_RUN_STARTUP_MIGRATIONS`, which defaults to `true` for local Compose.
PostgreSQL is health-checked before the backend starts, and the frontend waits
for the backend health endpoint.

Local URLs:

- Frontend: `http://localhost:8080`
- Browser API base: `http://localhost:8000/api/v1`
- Backend API documentation: `http://localhost:8000/docs`
- Backend health: `http://localhost:8000/health`
- Frontend health proxy: `http://localhost:8080/health`

The browser calls the published backend host directly at
`http://localhost:8000/api/v1`; it never uses the Docker service name `backend`.
This keeps the existing cross-origin API client, Origin-header CSRF checks,
cookies, and `/api/v1/auth` refresh-cookie path unchanged. The backend allows
the frontend origin `http://localhost:8080`. Nginx serves the production build
and proxies only the frontend `/health` path to the backend.

Stop the stack without deleting data:

```sh
docker compose down
```

PostgreSQL data is stored in the named volume `finpath_postgres_data`. It
survives `docker compose down` and container restarts. To start again:

```sh
docker compose up -d
```

View logs and restart/rebuild services:

```sh
docker compose logs -f
docker compose restart backend
docker compose up --build
```

`docker compose up --build` is the normal rebuild command after changing
application code. To recreate the Docker database from scratch, you would need
`docker compose down -v`, which **permanently deletes the Docker PostgreSQL
volume**. Do not use it unless you intentionally want to erase all Docker
database data; it does not affect the Homebrew/local databases.

The PostgreSQL role passwords are created only when the named volume is first
initialized. If you change them in `.env` later, update the roles in the running
database or intentionally recreate the volume; the init script does not rerun on
an existing volume.

Host ports can be changed with `FINPATH_FRONTEND_PORT` and
`FINPATH_BACKEND_PORT` in the root `.env`. The Docker environment example uses
`FINPATH_ENVIRONMENT=development` and disables email verification for local
sign-up. That is a local-only convenience; production must keep verification
enabled and use a real email provider.

## Cloudflare Pages + Render Free + Neon deployment readiness (not deployed)

FinPath is prepared for a strict-$0 deployment, but **it is not deployed**. No
Cloudflare Pages project, Render service, Neon project, custom domain, or
production credential has been created. Railway is no longer the target; the
Nginx image remains only for local Docker Compose.

```text
Browser
  → Cloudflare Pages (React/Vite static build, HTTPS pages.dev domain)
  → Pages Function /api/* (same-origin proxy)
  → Render Free web service (existing FastAPI Docker image)
  → Neon Free PostgreSQL 17
```

Resend Free delivers verification and password-reset email when an
already-owned verified sender domain is available.

### Cloudflare Pages build settings

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Build command | `npm run build` |
| Output directory | `dist` |
| Build variable | `VITE_API_BASE_URL=/api/v1` |
| Node version | `22` (required by Wrangler 4) |

Local Cloudflare deployment tooling uses the pinned `wrangler@4.158.0` frontend
development dependency and requires Node.js 22 or newer. Node.js 18 remains the
system default used by the existing Vite/test workflow; use an isolated Node 22
binary or version manager when running Wrangler. Verify without authenticating:

```sh
node --version          # v22.x
npx wrangler --version  # 4.158.0
```

Do not run `wrangler login`, create a Pages project, or deploy until the
deployment task is explicitly approved.

The production frontend defaults to the same-origin `/api/v1` base even when the
build variable is absent, so a production bundle does not fall back to
`localhost`. Local development still defaults to
`http://localhost:8000/api/v1`.

`frontend/public/_routes.json` restricts Pages Functions to `/api/*`; static
assets remain outside the Function request quota. `frontend/functions/api/[[path]].ts`
is the entry point, and `frontend/functions/api/proxy.ts` contains the proxy.

The Nginx configuration in `frontend/nginx.conf` is used only by Docker Compose.
Cloudflare Pages does not run Nginx; do not configure an Nginx proxy there. If
client-side routes are added later, add a Pages SPA fallback such as
`/* /index.html 200`; the current tab-based UI does not need one.

### Pages Function proxy configuration

Set one non-secret Pages environment variable:

| Variable | Purpose |
|---|---|
| `RENDER_BACKEND_URL` | Fixed backend origin, for example `https://finpath-backend.onrender.com` |

The proxy never accepts an upstream from the request. It forwards method, path,
query string, body, `Content-Type`, `Authorization`, `Cookie`, `X-CSRF-Token`,
`Idempotency-Key`, `If-Match`, `If-None-Match`, `Origin`, and `Sec-Fetch-Site`.
It drops client-supplied `Host` and `X-Forwarded-*` headers, returns a controlled
JSON error when the backend URL is missing or the upstream is unreachable, and
does not log bodies, cookies, tokens, or secrets. Upstream status, body,
`Content-Type`, `Location`, and multiple `Set-Cookie` headers are preserved.
API responses are always `Cache-Control: no-store`; an upstream cacheable value
is overridden.

Because the browser stays on the Pages origin, the refresh cookie keeps
`Path=/api/v1/auth`, `HttpOnly`, `Secure`, and `SameSite=Lax` behavior. Set the
production backend `FINPATH_FRONTEND_ORIGINS` to the Pages origin
(for example `https://finpath.pages.dev`). Do not remove `Origin` or
`Sec-Fetch-Site`, and do not disable CSRF validation to make the proxy work.

### Render Free web service

Create a Render **Docker** web service with:

- Dockerfile path: `backend/Dockerfile`
- Health check path: `/health`
- Instance type: Free (0.1 CPU, 512 MB)
- No persistent disk; PostgreSQL is the source of truth
- `PORT` is supplied by Render; the entrypoint binds `0.0.0.0`
- `FINPATH_RUN_STARTUP_MIGRATIONS=false`

Production environment variables for the email-verification mode:

| Variable | Purpose |
|---|---|
| `FINPATH_ENVIRONMENT=production` | Enables production fail-closed validation |
| `FINPATH_ACCESS_TOKEN_SECRET` | Long random token-signing secret |
| `FINPATH_DATABASE_URL` | Neon runtime URL as `finpath_app`, TLS required |
| `FINPATH_REGISTRATION_DATABASE_URL` | Neon registration URL as `finpath_registration` |
| `FINPATH_FRONTEND_ORIGINS` | Pages origin, for example `https://finpath.pages.dev` |
| `FINPATH_REQUIRE_EMAIL_VERIFICATION=true` | Require verified email ownership |
| `FINPATH_EMAIL_BACKEND=resend` | Production email backend |
| `FINPATH_EMAIL_FROM` | Verified Resend sender address |
| `FINPATH_RESEND_API_KEY` | Render secret; never exposed to Pages or Vite |
| `FINPATH_REFRESH_COOKIE_SECURE=true` | Required in production |
| `FINPATH_RUN_STARTUP_MIGRATIONS=false` | Do not migrate/seed on every cold start |

For the current no-email MVP, use the same runtime/database/frontend/cookie
variables and the startup flag, but omit all Resend variables:

```text
FINPATH_REQUIRE_EMAIL_VERIFICATION=false
FINPATH_ALLOW_UNVERIFIED_REGISTRATION=true
```

The explicit opt-in keeps production fail-closed by default. Never set it for a
deployment that requires verified email ownership. In this mode registration
creates already-verified accounts, no verification token or email is created,
and password-reset email delivery is unavailable. The API returns its existing
generic reset-request response without creating an undelivered reset token.

Do not store `FINPATH_MIGRATION_DATABASE_URL` on Render. The migrator credential
belongs only in the trusted local or CI environment that runs migrations and the
seed; the running web service does not need it.

Production startup fails if verification is enabled without Resend credentials,
if verification is disabled without the explicit unverified-registration opt-in,
if the refresh cookie is made insecure, or if the access-token secret is missing.


Render Free constraints:

- Sleeps after about 15 minutes without inbound traffic and may take about a
  minute to wake.
- 750 instance hours per month; 512 MB; 0.1 CPU; ephemeral filesystem.
- No shell, one-off jobs, or private inbound networking.
- Outbound HTTPS (Resend and Neon) and PostgreSQL ports are available; SMTP
  ports 25/465/587 are blocked.

The first request after sleep may be slow or fail while the service starts. The
frontend retries only the safe `GET /auth/csrf` and `GET /auth/me` calls, at most
twice. A refresh-token `POST` is never retried automatically because its
response may have been lost after rotation. If refresh fails because of a
cold-start or network error, the UI asks the user to sign in again; if CSRF
initialization fails, the restore screen offers a safe **Try again** action.
Do not add artificial keep-alive traffic.

### Migration execution

Migrations and the idempotent seed run separately from Render startup. Run them
from a trusted local or CI environment with the dedicated migrator identity and
the direct Neon endpoint (not the pooled endpoint):

```sh
cd backend
FINPATH_MIGRATION_DATABASE_URL='postgresql+psycopg://finpath_migrator:<password>@<neon-direct-host>/<database>?sslmode=require' \
  .venv/bin/python -m alembic upgrade head
FINPATH_MIGRATION_DATABASE_URL='postgresql+psycopg://finpath_migrator:<password>@<neon-direct-host>/<database>?sslmode=require' \
  .venv/bin/python -m app.db.seed
```

Expected head: `c4f7a1b9e2d6`. Do not run these commands against an external
database until the deployment step is explicitly approved.

`FINPATH_RUN_STARTUP_MIGRATIONS` defaults to `true`, preserving local Docker
Compose behavior. Render sets it to `false`.

### Neon Free PostgreSQL

Recommended configuration:

- PostgreSQL 17.
- A **direct** connection for the initial deployment and for migrations.
  Neon's pooled PgBouncer endpoint uses transaction mode, which does not support
  session-level `SET`, temporary tables, or session advisory locks. FinPath uses
  transaction-scoped `SET TRANSACTION` and `pg_advisory_xact_lock`, so direct
  connections are the safest choice.
- `sslmode=require` on every connection URL.
- Separate `finpath_migrator`, `finpath_app`, and `finpath_registration`
  identities. Never use a Neon administrative/superuser identity at runtime.
- Keep `pool_pre_ping=True`; the existing pool is 5 connections plus 10 overflow
  per engine, well within a small Neon compute's direct-connection limit.

Neon Free provides 100 CU-hours/month, 1 GB storage, 5 GB egress, scale-to-zero,
no private networking, and no PostgreSQL superuser. Data persists when compute
suspends. **UNVERIFIED:** transferring database ownership to `finpath_migrator`.
If Neon's administrative identity cannot transfer ownership, keep it as the
database owner and grant `finpath_migrator` `USAGE, CREATE` on `public`, then run
migrations as `finpath_migrator`; do not grant runtime DDL or use the
administrator as the application identity. Verify all grants before relying on
production financial data.

### Remaining deployment blockers

- **Resend (only if email verification is required):** a verified sender
  domain/address must already be owned. The current no-email MVP does not use
  Resend. Do not purchase a domain for this task.
- **Neon role ownership and grants:** unverified until a Neon project exists.
- **Render source:** the workspace is not a Git repository; a Git source or
  prebuilt image must be approved before Render can deploy.
- **Render cold-start availability:** accepted as a Free-tier limitation.
- **Actual provisioning:** no Cloudflare, Render, or Neon resource has been
  created, and no migration or deployment has run.
- **No external integration has been tested:** proxy cookies/CSRF, Render wake,
  Neon migrations, and optional Resend delivery remain unverified until a
  separately approved deployment task.

## Production email (Resend)

The current strict-$0 MVP does **not** require Resend. It runs with
`FINPATH_REQUIRE_EMAIL_VERIFICATION=false` and
`FINPATH_ALLOW_UNVERIFIED_REGISTRATION=true`, so registration creates
already-verified accounts and no verification email is generated. Password-reset
email delivery is unavailable in this mode; the reset-request endpoint keeps its
generic response and does not store an undelivered reset token. No Resend
account, API key, sender, or verified domain is required.

A deployment that requires email ownership must keep verification enabled and
configure the backend with:

- `FINPATH_REQUIRE_EMAIL_VERIFICATION=true`
- `FINPATH_EMAIL_BACKEND=resend`
- `FINPATH_RESEND_API_KEY` — secret API key, set only in the deployment
  platform’s secret store.
- `FINPATH_EMAIL_FROM` — a sender address on a domain or address verified in
  Resend.

The backend calls the fixed Resend endpoint `https://api.resend.com/emails`
with a 10-second timeout. Provider rejection or network failure leaves the
account unverified and allows a later verification resend. Tokens, API keys,
and message contents are never logged. Production startup fails when Resend is
selected without an API key or sender address, and production verification
cannot use the development outbox.

A verified Resend sending domain/address is required before real delivery can
be tested. Resend Free allows 3,000 emails/month and 100/day. If no verified
domain is already owned, production email verification is an unresolved
deployment blocker; do not substitute the Resend test sender or purchase a
domain for this task. Automated tests mock the Resend HTTP request and do not
send email.

## Documentation

- [Project specification](PROJECT_SPEC.md)
- [AI development workflow](AI_WORKFLOW.md)
- [Ordered task checklist](TASKS.md)
- [Current progress and next task](PROGRESS.md)