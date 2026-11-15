# FinPath Agent Instructions

This file is the repository-level operating guide for AI agents and the humans who direct them. It defines project context, documentation precedence, the agent team, collaboration rules, verified commands, safety constraints, and the required completion report.

Product rules, API contracts, and schema details live in the linked documents. Link to them rather than duplicating them here.

## 1. Project context

FinPath is a full-stack personal finance and goal-planning web application for tracking income, expenses, loans, savings goals, and travel budgets in Myanmar Kyat (MMK).

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite, Tailwind CSS |
| Backend | FastAPI, Python, SQLAlchemy 2, Alembic, psycopg 3 |
| Database | PostgreSQL 17 |
| Production | Cloudflare Pages + Pages Function proxy → Render (FastAPI Docker) → Neon PostgreSQL |

The backend owns financial rules and persistence. The frontend renders API responses and is never the source of the financial ledger.

### Repository map

- `frontend/src/` — React application, API client, auth context, and feature sections.
- `frontend/functions/api/proxy.ts` — production Pages Function proxy, configured by `RENDER_BACKEND_URL`.
- `frontend/e2e/` — Playwright specs and `start-backend.sh` test-database launcher.
- `backend/app/` — FastAPI routes, services, models, and core configuration.
- `backend/migrations/` — Alembic revisions.
- `backend/tests/` — Pytest suites, including PostgreSQL-backed tests.
- `backend/Dockerfile`, `backend/docker/` — API image and startup entrypoint.
- `frontend/Dockerfile`, `compose.yaml` — frontend image and local full-stack orchestration.
- `.github/workflows/ci.yml` — build and test CI. It does not deploy.

### Documentation

