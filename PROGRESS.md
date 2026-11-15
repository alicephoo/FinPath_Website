# FinPath Progress

**Last updated:** 2026-11-15
**Current status:** Implemented for the documented in-scope features and live in production. End-to-end observability (request-ID correlation, structured domain events, and an in-process metrics registry) is implemented and CI-verified at commit `33b3e05`, with metrics disabled in production by default. User-impact alerting (Phase 2A–2C) is implemented in the repository but is **not yet active**: the hourly workflow takes effect only after it is committed to the default branch and a run succeeds. See [docs/ALERTING_RUNBOOK.md](docs/ALERTING_RUNBOOK.md). Deferred items are listed in [Section 7](#7-known-limitations-and-deferred-features).
**Authoritative specification:** [PROJECT_SPEC.md](PROJECT_SPEC.md). This log summarizes progress and evidence; it does not override the specification.
**Public overview:** [README.md](README.md).

Evidence dates are stated explicitly. Test counts are a snapshot of one CI run and are not permanent totals.

## 1. Project Status

FinPath is a full-stack personal finance and goal-planning web application for tracking income, expenses, loans, savings goals, and travel budgets in Myanmar Kyat (MMK). The frontend uses React, TypeScript, Vite, and Tailwind CSS; the backend uses FastAPI and Python; PostgreSQL is the production database.

| Area | Status |
|---|---|
| Application (documented scope) | Implemented — five tabs, backend-owned financial rules, PostgreSQL persistence |
| Automated tests | Green in CI — backend, frontend, Playwright, Docker image build, and container smoke jobs |
| Observability (C1–C3.2) | Implemented and CI-verified at commit `33b3e05` — request-ID correlation, structured domain events, and a Prometheus-format metrics registry with a protected `/metrics` endpoint; metrics disabled in production by default |
| User-impact alerting (Phase 2A–2C) | Implemented locally, not active — four synthetic probes, a pure evaluator, a standard-library runner, 90 focused tests, and an hourly GitHub Actions workflow; activation pending default-branch merge and a successful run |
| CI | Green — run `43174534132`, 2026-11-12 |
| Deployment | Live — Cloudflare Pages frontend and Render backend verified 2026-11-12; `/health` and `/ready` re-probed 2026-11-13 |
| Documentation | `PROJECT_SPEC.md` is authoritative; `README.md` is current; `AGENTS.md` defines agent context and collaboration; `docs/ALERTING_RUNBOOK.md` documents alerting; the public [FinPath — Development Tracker](https://github.com/users/alicephoo/projects/1) tracks active tasks; this progress log was refreshed 2026-11-15 |
| Zoomcamp rubric | 7 criteria complete, 4 partial, 1 missing; current maximum 22/24 — see [Section 6](#6-ai-dev-tools-zoomcamp-rubric-status) |
| Deferred scope | Admin, rate limiting, MFA, RLS, and several API-only UI flows — see [Section 7](#7-known-limitations-and-deferred-features) |

## 2. Completed Milestones

| Date | Milestone | Evidence |
|---|---|---|
| Through 2026-11-12 | Application implementation reached `main` at commit `45527fffe2fa36e4a0c90dd4f9592aff188be1bf`, including authentication, financial APIs, PostgreSQL persistence, dashboard reports, and CSV export | Git `HEAD` = `origin/main`; `PROJECT_SPEC.md` Appendix A |
| 2026-11-12 | CI run `43174534132` passed all jobs: 250 backend tests, 316 frontend tests across 19 files, and 10 Playwright tests across 5 specs | [CI run](https://github.com/alicephoo/FinPath_Website/actions/runs/43174534132); `.github/workflows/ci.yml` |
| 2026-11-12 | Cloudflare Pages frontend and Render backend confirmed live; Pages deployment check-run succeeded | `PROJECT_SPEC.md` §10.7 and Appendix B.3 |
| 2026-11-13 | Render `/health` returned `200 {"status":"ok"}` and `/ready` returned `200 {"status":"ready"}` | Deployment re-probe recorded in `PROJECT_SPEC.md` Appendix B.3 |
| 2026-11-13 | `PROJECT_SPEC.md` rewritten as the authoritative product reference | File mtime; 13 sections plus Appendices A and B |
| 2026-11-13 | `README.md` rewritten for public discovery, local setup, architecture, and limitations | File mtime; 341 lines |
| 2026-11-14 | `PROGRESS.md` refreshed to reflect verified implementation, CI, deployment, and rubric status | This document |
| 2026-11-14 | Added `AGENTS.md` and integrated it with `AI_WORKFLOW.md` to document the six-agent team, context precedence, handoffs, and approval gates | `AGENTS.md`; `AI_WORKFLOW.md`; `README.md` documentation table |
| 2026-11-15 | End-to-end observability (C1–C3.2) committed as `33b3e05df7453c35c3b7642473c8f0134fcf1619` with message `feat: add end-to-end observability` and pushed to `origin/main` | Git `HEAD` = `origin/main` at the same commit |
| 2026-11-15 | GitHub Actions reported all five CI jobs passing for commit `33b3e05` (confirmed by the project owner; no run ID or URL supplied) | User confirmation; `.github/workflows/ci.yml` |
| 2026-11-15 | User-impact alerting implemented locally (Phase 2A–2C): pure evaluator, standard-library probe runner, focused tests, and an hourly GitHub Actions workflow. Not yet active | `backend/app/core/alerting.py`, `backend/app/core/alerting_runner.py`, `backend/tests/test_alerting.py`, `backend/tests/test_alerting_runner.py`, `.github/workflows/alerting.yml`, `docs/ALERTING_RUNBOOK.md`; 65 evaluator + 25 runner tests pass in the focused run |
| 2026-11-15 | Created and verified the public GitHub Projects v2 board [FinPath — Development Tracker](https://github.com/users/alicephoo/projects/1) with six draft tasks. No Issues, pull requests, labels, or milestones were created; no repository files changed | Board URL; `gh project view` (`public: true`, 6 items); unauthenticated HTTP 200; Todo/In Progress/Done status field |

## 3. Current Implementation Status

Status reflects `PROJECT_SPEC.md` Appendix A and the implemented API/UI surface.

| Area | Status | Notes |
|---|---|---|
| Registration, email verification, login, refresh, logout | Implemented | Argon2id passwords, 15-minute JWT access tokens, rotating HttpOnly refresh cookies, CSRF protection, trusted-origin checks |
| Account status enforcement | Implemented | Suspension/closure checks exist; no Admin route changes status |
| Five-tab frontend shell | Implemented | Dashboard, Transactions, Loans, Savings Goals, Travel Budget, with accessible keyboard navigation |
| Dashboard and reports | Implemented | Account balance, all-time and current-month totals, six-month cash flow, expense categories, recent transactions |
| Starting balance | Implemented | ETag-protected set, replace, and clear with an effective date |
| Transactions | Implemented | CRUD, 11 seeded categories, inclusive filters, cursor pagination, CSV inclusion |
| Loans | Partially implemented | Principal, repayments, derived outstanding principal; CRUD except delete; full-reversal corrections are API-only |
| Savings goals | Partially implemented | Goals, contributions, withdrawals, progress, archive/restore API; archive/restore UI is absent; corrections and daily plans are absent |
| Travel budget | Implemented | Trips, category budgets, actual spending, bounded JSON import, completion, cancellation |
| CSV export | Implemented | `GET /api/v1/reports/export.csv` from the Dashboard dataset |
| PostgreSQL persistence | Implemented | 20 tables, 6 Alembic revisions, seeded roles and categories |
| ETags and idempotency | Implemented | `If-Match` concurrency protection and scoped 24-hour idempotency records |
| Observability (C1–C3.2) | Implemented | `X-Request-ID` correlation end to end; allowlisted structured domain events; dependency-free metrics registry; HTTP request, duration, and slow-request metrics; protected `/metrics`; disabled by default |
| User-impact alerting (Phase 2A–2C) | Implemented locally, not active | Four production probes (`frontend`, `backend_health`, `backend_ready`, `auth_smoke`), pure evaluator, standard-library runner with one retry and a 90-second budget, hourly workflow; activation pending default-branch merge and a successful run |
| Admin console, role management, audit UI | Deferred | Admin role and append-only audit table exist in the schema; no Admin API or UI |
| Rate limiting | Not implemented | No request throttling or `429` handling |
| MFA, passkeys, social login, email change, RLS | Deferred | Not implemented |
| Password change and reset | Backend only | Backend endpoints exist; the frontend does not expose the screens |

## 4. Testing and CI Evidence

### CI workflow

`.github/workflows/ci.yml` runs on pushes and pull requests to `main` with read-only `contents` permission. It has five jobs:

| Job | What it verifies |
|---|---|
| Backend tests | Python 3.12, PostgreSQL 17 service container, migrations and seed, Pytest suite |
| Frontend tests, type check, and build | Node.js 22, `npm ci`, Vitest, `tsc -b`, `vite build` |
| Playwright E2E (Chromium) | Real backend against the test database, Vite dev server, serial browser tests |
| Docker image builds | Builds the backend and frontend images |
| Backend container staging-lite | Runs the built backend image against a disposable PostgreSQL 17 database, applies migrations, and checks `/health`, `/ready`, and a non-mutating invalid-login smoke test |

The workflow uploads Pytest and Playwright reports as artifacts. It does not deploy or create cloud resources.

### Alerting workflow

`.github/workflows/alerting.yml` is a separate scheduled monitoring workflow. It runs hourly (`0 * * * *` UTC) and on manual `workflow_dispatch`, with `contents: read` permission, a 3-minute job timeout, and no push or pull-request triggers. It runs `python -m app.core.alerting_runner` from `backend` on Python 3.12 with no dependency install, and a non-zero runner exit fails the job. It does not deploy. The workflow is **not yet active**: it takes effect only after being merged to the default branch and completing a successful run, as described in `docs/ALERTING_RUNBOOK.md`.

### Verified snapshot — run `43174534132`, 2026-11-12

| Suite | Result |
|---|---|
| Backend (Pytest, PostgreSQL 17) | 250 passed in 55.46 s |
| Frontend (Vitest, 19 files) | 316 passed; type check and production build succeeded |
| End-to-end (Playwright, Chromium, 5 specs) | 10 passed in 31.4 s |

The run executed against commit `45527fffe2fa36e4a0c90dd4f9592aff188be1bf` on `main`. These counts are dated evidence and may change as the project evolves.

### Observability verification — commit `33b3e05`, 2026-11-15

The observability work (C1–C3.2) was committed as `33b3e05df7453c35c3b7642473c8f0134fcf1619` (`feat: add end-to-end observability`) and pushed to `origin/main`. GitHub Actions reported **all five jobs passing** for that commit, as confirmed by the project owner. No run ID or URL is recorded here because none was supplied. CI verification confirms the code builds and its tests pass; it does **not** mean metrics are collected in production.

### Known coverage gaps

- Playwright runs Chromium only; there is no Firefox or WebKit matrix.
- No load, stress, or penetration testing is part of CI.
- Email delivery is tested through the local outbox; live Resend delivery is not covered.
- Savings archive/restore and loan repayment corrections have backend tests but no UI or E2E coverage because the UI does not expose those actions.
- There are no Admin or rate-limiting tests because those features do not exist.
- The PostgreSQL domain-event boundary tests (`backend/tests/test_domain_events_postgres.py`) skip when no test database is configured, so post-commit emission, replay suppression, archive/restore no-op suppression, and failed-write absence are unverified without one (finding M1).

Full details are in `PROJECT_SPEC.md` §11.7.

## 5. Deployment Status

| Check | Result | Evidence date |
|---|---|---|
| Frontend | Live at <https://finpath-website.pages.dev> | Confirmed 2026-11-12; Cloudflare Pages deployment check-run succeeded |
| Backend | Live at <https://finpath-website.onrender.com> | Confirmed 2026-11-12 |
| `GET /health` | `200 {"status":"ok"}` | Re-probed 2026-11-13 (cold start ≈23 s) |
| `GET /ready` | `200 {"status":"ready"}` | Re-probed 2026-11-13 |
| Deployment commit | `45527fffe2fa36e4a0c90dd4f9592aff188be1bf` | `main` = `origin/main` at verification |

Production architecture:

- **Cloudflare Pages** serves the Vite build and a Pages Function proxy for `/api/*`. The proxy forwards only to the fixed `RENDER_BACKEND_URL` origin.
- **Render** runs the FastAPI Docker service and supplies `PORT`; startup migrations are disabled in production.
- **Neon** hosts PostgreSQL 17 with separate runtime, registration, and migration roles. Neon follows the documented production architecture but was not directly probed for this log.
- Migrations are applied separately with `FINPATH_MIGRATION_DATABASE_URL` as `finpath_migrator`.

The Pages root could not be re-probed from the authoring network during the final documentation pass; its live status rests on the 2026-11-12 owner confirmation and successful Pages check-run. No deployment, provider setting, database migration, or secret was changed while producing this document.

## 6. AI Dev Tools Zoomcamp Rubric Status

This assessment uses repository evidence, not documentation claims alone. **Complete** means the artifact and its supporting evidence exist. **Partial** means meaningful evidence exists but a rubric-relevant gap remains. **Missing** means no supporting artifact was found.

| # | Criterion | Pts | Status | Evidence | Remaining work |
|---|---|---|---|---|---|
| 1 | Problem Description | 2 | Complete | `PROJECT_SPEC.md` §1.1 problem, §1.2 personas, §1.3 goals; README "The Problem" | None; keep aligned with product changes |
| 2 | Product Specification | 2 | Partial | `PROJECT_SPEC.md` satisfies the written specification requirement: 13 sections covering journeys, functional requirements, financial rules, API contracts, deployment, and acceptance criteria (§11.5 `AC-1`–`AC-16`, §11.6 deferred criteria), plus Appendices A–B. The public [FinPath — Development Tracker](https://github.com/users/alicephoo/projects/1) exists with six draft tasks that include context, scope, acceptance criteria, and agent roles | Substantive agent usage through an implementation task has not yet been demonstrated; the board-setup item is the only agent update. Track a real task through Todo → In Progress → Done with verified evidence, then upgrade to Complete (documentation) |
| 3 | Agent Team Specification | 2 | Complete (documentation) | `AGENTS.md` defines six roles with purpose, responsibilities, inputs/outputs, permitted areas, boundaries, invocation triggers, and reviewers, plus delegation, handoff, and conflict procedures; `AI_WORKFLOW.md` links the task loop to that team process | Evidence is prepared for evaluation; the score depends on `AGENTS.md` being included in the submitted repository and on grader assessment |
| 4 | Context Management | 2 | Complete (documentation) | `AGENTS.md` defines documentation precedence, a context-refresh rule, a handoff template, a documentation-update matrix, verified commands, and a completion report; `PROGRESS.md` records status and evidence | Evidence is prepared for evaluation; the score depends on the referenced files being included in the submitted repository and on grader assessment |
| 5 | Application | 2 | Complete | Five-tab full-stack application with implemented auth and financial APIs; live Pages and Render URLs | None for the documented scope |
| 6 | Testing | 2 | Complete | CI snapshot: 250 backend tests, 316 frontend tests across 19 files, 10 Playwright tests across 5 specs | Optional hardening: browser matrix, load tests; not required by current scope |
| 7 | Containerization | 2 | Complete | `backend/Dockerfile` (Python 3.12 slim, non-root), `frontend/Dockerfile` (build + Nginx), `compose.yaml` with PostgreSQL health check and role initialization | CI does not build images; optional addition |
| 8 | Deployment | 2 | Complete | Live Cloudflare Pages frontend and Render backend; `/health` and `/ready` verified 2026-11-13 at commit `45527ff`; Neon documented | Neon was not directly probed; Pages root was not re-probed from the authoring network |
| 9 | CI/CD | 3 | Partial | Five-job CI workflow (backend, frontend, Playwright E2E, Docker image builds, and backend container staging-lite) is green on push and pull request, with read-only permissions and uploaded artifacts | No CD automation exists; deployment is provider-driven/manual. Add a deploy workflow or document the release procedure |
| 10 | Observability | 2 | Partial | Structured JSON request logs with `X-Request-ID` correlation, durations, slow-request warnings above 1000 ms, and redaction; privacy-safe allowlisted domain events; a dependency-free Prometheus-format metrics registry with HTTP request, duration, and slow-request metrics and a protected `/metrics` endpoint (C1–C3.2, commit `33b3e05`); `/health` and `/ready`; coverage in `backend/tests/test_observability.py`, `backend/tests/test_metrics.py`, and `backend/tests/test_http_metrics.py` | Metrics remain disabled in production, and there is no tracing, dashboard, log aggregation/retention, or documented SLO; user-impact alerting is implemented locally but not yet active (see #11) |
| 11 | Alerting on User Impact | 1 | Partial | Four synthetic production probes (`frontend`, `backend_health`, `backend_ready`, `auth_smoke`) with a pure evaluator, a standard-library runner, retries, a 90-second budget, and an hourly schedule/manual workflow; 65 evaluator + 25 runner tests pass | Activation is pending: merge the workflow to the default branch, enable Actions, and complete a green scheduled or manual production run. Until then it is not live coverage |
| 12 | On-Call Agent | 2 | Missing | No on-call rotation, alert-linked runbooks, or automated responder | Define an on-call process and runbooks, and connect them to alerts |

**Status arithmetic:** 7 criteria complete = 14 points documented; 4 criteria partial = up to 8 additional points (#2 Product Specification up to 2, #9 CI/CD up to 3, #10 Observability up to 2, #11 Alerting up to 1); 1 criterion missing (On-Call Agent) = 0 points. The current maximum without completing On-Call Agent is therefore **22/24**, not 24/24. This is a potential maximum, not an awarded score: partial criteria may receive less than full credit, so an estimated current score could be lower. The previously reported **14/24** is retained only as historical context. The verified implementation floor from the six application/testing/deployment criteria remains 12/24, and the Agent Team Specification and Context Management entries rest on repository documentation rather than a grader award.

**Documentation evidence, not an awarded score.** The Agent Team Specification and Context Management rows point to repository artifacts, not to an official evaluation. Their status depends on `AGENTS.md`, `AI_WORKFLOW.md`, and the referenced documents being included in the submitted repository and on the grader's assessment. This update produced no new test, deployment, or runtime evidence.

## 7. Known Limitations and Deferred Features

### Product and security

- **Admin is deferred.** Account management, role assignment, suspension/closure operations, and audit-log browsing are unavailable. The `admin` role and audit table exist in the schema without an API or UI.
- **Rate limiting is absent.** Authentication and write endpoints are not throttled.
- **No PostgreSQL row-level security.** Ownership is enforced by owner-scoped application queries.
- **Password change and reset UI are absent.** Backend endpoints exist.
- **Savings archive/restore is API-only.** The UI does not expose the actions.
- **Loan repayment corrections are API-only.** The UI shows corrected status but offers no correction action.
- **No email change, MFA, passkeys, or social login.**
- **No loan or savings-goal deletion.** Archive and cancellation semantics preserve history.
- **Categories are fixed** to 11 seeded rows.
- **CSV is the only export format,** limited to the Dashboard dataset.
- **No bank sync, interest, amortization, or multi-currency support.**

### Financial and data behavior

- Future-dated transactions and loan movements are included in the current balance because the balance query has no upper date bound.
- Reports default to the API server's current date; the stored user timezone is not applied to report boundaries.
- Dashboard allocations are not date-filtered, so historical point-in-time reconstruction is not supported.
- Hard-deleting a transaction removes it from history and changes prior totals.
- CSV formula-injection protection alters leading dangerous characters, so exports are for spreadsheet use rather than byte-for-byte archival.

### Operational and testing

- Render free-tier cold starts were observed at roughly 23 seconds during verification.
- Neon and Render are external dependencies; outages, cold starts, and provider limits affect availability.
- No automated backup/restore verification is documented.
- Public API docs are disabled in production.
- Email verification in production depends on Resend configuration and a verified sender domain.
- In-process metrics exist but are disabled in production by default; there is no tracing or on-call tooling. User-impact alerting is implemented in the repository but is not yet active in production.
- Metrics are in-process only: counters reset on process restart and are not shared across multiple backend instances.
- Playwright covers Chromium only; no load, stress, or penetration tests are in CI.

The full list and open questions are in `PROJECT_SPEC.md` §12.

### Observability findings (open, not fixed)

The combined observability review (C1–C3.2) raised one medium and four low findings. None block the observability commit, and none are fixed.

- **M1 (Medium):** the PostgreSQL domain-event boundary tests in `backend/tests/test_domain_events_postgres.py` skip when no test database is configured, so post-commit emission, idempotent replay suppression, archive/restore no-op suppression, and failed-write absence are unverified without one.
- **L1 (Low):** when `crypto.randomUUID` is unavailable, a malformed caller-supplied `X-Request-ID` can be forwarded instead of being dropped.
- **L2 (Low):** the frontend surfaces the error-envelope `request_id` fallback without the same pattern validation it applies to the response header.
- **L3 (Low):** post-commit `log_domain_event` calls are not defensively wrapped, so a logging failure could surface as a sanitized `500` after a successful write.
- **L4 (Low):** the slow-request threshold and HTTP metrics read module-global `settings` rather than a per-app configuration object.

## 8. Remaining Work and Priorities

### Rubric priorities

1. **Agent Team Specification** — documentation evidence is now in `AGENTS.md`; include the file in the submitted repository and validate it against the grader's criteria.
2. **Context Management** — documentation evidence is now in `AGENTS.md` (precedence, refresh/handoff, update matrix, completion report) and `AI_WORKFLOW.md`; include both in the submitted repository.
3. **CI/CD** — add automated deployment or document a repeatable manual release procedure with required secrets and approvals.
4. **Observability** — request-ID correlation, structured domain events, and an in-process metrics registry are implemented and CI-verified (commit `33b3e05`), but metrics are disabled in production. Remaining: decide whether to activate production metrics and how to scrape them, resolve the open findings M1 and L1–L4, and add tracing, dashboards, log aggregation/retention, and explicit SLOs.
5. **Alerting on User Impact** — the evaluator, runner, tests, and hourly workflow are implemented locally. Remaining: merge the workflow to the default branch, enable Actions, and record a green scheduled or manual production run (see `docs/ALERTING_RUNBOOK.md`). On-call routing remains part of #12.
6. **On-Call Agent** — define a rotation, runbooks, and an alert-linked response process.

### Product and security backlog

7. Rate limiting for authentication and write endpoints.
8. Admin API/UI, runtime `user_roles` writes, first-Admin bootstrap, and audit-log browsing.
9. PostgreSQL row-level security.
10. Password change/reset UI, email change, MFA, passkeys, and social login.
11. UI for savings archive/restore and loan repayment corrections; savings corrections and daily plans.
12. Automated backup/restore verification for Neon.
13. Broader E2E browser coverage and load/penetration testing.

A public GitHub Projects v2 board, [FinPath — Development Tracker](https://github.com/users/alicephoo/projects/1), is the active task tracker. GitHub Projects draft items are used instead of GitHub Issues; Issues, labels, and milestones remain unused unless separately approved. `TASKS.md` remains a historical record, and this document plus `PROJECT_SPEC.md` remain the durable progress and specification record.

## 9. Verification Evidence and References

| Source | Use |
|---|---|
| [PROJECT_SPEC.md](PROJECT_SPEC.md) | Authoritative product behavior, financial rules, acceptance criteria, dated implementation status, and deployment evidence |
| [README.md](README.md) | Public introduction, setup, environment variables, architecture, and limitations |
| [AGENTS.md](AGENTS.md) | Repository instructions: context precedence, six-agent team, handoff and approval workflow, safety rules, and completion report |
| [AI_WORKFLOW.md](AI_WORKFLOW.md) | Task execution loop, its relationship to `AGENTS.md`, and the simplified single-agent path |
| [API_CONTRACTS.md](API_CONTRACTS.md) | Endpoint, error, ETag, and idempotency contracts |
| [BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md) | Schema, privilege matrix, and deferred Admin design |
| `.github/workflows/ci.yml` | CI jobs, triggers, permissions, and artifacts; confirms that CI does not deploy |
| `backend/app/core/observability.py` | Structured JSON logs, request-ID correlation, timing, slow-request threshold, domain events, and HTTP metrics instrumentation |
| `backend/app/core/metrics.py` | Dependency-free, thread-safe Prometheus-format metrics registry (counters, gauges, histograms) |
| `backend/tests/test_observability.py` | Request-ID correlation, structured logging, domain-event allowlist, and redaction tests |
| `backend/tests/test_metrics.py` | Metrics registry validation, semantics, rendering, and concurrency tests |
| `backend/tests/test_http_metrics.py` | HTTP instrumentation and protected `/metrics` endpoint tests |
| `backend/tests/test_domain_events_postgres.py` | PostgreSQL domain-event boundary tests (skip without a configured test database) |
| `backend/app/main.py` | Router registration, `/health`, `/ready`, middleware |
| `backend/app/core/alerting.py` | Pure evaluator: canonical requiredness, severity, duplicate/missing-probe handling, and allowlisted diagnostics |
| `backend/app/core/alerting_runner.py` | Four production probes, retry policy, 90-second budget, and exit codes |
| `backend/tests/test_alerting.py`, `backend/tests/test_alerting_runner.py` | Focused evaluator and runner tests (65 + 25) |
| `.github/workflows/alerting.yml` | Hourly scheduled and manual production probe workflow |
| `docs/ALERTING_RUNBOOK.md` | Probe behavior, severities, retries, investigation, and activation steps |
| [FinPath — Development Tracker](https://github.com/users/alicephoo/projects/1) | Public Projects v2 board with draft tasks, acceptance criteria, agent roles, and evidence |
| `compose.yaml`, `backend/Dockerfile`, `frontend/Dockerfile` | Containerization evidence |
| `frontend/e2e/`, `frontend/playwright.config.ts` | Playwright specs and serial test configuration |
| [CI run `43174534132`](https://github.com/alicephoo/FinPath_Website/actions/runs/43174534132) | Dated test counts and green CI evidence |
| Git commit `45527fffe2fa36e4a0c90dd4f9592aff188be1bf` | Deployment and CI commit; `HEAD` = `origin/main` at verification |
| Git commit `33b3e05df7453c35c3b7642473c8f0134fcf1619` | Observability (C1–C3.2) commit, `feat: add end-to-end observability`; pushed to `origin/main` |

### Verification method and scope

- Repository state, CI configuration, observability code and tests, container files, and deployment evidence were inspected during the earlier progress refresh.
- Deployment results are the dates recorded in `PROJECT_SPEC.md` Appendix B.3; they were not re-probed during the 2026-11-14 documentation updates.
- Tests, migrations, Docker builds, and deployments were not run for the 2026-11-14 documentation updates.
- The 2026-11-14 documentation update created `AGENTS.md` and edited `AI_WORKFLOW.md`, `README.md`, and `PROGRESS.md`. No code, tests, configuration, environment variables, database, Docker state, Git history, or provider settings were changed; nothing was staged, committed, pushed, or deployed.
- The 2026-11-15 documentation update recorded the observability implementation and CI verification at commit `33b3e05` across `PROGRESS.md`, `PROJECT_SPEC.md`, `README.md`, `backend/README.md`, and `frontend/README.md`. It changed only documentation; no code, tests, configuration, environment variables, database, Docker state, Git history, or provider settings were changed; nothing was staged, committed, pushed, or deployed by this update.
- The 2026-11-15 alerting work added `backend/app/core/alerting.py`, `backend/app/core/alerting_runner.py`, their focused tests, `.github/workflows/alerting.yml`, and `docs/ALERTING_RUNBOOK.md` (Phase 2A–2C). The Phase 2D documentation pass changed only documentation (`PROGRESS.md`, `README.md`, `PROJECT_SPEC.md`, and the runbook). The workflow was not executed against production, and no environment, provider, database, or deployment setting was changed; nothing was staged, committed, pushed, or deployed.
- The 2026-11-15 tracker update created the public [FinPath — Development Tracker](https://github.com/users/alicephoo/projects/1) and six draft items. It created no GitHub Issues, pull requests, labels, or milestones, and changed no repository files. Public visibility was verified with `gh project view` and an unauthenticated HTTP 200.