| Document | Role |
|---|---|
| [`PROJECT_SPEC.md`](PROJECT_SPEC.md) | **Authoritative** product specification: scope, financial rules, security, API behavior, deployment, and acceptance criteria |
| [FinPath — Development Tracker](https://github.com/users/alicephoo/projects/1) | **Active task tracker** (public GitHub Projects v2 board): tasks, acceptance criteria, agent roles, and evidence |
| [`API_CONTRACTS.md`](API_CONTRACTS.md) | Endpoint contracts, error envelopes, ETags, and idempotency |
| [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) | Schema, privilege/protection matrix, and deferred design decisions |
| [`README.md`](README.md) | Public overview, setup, architecture, and limitations |
| [`frontend/README.md`](frontend/README.md) | Frontend setup, UI behavior, and frontend tests |
| [`backend/README.md`](backend/README.md) | Backend setup, API reference, database roles, and backend tests |
| [`PROGRESS.md`](PROGRESS.md) | Current status, dated evidence, and rubric status |
| [`TASKS.md`](TASKS.md) | Historical task checklist; **not authoritative** for current behavior |
| [`AI_WORKFLOW.md`](AI_WORKFLOW.md) | Task execution loop for agents and contributors |

## 2. Context precedence

When instructions or documents disagree, use this order:

1. Explicit human instructions for the current task.
2. `PROJECT_SPEC.md`.
3. `API_CONTRACTS.md` and `BACKEND_ARCHITECTURE.md`.
4. Root `README.md`.
5. Frontend and backend READMEs.
6. `PROGRESS.md` for current status.
7. `TASKS.md` for historical reference only.

If authoritative documents conflict, or a decision is not covered, stop and report the ambiguity. Do not invent a decision, silently choose a convention, or treat historical notes as current requirements. Record the open question for the human owner.

## 3. Agent team

FinPath uses six roles. Small tasks may be handled by one agent wearing several hats, but responsibilities and review rules still apply. This is a documented collaboration process, not automated orchestration.

### 3.1 Coordinator / Orchestrator

- **Purpose and responsibilities:** Own task intake, scope, sequencing, and handoffs. Restate the task and acceptance criteria, check the relevant specification, identify likely files and risks, decide whether work is one task or parallel slices, issue delegation packets, arbitrate conflicts, enforce approval gates, and confirm completion evidence.
- **Inputs and outputs:** Human request plus repository documentation → scoped task plan, delegation packets, decision notes, and final completion summary.
- **Permitted areas:** Read access to the whole repository; writes are limited to task plans and approved status/decision notes.
- **Boundaries:** Does not implement code by default, commit, push, deploy, run migrations, or change provider settings. Cannot override `PROJECT_SPEC.md` or the human owner.
- **Invocation:** Every task starts here; also invoked for cross-cutting work, conflicts, or ambiguous requirements.
- **Reviewer:** Human owner.

### 3.2 Frontend Engineer

- **Purpose and responsibilities:** Implement and maintain the React/TypeScript/Vite/Tailwind UI. Own components, tabs, forms, API-client usage, error/loading/empty states, accessibility, Vitest/RTL tests, and Playwright coverage for user-visible changes.
- **Inputs and outputs:** Delegation packet, `frontend/README.md`, and API contracts → code, tests, and a handoff note with commands and actual results.
- **Permitted areas:** `frontend/src/**`, `frontend/tests/**`, `frontend/e2e/**`, `frontend/functions/**`, and frontend configuration files.
- **Boundaries:** Does not change backend behavior or financial rules, add dependencies without approval, access databases directly, or invent API fields. Preserves the backend as the source of financial truth.
- **Invocation:** UI, client API, routing, styling, frontend tests, or Pages proxy behavior changes.
- **Reviewer:** QA & Testing Engineer; Security & Reliability Reviewer when auth, CSRF, cookies, or the proxy are touched.

### 3.3 Backend Engineer

- **Purpose and responsibilities:** Implement and maintain FastAPI routes, services, ORM models, and migrations. Own endpoint behavior, owner-scoped queries, financial rules, ETag/idempotency behavior, error envelopes, Alembic revisions, and Pytest coverage.
- **Inputs and outputs:** Delegation packet, `PROJECT_SPEC.md`, `API_CONTRACTS.md`, and `BACKEND_ARCHITECTURE.md` → code, migration, tests, and a handoff note.
- **Permitted areas:** `backend/app/**`, `backend/tests/**`, `backend/migrations/**`, `backend/pyproject.toml`, `backend/Dockerfile`, and `backend/docker/**`.
- **Boundaries:** Does not access production or development databases for mutations, run migrations without approval, change schema without an Alembic revision, loosen ownership or authorization checks, or alter secret handling without review.
- **Invocation:** API contract, persistence, financial calculation, auth/session, or migration changes.
- **Reviewer:** QA & Testing Engineer; Security & Reliability Reviewer when auth, ownership, money math, or migrations are touched.

### 3.4 QA & Testing Engineer

- **Purpose and responsibilities:** Independently verify behavior and prevent regressions. Run the narrowest relevant tests first, then broader suites, check acceptance criteria against the specification, verify error/empty/loading states, and confirm test-database safety.
- **Inputs and outputs:** Changed code plus acceptance criteria → test report with pass/fail results, coverage gaps, reproduction steps, and unrun tests with reasons.
- **Permitted areas:** Test suites, test configuration, `frontend/e2e/start-backend.sh`, and CI configuration. May add or adjust tests within the approved scope.
- **Boundaries:** Does not edit product code unless delegated, run destructive database cleanup, weaken assertions to force a pass, or claim an unrun test passed.
- **Invocation:** After every implementation handoff and before any commit or deployment recommendation.
- **Reviewer:** Coordinator / Orchestrator.

### 3.5 Security & Reliability Reviewer

- **Purpose and responsibilities:** Guard authentication, authorization, financial integrity, secrets, and production safety. Review ownership scoping, CSRF/cookies, error leakage, ETag/idempotency, migration safety, dependency changes, CI permissions, proxy behavior, and deployment prerequisites. Confirm that no secrets or `.env` contents enter the repository.
- **Inputs and outputs:** Diff plus specification security sections → approval or block with specific findings and required fixes.
- **Permitted areas:** Read access to all code and configuration; may add security tests when delegated.
- **Boundaries:** Does not change production, provider settings, or secrets; does not inspect secret values beyond confirming absence; does not waive `PROJECT_SPEC.md` security requirements unilaterally.
- **Invocation:** Changes touching auth, sessions, CSRF, ownership checks, money calculations, migrations, Docker/entrypoint, CI permissions, the proxy, or environment/configuration.
- **Reviewer:** Human owner.

### 3.6 Documentation & Context Manager

- **Purpose and responsibilities:** Keep repository context accurate, current, and non-duplicative. Update the correct document after behavior changes, maintain precedence and links, label historical documents, keep test counts dated, and record decisions and open questions.
- **Inputs and outputs:** Completed work plus handoff notes → documentation updates, decision entries, and a context-refresh summary.
- **Permitted areas:** Root documentation, `frontend/README.md`, `backend/README.md`, `API_CONTRACTS.md`, `BACKEND_ARCHITECTURE.md`, and `AGENTS.md`.
- **Boundaries:** Does not change product behavior or scope, edit untracked documentation without approval, treat `TASKS.md` as current, or document secrets.
- **Invocation:** Any user-visible, API, schema, test, or deployment change; end of each task; whenever documents conflict.
- **Reviewer:** Coordinator / Orchestrator.

## 4. Collaboration workflow

### 4.0 Task tracking

The public [FinPath — Development Tracker](https://github.com/users/alicephoo/projects/1) is the active task tracker; `TASKS.md` is historical.

1. The Coordinator checks the board before starting work and selects or confirms the matching draft task.
2. Confirm the task's acceptance criteria, scope and non-goals, and responsible agent role.
3. Move the task to `In Progress` before implementation begins.
4. Record actual implementation and verification evidence on the task: changed files, commands and actual results, tests not run and why, security/privacy impact, documentation updated, and remaining questions.
5. Move the task to `Done` only when its acceptance criteria are verified.

Never fabricate task status, assignments, comments, or evidence. Routine status and evidence updates on the approved board are authorized. Creating a new project, deleting or archiving items, or creating GitHub Issues, labels, or milestones still requires separate approval.

### 4.1 Delegation packet

The Coordinator issues a packet containing the task ID and goal, the relevant `PROJECT_SPEC.md` sections and acceptance criteria, in-scope areas and explicit non-goals, existing conventions and risks, required tests, any approval gate, and the expected handoff format. No agent starts from a bare instruction.

### 4.2 Context refresh and handoff

Before editing, re-read the relevant authority document and inspect current Git status and the files in scope. Do not rely on memory or on a summary alone. Every handoff uses this template:

```text
Task:
Tracker item and status:
Spec references:
Changed files:
Behavior or documentation change:
Commands run and actual results:
Tests not run and reason:
Security/privacy impact:
Documentation updated:
Remaining questions and next steps:
```

### 4.3 Parallelization

- Freeze the API contract before frontend and backend work run in parallel.
- Sequence migrations, auth/ownership changes, and money-math changes as backend-first, then security review, then frontend.
- Keep small bug fixes and documentation changes with a single owner to avoid churn.
- Never let two agents edit the same file or document simultaneously without an explicit split.

### 4.4 Review and test sequence

1. The author runs the narrowest relevant tests and reports actual results.
2. QA independently runs the required suites.
3. The Security & Reliability Reviewer approves high-risk changes.
4. The Coordinator confirms the tracker item and its evidence.
5. The Documentation & Context Manager updates status and documentation last.

### 4.5 Conflict resolution

Apply the precedence in Section 2. If the specification is silent, record the open question and escalate to the human owner rather than resolving it by assumption. Record the resulting decision in `PROGRESS.md` only after approval.

### 4.6 Status and decisions

`PROGRESS.md` holds current status and dated durable evidence. The public Projects board holds live task status, acceptance criteria, and per-task evidence. `TASKS.md` is historical and must not be presented as current. Handoff notes are transient per task; durable decisions are summarized after approval. Do not create parallel sources of truth.

### 4.7 Human approval gates

Explicit human approval is required before:

- Creating or changing Git state: commits, pushes, branch changes, or pull requests.
- Deploying or changing provider settings, secrets, or external services.
- Running migrations, changing schema, or mutating any database.
- Destructive actions such as deleting files, force-pushing, or resetting work.
- Adding dependencies or changing CI permissions.
- Changing `PROJECT_SPEC.md` scope or security requirements.
- Creating a new GitHub Project, deleting or archiving project items, or creating GitHub Issues, labels, or milestones. Routine status and evidence updates on the approved public tracker are authorized.

### 4.8 Workflow diagram

```text
Human owner
   │ scope + approval gates
   ▼
Coordinator ── checks PROJECT_SPEC.md, issues packet
   │
   ├───────────────┬───────────────┐
   ▼               ▼               ▼
Frontend        Backend          QA
Engineer        Engineer         Engineer
   └───────┬───────┘               │
           ▼                       ▼
   Security & Reliability Reviewer
           │ approve / block
           ▼
   Documentation & Context Manager
           │ update docs + status
           ▼
   Coordinator → human review → commit/deploy approval
```

## 5. Development instructions

Run commands from the repository root unless a command changes directory.

| Task | Verified command | Notes |
|---|---|---|
| Frontend tests | `cd frontend && npm run test:run` | Vitest with React Testing Library and jsdom |
| Frontend build/typecheck | `cd frontend && npm run build` | `tsc -b && vite build` |
| Backend tests | `cd backend && source .venv/bin/activate && pytest` | PostgreSQL tests skip when the test database is not configured |
| End-to-end tests | `cd frontend && npm run test:e2e` | Requires `npx playwright install chromium`, `backend/.venv`, and `FINPATH_TEST_*` settings in `backend/.env` |

Playwright starts the backend against the dedicated test database through `frontend/e2e/start-backend.sh`, then starts the Vite dev server. Tests run serially on Chromium. The launcher substitutes the test URLs and does not run migrations or destructive cleanup.

Lint and format scripts are not currently configured in `frontend/package.json` or `backend/pyproject.toml`. Do not invent or claim lint/format commands. Report actual command output and never claim an unrun test passed.

## 6. Safety and security

- Never read, print, copy, or commit `.env` files or secrets. Do not document credentials, connection strings, or real financial data.
- Preserve authentication, CSRF protection, owner-scoped authorization, ETag/`If-Match` concurrency, idempotency, and financial-integrity controls.
- Do not access, mutate, or migrate production data. Migrations for any environment require explicit approval.
- Do not deploy or change provider settings, secrets, or external services without explicit approval.
- Inspect existing Git changes before editing and preserve user work. Never overwrite or delete unrelated changes.
- Do not weaken tests, assertions, validation, or security controls merely to make checks pass.

## 7. Documentation and completion

| Change | Update |
|---|---|
| Product behavior or scope | `PROJECT_SPEC.md`, then the relevant README |
| API contract, error, ETag, or idempotency behavior | `API_CONTRACTS.md` and the backend README |
| Schema, privileges, or migration design | `BACKEND_ARCHITECTURE.md` and the backend README |
| Frontend setup or UI behavior | `frontend/README.md` |
| Test or deployment procedure | The relevant README |
| Task status or verified evidence | `PROGRESS.md` |

End every task with a completion report containing:

1. Task and specification references.
2. Changed files.
3. Behavior or documentation changes.
4. Commands run and actual results.
5. Tests not run and the reason.
6. Security and privacy impact.
7. Documentation updated.
8. Tracker item and final status.
9. Remaining questions and next steps.

## 8. Nested instructions

This root file applies repository-wide. Do not add nested instruction files unless a subdirectory develops genuinely distinct rules; if one is added, it must be short, link back here, and never contradict `PROJECT_SPEC.md` or this file.
