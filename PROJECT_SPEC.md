# FinPath Product Specification

| Field | Value |
|---|---|
| Document status | Submission-ready product specification |
| Product | FinPath personal finance and goal planning |
| Last verified | 2026-11-15 |
| Repository state | `main` at `33b3e05df7453c35c3b7642473c8f0134fcf1619` (observability C1–C3.2); deployment evidence recorded at `45527fffe2fa36e4a0c90dd4f9592aff188be1bf` |
| Production frontend | <https://finpath-website.pages.dev> |
| Production API | <https://finpath-website.onrender.com> |
| Verification evidence | Appendix B |

This document describes both the intended product behavior and the implementation that exists in the repository. Features that are not implemented are labeled **Planned** or **Deferred**; they are not presented as available functionality.

---

## 1. Overview and Problem Statement

### 1.1 Problem

Personal financial decisions depend on knowing how much cash is actually available after obligations, planned spending, and savings commitments. FinPath addresses that problem for users who track money in Myanmar Kyat (MMK) by combining income, expenses, loans, savings goals, and travel plans in one application and deriving a single available-balance figure from those records.

Without a shared record, users typically maintain disconnected spreadsheets or mental estimates. That approach makes it difficult to answer questions such as:

- How much cash do I have after this month's expenses and loan repayments?
- How much have I actually saved versus committed to savings goals?
- What will a trip cost, and how does planned spending compare with actual spending?
- Which transactions, repayments, and goals produced the current balance?

FinPath answers these questions from persisted user-owned records and presents the result in a five-tab interface.

### 1.2 Target users and personas

| Persona | Context | Primary needs |
|---|---|---|
| Salaried professional | Receives regular income in MMK and tracks monthly spending | Record income and expenses quickly, see cash flow, monitor a starting balance |
| Freelancer or small earner | Has irregular income and wants to reserve money deliberately | Track variable income, separate actual savings from planned savings |
| Borrower | Owes one or more principal-only loans | Record proceeds and repayments, see outstanding principal without double-counting expenses |
| Saver | Works toward named savings goals | Set targets, contribute, withdraw, and understand how much cash is reserved |
| Traveller | Plans trips with category budgets | Compare planned budgets with actual trip spending without corrupting general expense totals |

The product targets a single authenticated individual per account. Shared households, teams, financial advisers, and business accounting are not supported.

### 1.3 Product goals

| ID | Goal |
|---|---|
| G1 | Provide one persisted source of truth for transactions, loans, savings goals, and trips. |
| G2 | Calculate a defensible available balance using an explicit, testable formula. |
| G3 | Use whole Myanmar Kyat integers and reject fractional currency values. |
| G4 | Scope every financial record to its authenticated owner and enforce that scope on the backend. |
| G5 | Keep the Dashboard a reporting surface rather than a second record-management interface. |
| G6 | Deliver reports and a CSV export derived from persisted data, not from hard-coded examples. |
| G7 | Operate the application on a zero-cost deployment architecture (Cloudflare Pages, Render, Neon) with verified health and readiness endpoints. |

### 1.4 Non-goals and deferred scope

| Item | Status | Notes |
|---|---|---|
| Bank or financial-institution integrations | Non-goal | No account aggregation, open banking, or automatic import. |
| Interest accrual, amortization, or loan schedules | Non-goal | Loans are principal-only. |
| Multi-currency support | Non-goal | MMK only. |
| Investment, tax, or full double-entry accounting | Non-goal | Reports are personal-finance summaries, not accounting statements. |
| Admin console, role management, audit-log UI | Deferred | Admin role and audit table exist; no Admin API or UI. |
| Rate limiting, MFA, passkeys, social login | Deferred | Security hardening backlog; see Section 8.7. |
| Email address change | Deferred | No endpoint or UI. |
| Bank-grade row-level security (RLS) | Deferred | Owner scoping is enforced in services. |
| Native mobile or offline apps | Non-goal | Responsive web application only. |
| Savings corrections and daily savings plans | Deferred | Tables exist; no implemented API or UI. |
| Loan deletion | Deferred | No delete route. |
| Category creation and editing | Deferred | Eleven categories are seeded. |
| Password change and reset UI | Deferred | Backend endpoints exist; frontend screens do not expose them. |
| Frontend savings archive/restore controls | Deferred | Backend routes exist; the current UI does not expose them. |
| Frontend loan repayment-correction control | Deferred | Backend route exists; the UI shows corrected status only. |

---

## 2. Roles and Permissions

### 2.1 User (implemented)

Every registered account receives the required `user` role. An authenticated active user can:

- Manage only their own transactions, loans, loan repayments, savings goals, savings entries, trips, trip budgets, and trip spending.
- Read and replace their own financial settings (starting balance and effective date).
- Read their own Dashboard overview, cash-flow report, expense-category report, and CSV export.
- Read their own profile summary through `GET /auth/me`.
- Change their own password and verify their email through backend endpoints. The current UI exposes registration, login, and email verification; it does not expose change-password or password-reset screens.

Users cannot grant themselves the `admin` role through registration or any runtime endpoint.

### 2.2 Admin (planned and deferred)

The database seeds an `admin` role and defines an append-only `admin_audit_logs` table, but **Admin functionality is not implemented**. Specifically:

- There are no Admin API routes.
- There is no account-management, suspension, closure, reactivation, role-assignment, or audit-log browsing interface.
- Runtime Admin writes to `user_roles` (granting or removing `admin`) are deferred pending a separately approved privileged path. Registration may assign only the required `user` role through its dedicated identity.
- The first-Admin bootstrap operation is specified in `BACKEND_ARCHITECTURE.md` but is not implemented.
- Admin access would not, by design, grant automatic access to another user's private financial records.

The `admin_audit_logs` table is part of the schema and its append-only protection exists, but no Admin API reads or writes it and no Admin browsing feature exists. The backend does record internal security events, such as refresh-token reuse, in this table.

### 2.3 Ownership and authorization model

| Layer | Mechanism |
|---|---|
| Authentication | Bearer JWT access token issued after login; agent must present `Authorization: Bearer <token>`. |
| Request identity | `get_current_auth` resolves the user, session, status, timezone, and revision from the token. |
| Ownership | Service-layer SQL includes `user_id = :user_id` for every financial read and write. |
| Cross-user access | A record owned by another user is treated as not found (`404`) rather than disclosed. |
| Account state | Suspended and closed accounts are rejected by login and by authenticated access. |
| Database defense in depth | Column-level grants restrict the runtime identities; they do not replace service authorization. |
| Row-level security | Not implemented; the shared runtime identity cannot distinguish users inside PostgreSQL. |
| Frontend | Not an authorization boundary; hiding a control does not grant or deny access. |

Admin role checks are not implemented because Admin operations are deferred. No `Admin` dependency or route-level role gate exists in the running API.

---

## 3. Product Scope and Navigation

### 3.1 Five-tab navigation

The application shell exposes five top-level tabs in this order:

| Order | Tab | Panel component | Primary responsibility |
|---|---|---|---|
| 1 | Dashboard | `DashboardReports` | Cross-feature summaries, reports, CSV export, starting-balance setup |
| 2 | Transactions | `TransactionsSection` | Income and expense records, categories, filters |
| 3 | Loans | `LoansSection` | Loan records, repayments, outstanding principal, corrections |
| 4 | Savings Goals | `SavingsGoalsSection` | Goals, contributions, withdrawals, progress |
| 5 | Travel Budget | `TravelBudgetSection` | Trips, category budgets, actual trip spending, completion |

Tabs use accessible tab semantics (`tab`, `tabpanel`, `aria-selected`, `aria-controls`) and support keyboard navigation with Arrow Left/Right/Up/Down, Home, and End.

### 3.2 Feature ownership

Each feature tab owns the records it manages:

- **Transactions** owns income and expense entries and their category assignment.
- **Loans** owns loan details, repayments, and repayment corrections.
- **Savings Goals** owns goals and their contribution/withdrawal entries.
- **Travel Budget** owns trips, category budgets, and trip-owned spending entries.
- **Dashboard** does not duplicate CRUD forms. Its only data-entry control is the starting-balance and effective-date form.

Dashboard summaries are read from the same backend records managed in the feature tabs. No feature maintains a separate mock dataset or a second copy of another feature's records.

### 3.3 Dashboard responsibilities and boundaries

The Dashboard:

- Shows the calculated account balance and whether a starting balance is configured.
- Shows all-time and current-month income and expense totals.
- Shows recognized travel expense, outstanding loan principal, recorded loan repayments, active-loan count, actual savings balance, savings targets, and active-goal count.
- Shows six-month cash-flow and expense-by-category reports.
- Lists recent transactions.
- Allows the user to save or clear the starting balance and effective date.
- Downloads a CSV report generated from persisted user data.

The Dashboard does **not** create, edit, or delete transactions, loans, savings goals, or trips. It also does not calculate a second balance in the browser from cached records; the balance is returned by the backend.

---

## 4. User Journeys

### 4.1 First-run setup, registration, email verification, login, and session restoration

1. A new visitor opens the application and sees the authentication screen with Login, Register, and Email verification views.
2. The user registers with an email address and a password of at least 12 characters. The client sends `Idempotency-Key`; registration atomically creates the user, the required `user` role, and one financial-settings row.
3. With email verification enabled (the default), registration returns `201` with `verification_status: "required"` and does **not** issue credentials or set a refresh cookie. An opaque verification token is generated, hashed for storage, and delivered by the configured email backend (outbox locally, Resend in production when configured).
4. The user submits the verification token through the Email verification screen. The token is consumed once; the account becomes verified.
5. The user signs in. Login returns a 15-minute JWT access token, a 15-minute CSRF token, and a user summary, and sets a 30-day HttpOnly refresh cookie scoped to `/api/v1/auth`.
6. On later visits, the frontend restores the session by requesting `/auth/csrf`, then calling `/auth/refresh` once, then loading `/auth/me`. If the refresh succeeds but the profile request fails, the frontend keeps the renewed session for a safe retry instead of rotating again. A recoverable "Session restore paused" screen offers a safe retry.
7. When verification is explicitly disabled for local development, registration creates an already-verified account and reports `verification_status: "not_required"`. Production configuration rejects an insecure verification bypass unless unverified registration is explicitly allowed.

**Result:** The user reaches an authenticated shell with the five tabs and sees the Dashboard.

### 4.2 Starting-balance setup

1. The user opens Dashboard. If no balance is configured, the form asks for a starting balance and effective date.
2. The user enters a whole-Kyat amount (negative values are permitted) and a valid `YYYY-MM-DD` date, then saves.
3. The frontend sends `PUT /users/me/financial-settings` with the current ETag in `If-Match`.
4. The backend validates the paired amount/date, stores both values, and returns the updated resource and a new ETag.
5. The Dashboard reloads the overview, showing the recalculated balance and the note that the starting balance is effective at the start of the chosen date.
6. The user may later clear both values together; the balance then reports `not_configured`.

If the settings changed elsewhere, the server returns `412`; the frontend reloads the latest settings and preserves the user's entry so it can be saved again.

### 4.3 Transaction creation, editing, deletion, and filtering

1. The user opens Transactions and selects an income or expense category from the seeded list.
2. The user enters a description, a whole-Kyat amount no greater than 1,000,000,000 MMK, and a local-calendar date.
3. The client sends `POST /transactions` with an `Idempotency-Key`; the backend creates the record and returns it with an ETag.
4. The user edits a transaction by selecting it, changing fields, and saving. The request includes the record's ETag in `If-Match`; a stale revision returns `412` and the UI reloads the record.
5. The user deletes a transaction after confirmation. The request includes `If-Match` and `Idempotency-Key`; deletion is permanent and removes the record from reported totals.
6. The user filters by type, category, date range, or a text query. The list returns up to 100 records (default 50) with an opaque cursor for the next page.

**Result:** The backend persists the change; the transaction list, Dashboard totals, and balance reflect it on the next read.

### 4.4 Loan creation, repayment, and correction

1. The user creates a loan with a name, whole-Kyat principal, and start date.
2. The loan appears with total repaid and outstanding principal (`principal − unreversed repayments`).
3. The user records a repayment with an amount, date, and optional note. The backend rejects a repayment that exceeds outstanding principal.
4. Through the API, a correction fully reverses the repayment exactly once; the repayment remains in history marked corrected, and its amount is excluded from repaid totals. The current Loans UI displays the corrected status but does not expose a correction action.
5. The user can edit the loan name, principal, or start date, but cannot reduce principal below repayments already recorded.

**Result:** Outstanding principal and the account balance reflect the corrected repayment set. Loan principal is a liability, not an expense; repayments are cash movements, not transaction expenses.

### 4.5 Savings goal creation, contributions, withdrawals, archive, and restore

1. The user creates a goal with a name, whole-Kyat target, and optional tone.
2. The user records a contribution with an amount, date, and optional note.
3. The user records a withdrawal. Withdrawals cannot exceed the goal's actual savings balance and are rejected for archived goals.
4. Actual savings equal contributions minus withdrawals. The goal's active allocation reserves `max(target, contributions) − withdrawals` from available cash.
5. The user can edit a goal's name, target, or tone. Reducing the target releases only the un-contributed remainder; it cannot release cash already recorded as actual savings.
6. **Archive:** the backend can archive a goal, releasing the un-contributed remainder while keeping actual savings reserved. **Restore:** the backend can restore an archived goal, reserving that remainder again.
7. **Current UI gap:** the Savings Goals interface exposes goal creation/editing, contributions, and withdrawals, but does not expose archive or restore controls. The API supports both operations.

**Result:** Actual savings, the reserved allocation, and available cash remain consistent, including across archive/restore operations performed through the API.

### 4.6 Travel creation, budgeting, spending, and completion

1. The user creates a trip with a name, destination, start date, and end date. The return date cannot precede departure.
2. The user adds category budgets, each a planned allocation rather than an expense.
3. The user records actual trip spending with a description, category, amount, date, and optional same-trip budget reference.
4. The user may import trips (up to 100 per request) with up to 500 budgets and 500 spending entries per trip. The import is atomic and skips exact duplicates.
5. An active trip reserves the greater of its planned budget total and its actual spending.
6. The user completes the trip. A completed trip recognizes actual spending only, releasing unused budget.
7. "Delete trip" is implemented as a cancellation: unused budget is released, but real spending history remains recorded and continues to count as recognized travel expense.

**Result:** Travel spending remains separate from general transactions, but the recognized travel amount is deducted from available cash exactly once.

### 4.7 Reports and CSV export

1. The Dashboard requests the overview, six-month cash-flow report, and expense-by-category report.
2. The default report window is the six months ending on the current date: the first day of the month five months earlier through today.
3. The user selects **Export CSV**. The client downloads the file from `GET /api/v1/reports/export.csv`.
4. The CSV is generated on demand from the authenticated user's persisted records.

**Result:** The downloaded file contains the current user's summary, cash-flow, category, transaction, loan, savings, and travel sections.

### 4.8 Sign-out

1. The user selects Sign out.
2. The client calls `POST /auth/logout` with the CSRF token and refresh cookie.
3. The backend revokes the current session, clears the refresh cookie, and returns `204`.
4. The client discards the access token and returns to the authentication screen.

---

## 5. Functional Requirements

### 5.1 Dashboard

| ID | Requirement |
|---|---|
| FR-D-1 | The Dashboard shall display the backend-calculated account balance, or a not-configured state when no starting balance exists. |
| FR-D-2 | The Dashboard shall display all-time income, all-time transaction expenses, recognized travel expense, and total expenses (transaction expenses plus recognized travel). |
| FR-D-3 | The Dashboard shall display current-month income and transaction expenses. |
| FR-D-4 | The Dashboard shall display outstanding loan principal, active-loan count, and recorded principal repayments. |
| FR-D-5 | The Dashboard shall display actual savings balance, savings target total, active-goal count, and per-goal progress. |
| FR-D-6 | The Dashboard shall display a six-month cash-flow report and an expense-by-category report. |
| FR-D-7 | The Dashboard shall list recent transactions with a configurable limit (1–50; default 5). |
| FR-D-8 | The Dashboard shall allow the user to set, replace, or clear the starting balance and effective date using ETag-protected writes. |
| FR-D-9 | The Dashboard shall offer CSV export of persisted user records. |
| FR-D-10 | The Dashboard shall not provide CRUD forms for transactions, loans, savings goals, or trips. |

### 5.2 Transactions

| ID | Requirement |
|---|---|
| FR-T-1 | The user shall list, create, read, update, and delete their own income and expense transactions. |
| FR-T-2 | Each transaction shall have a category whose type matches the transaction type. |
| FR-T-3 | Descriptions shall be trimmed, non-empty, and limited to 80 characters. |
| FR-T-4 | Amounts shall be positive whole-Kyat integers no greater than 1,000,000,000. |
| FR-T-5 | Dates shall be real `YYYY-MM-DD` calendar dates; timestamps shall be rejected. |
| FR-T-6 | The list shall filter by type, category, inclusive date range, and text query. |
| FR-T-7 | The list shall paginate with an opaque cursor and a maximum page size of 100. |
| FR-T-8 | Updates and deletes shall require a matching `If-Match` revision. |
| FR-T-9 | Creates and deletes shall require a valid `Idempotency-Key`; updates shall use `If-Match`. |

### 5.3 Loans

| ID | Requirement |
|---|---|
| FR-L-1 | The user shall create, list, read, and update their own loans. |
| FR-L-2 | Loans shall store a name (70 characters or fewer), whole-Kyat principal, and start date. |
| FR-L-3 | Loan principal and each repayment shall be at most 1,000,000,000 MMK. |
| FR-L-4 | A repayment shall not exceed the loan's outstanding principal. |
| FR-L-5 | Outstanding principal shall equal principal minus unreversed repayments. |
| FR-L-6 | The API shall fully reverse one repayment with a single append-only correction and preserve the original record. |
| FR-L-7 | Loan principal shall be treated as a liability, not an expense. |
| FR-L-8 | Loan deletion shall not be offered. |
| FR-L-9 | The current UI shall display corrected repayments but may omit a correction action until it is added. |

### 5.4 Savings goals

| ID | Requirement |
|---|---|
| FR-S-1 | The user shall create, list, read, and update their own savings goals. |
| FR-S-2 | Goals shall have a name (80 characters or fewer), whole-Kyat target, optional tone (16 characters or fewer), and active/archived status. |
| FR-S-3 | Contributions and withdrawals shall be positive whole-Kyat integers no greater than 1,000,000,000. |
| FR-S-4 | A withdrawal shall not exceed the goal's actual savings balance and shall be rejected for archived goals. |
| FR-S-5 | Actual savings shall equal contributions minus withdrawals. |
| FR-S-6 | The API shall support archiving and restoring goals with correct allocation release and re-reservation. |
| FR-S-7 | Savings entries shall be append-only; no update or delete route is provided. |
| FR-S-8 | The current UI shall expose goal create/edit, contribution, and withdrawal actions. Archive/restore may be API-only until the UI is extended. |

### 5.5 Travel budget

| ID | Requirement |
|---|---|
| FR-V-1 | The user shall create, list, read, update, complete, and cancel their own trips. |
| FR-V-2 | Trips shall have a name (80 characters or fewer), destination (100 characters or fewer), and valid start/end dates. |
| FR-V-3 | The user shall manage category budgets and actual spending entries scoped to a trip. |
| FR-V-4 | Budgets shall be planned allocations, not expenses. |
| FR-V-5 | Spending entries shall have a description, category, amount, date, and optional same-trip budget reference. |
| FR-V-6 | Deleting a budget shall preserve its spending by clearing the budget reference. |
| FR-V-7 | Deleting a trip shall cancel it, release unused budget, and preserve actual spending history. |
| FR-V-8 | The API shall support atomic JSON trip import with duplicate skipping and bounded item counts. |

### 5.6 Administration (deferred)

| ID | Requirement | Status |
|---|---|---|
| FR-A-1 | List users and inspect administrative profile data | Deferred |
| FR-A-2 | Suspend, reactivate, or close accounts | Deferred |
| FR-A-3 | Assign or remove the additive `admin` role | Deferred |
| FR-A-4 | Read append-only Admin audit events | Deferred |
| FR-A-5 | Bootstrap the first Admin through a controlled operation | Deferred |
| FR-A-6 | Prevent an operation from leaving zero active Admins | Deferred |

No Admin route, screen, or runtime role-management path currently exists.

---

## 6. Financial Rules

### 6.1 MMK currency and numeric validation

| Rule | Implementation |
|---|---|
| Currency | Myanmar Kyat (MMK) only. |
| Representation | Whole-Kyat integers, serialized as JSON integers with `_kyat` field names. |
| Fractional values | Rejected. Backend fields use strict integer parsing; the frontend parses `/^-?\d+$/` and rejects decimals, separators, and currency symbols. |
| Individual entry cap | 1,000,000,000 MMK for transactions, loan principal, loan repayments, savings contributions/withdrawals, and trip spending. |
| Larger planned/settings amounts | Starting balance, savings targets, and trip budgets use the safe JSON integer bound rather than the per-entry cap. |
| Missing values | Required financial fields cannot be null on create. |
| Aggregate safety | Derived totals are checked against the JSON-safe integer range and fail with `aggregate_out_of_range` if unrepresentable. |

### 6.2 Starting balance and effective date

- The starting balance is the opening balance **at the start** of the selected effective date.
- Movements dated **on or after** the effective date are included. Same-day movements count.
- Both the starting balance and effective date must be set together or cleared together.
- A starting balance may be negative.
- If no starting balance is configured, the API reports `status: "not_configured"` and no numeric balance.

### 6.3 Exact account-balance formula

The backend computes:

```text
balance =
    starting_balance
  + transaction income
  − transaction expenses
  + loan proceeds
  − unreversed loan repayments
  − recognized travel expense
  − recognized savings allocation
```

| Component | Date behavior |
|---|---|
| Starting balance | Opening balance at the start of the effective date. |
| Transaction income and expenses | Included when `transaction_date >= effective_date`. |
| Loan proceeds | Included when `loan.start_date >= effective_date`. |
| Unreversed loan repayments | Included when `repayment_date >= effective_date` and no correction exists. |
| Recognized travel expense | Current allocation state; **not** date-filtered. |
| Recognized savings allocation | Current allocation state; **not** date-filtered. |

Additional behavior:

- Corrections remove the corrected repayment from the repaid total.
- No upper date bound is applied to the balance query, so future-dated transactions and loan movements are included in the computed balance. Reports, by contrast, use explicit date bounds.
- The balance is computed inside a repeatable-read snapshot for the Dashboard overview.

### 6.4 Loan cash movements

- Loan proceeds increase available cash because borrowed principal is cash received.
- A repayment decreases available cash.
- A corrected repayment is fully reversed and excluded from repaid totals.
- Outstanding principal is a liability and is displayed separately; it is not an expense.
- Repayments are not copied into transaction expenses, so they are not double-counted.

### 6.5 Actual savings balance versus savings allocation

| Concept | Formula | Meaning |
|---|---|---|
| Actual savings | `contributions − withdrawals` | Money actually recorded inside the goal. |
| Active goal allocation | `max(target, contributions) − withdrawals` | Cash reserved from available balance for an active goal. |
| Archived goal allocation | `max(0, contributions − withdrawals)` | Only actual savings remain reserved after archiving. |
| Archive release | `max(0, target − actual)` | Un-contributed remainder returned to available cash. |

Rules:

- Setting or increasing a target reserves cash immediately.
- Contributions within the target move cash into an already-reserved bucket and do **not** deduct it a second time.
- Contributions above the target add only the excess to the reservation.
- Withdrawals return cash to the available balance.
- Reducing a target releases only the un-contributed remainder, never recorded actual savings.
- Archiving releases the un-contributed remainder while actual savings stay reserved.
- Restoring an archived goal reserves that remainder again.
- A goal's allocation cannot be negative in aggregate; archived goals use `max(0, contributions − withdrawals)`.

### 6.6 Travel expense recognition

| Trip state | Recognized expense |
|---|---|
| Active | `max(sum of category budgets, sum of actual spending)` |
| Completed | Actual spending only |
| Cancelled (including "delete trip") | Actual spending only |

Rules:

- Trip spending stays separate from general transaction expenses.
- A trip's recognized expense is counted once in total expenses and deducted once from the available balance.
- Completing or cancelling an active trip releases unused budget.
- Cancelling preserves actual spending so genuine expenses are not erased.
- Trip spending is not automatically copied into Transactions.

### 6.7 Reporting and non-double-counting rules

- All-time income and all-time transaction expenses come from transactions only.
- Total expenses equal transaction expenses plus recognized travel expense.
- Loan repayments are displayed separately and are not included in transaction expenses.
- Original loan principal is a liability, not an expense.
- Net transaction flow is income minus transaction expenses; it is not an account balance.
- Savings actual balance excludes planned daily amounts and missed commitments.
- Savings targets are shown as commitments; they are not reported as expenses.
- Travel budgets are planned allocations; only recognized travel expense enters total expenses.
- Reports and CSV are derived from persisted backend records.

---

## 7. Data and Persistence

### 7.1 Entities and ownership

Financial records are stored in PostgreSQL through the FastAPI backend. The schema contains 20 tables:

| Domain | Tables | Owner-scoped |
|---|---|---|
| Identity and access | `users`, `roles`, `user_roles`, `auth_sessions`, `auth_action_tokens`, `admin_audit_logs` | User rows are self-scoped; roles are reference data |
| Settings and idempotency | `user_financial_settings`, `idempotency_records` | Yes for settings; idempotency is principal-scoped |
| Transactions | `transaction_categories` (reference), `transactions` | Transactions yes; categories are shared |
| Loans | `loans`, `loan_repayments`, `loan_repayment_corrections` | Yes |
| Savings | `savings_goals`, `savings_entries`, `savings_plans`, `savings_plan_days` | Yes |
| Travel | `trips`, `trip_category_budgets`, `trip_spending_entries` | Yes |

Every user-owned financial table includes an owner reference (`user_id`), and service queries constrain reads and writes by the authenticated user. Reference tables such as `transaction_categories` are shared.

### 7.2 PostgreSQL persistence

- Financial records are persisted in PostgreSQL; they do not reset on refresh and are not held only in React state.
- Frontend state is a cache of API responses. Loading a tab, signing in, or refreshing triggers API reads.
- Changes are written through the backend and survive browser restarts and server restarts.
- The backend uses SQLAlchemy 2 with parameterized SQL and session-managed transactions.
- Alembic manages schema revisions.

### 7.3 Revisions and append-only records

| Record | Revision/append-only behavior |
|---|---|
| Transactions | Mutable with `revision`; updates/deletes require `If-Match`; delete is a hard delete. |
| Loans | Mutable with `revision`; no delete. |
| Loan repayments | Created once; corrections are append-only full reversals. |
| Savings goals | Mutable with `revision`; status changes via archive/restore. |
| Savings entries | Append-only; no update/delete endpoints. |
| Trips | Mutable with `revision`; delete is a status change to `cancelled`. |
| Trip budgets/spending | Mutable; deletes remove only the selected record. |
| Financial settings | Single row with `revision`; ETag-protected replacement. |
| Admin audit log | Append-only by design and database protection; no Admin API. |

### 7.4 Archive versus delete behavior

| Action | Effect |
|---|---|
| Archive savings goal | Status becomes `archived`; actual savings stay reserved; un-contributed remainder is released. |
| Restore savings goal | Status becomes `active`; the remainder is reserved again. |
| Delete transaction | Row is removed; reported totals and balance change. |
| Delete loan | Not supported. |
| Delete trip | Status becomes `cancelled`; unused budget is released; actual spending remains. |
| Delete savings goal | Not supported; archive is the lifecycle operation. |
| Delete user | No runtime path; closure is a specified terminal tombstone but has no implemented route. |

### 7.5 Local-calendar dates

- Financial dates use `YYYY-MM-DD` date-only values.
- Backend validators reject timestamps and invalid calendar dates rather than truncating them.
- Date-only values are not converted through UTC.
- The frontend constructs local calendar strings for form defaults and report ranges.
- Backend report defaults use the API server's current date (`date.today()`).

---

## 8. Authentication and Security

### 8.1 Registration and verification

- Registration requires a valid `Idempotency-Key` and creates the user, required `user` role, financial-settings row, and idempotency result atomically.
- Passwords are hashed with Argon2id (`time_cost=3`, `memory_cost=65536`, `parallelism=4`, 32-byte hash, 16-byte salt). Plain-text passwords are never stored.
- Password minimum length is 12 characters.
- Registration never returns credentials or sets a refresh cookie.
- With verification enabled, the account starts unverified; login returns `403 email_verification_required` until verification succeeds.
- Verification tokens are opaque, hashed at rest, single-use, and expire after 24 hours.
- Verification email is delivered through an outbox in local development or Resend when configured. Production with verification enabled requires the Resend backend and a configured sender.
- Password-reset request/consume endpoints exist backend-side and are not exposed in the current UI.

### 8.2 Access and refresh token lifecycle

| Item | Behavior |
|---|---|
| Access token | JWT, 15-minute lifetime, sent as `Authorization: Bearer`. |
| Refresh token | Opaque, 30-day lifetime, HttpOnly cookie scoped to `/api/v1/auth`. |
| Refresh rotation | Each successful refresh rotates the refresh token. |
| Reuse detection | Reuse of a revoked refresh token revokes the entire session family and records a `refresh_token_reuse` audit event. |
| CSRF token | 15-minute token bound to the session family. |
| Logout | Revokes the current session and clears the cookie. |
| Password change/reset | Change revokes other active sessions and keeps the current one; reset revokes active sessions. |
| Suspension/closure | Login rejects the account and revokes active sessions; authenticated requests are blocked. |

### 8.3 Cookies and CSRF protection

- The refresh cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` outside development/local/test environments.
- Cookie-authenticated operations require a valid CSRF token.
- Login, refresh, logout, and CSRF issuance enforce trusted browser origins using `Origin` and `Sec-Fetch-Site`.
- CORS allows only configured frontend origins, with credentials enabled and an explicit header allowlist.
- State-changing financial requests use bearer tokens, with idempotency keys on create/delete-style operations and `If-Match` on updates; they do not rely on cookie authentication alone.

### 8.4 Authorization and ownership

- Every financial service read/write includes the authenticated user ID.
- Cross-user record IDs return `404`, preventing existence disclosure.
- Admin does not receive automatic access to private financial records by design.
- Database grants provide defense in depth but are not a substitute for service checks.
- The frontend is never treated as an authorization boundary.

### 8.5 Validation and secrets handling

- Pydantic models use `extra="forbid"` for request bodies.
- Dates, amounts, names, descriptions, statuses, and relationships are validated server-side.
- SQL uses bound parameters; errors never return SQL, parameters, or driver details.
- API error envelopes include a request ID but no stack traces.
- Production disables `/docs`, `/redoc`, and `/openapi.json`.
- Secrets are supplied through environment configuration and are not committed. The refresh cookie is never readable by JavaScript.
- The Pages proxy deliberately avoids logging request/response bodies, cookies, authorization headers, CSRF tokens, or secrets.

### 8.6 Concurrency protection

- Mutable records carry integer revisions exposed as ETags.
- Updates and deletes require `If-Match`; missing values return `428`, stale values return `412`.
- Loan repayment and savings operations lock the parent row before validation and write.
- Financial-settings updates are ETag-protected with a revision-guarded conditional write.
- Idempotency records use PostgreSQL advisory locks and a unique scoped key.

### 8.7 Deferred security work and known gaps

| Gap | Status |
|---|---|
| Rate limiting / brute-force protection | **Not implemented**; no `429` handling or request throttling exists. |
| Multi-factor authentication, passkeys, social login | Not implemented. |
| Email address change | Not implemented. |
| Row-level security in PostgreSQL | Deferred. |
| Admin authorization and audit APIs | Deferred. |
| Account closure and deletion routes | Not implemented. |
| Password change and reset UI | Backend exists; UI absent. |
| Security penetration/load testing | Not part of the verified CI suite. |
| Token storage in browser memory | Access token is held client-side; refresh token is HttpOnly. |

---

## 9. API and Integration Contracts

### 9.1 `/api/v1` versioning

All product endpoints are under `/api/v1`, except operational `/health` and `/ready`. The frontend uses the same-origin base `/api/v1` in production, proxied by Cloudflare Pages Functions to the Render backend.

### 9.2 Implemented endpoint groups

| Group | Endpoints |
|---|---|
| Authentication | `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/csrf`, `GET /auth/me`, `POST /auth/change-password`, `POST /auth/email-verification-requests`, `POST /auth/email-verification/consume`, `POST /auth/password-reset-requests`, `POST /auth/password-reset/consume` |
| Transactions | `GET /transaction-categories`, `GET/POST /transactions`, `GET/PATCH/DELETE /transactions/{id}` |
| Loans | `GET/POST /loans`, `GET/PATCH /loans/{id}`, `GET/POST /loans/{id}/repayments`, `GET /loans/{id}/repayments/{repayment_id}`, `POST /loans/{id}/repayments/{repayment_id}/corrections` |
| Savings | `GET/POST /savings/goals`, `GET/PATCH /savings/goals/{id}`, `POST /savings/goals/{id}/archive`, `POST /savings/goals/{id}/restore`, `GET /savings/goals/{id}/entries`, `POST /savings/goals/{id}/entries/contributions`, `POST /savings/goals/{id}/entries/withdrawals` |
| Travel | `GET/POST /trips`, `POST /trips/import`, `GET/PATCH/DELETE /trips/{id}`, `POST /trips/{id}/complete`, budget CRUD under `/trips/{id}/budgets`, spending CRUD under `/trips/{id}/spending` |
| Dashboard | `GET /dashboard/overview` with optional `recent_limit` (1–50) |
| Reports | `GET /reports/cash-flow`, `GET /reports/expenses-by-category`, `GET /reports/export.csv` |
| Settings | `GET/PUT /users/me/financial-settings` |
| Operations | `GET /health`, `GET /ready`, `GET /metrics` (disabled by default; Bearer-protected when enabled) |

There are no Admin endpoints.

### 9.3 Pagination and error responses

- **Transactions pagination:** `limit` (1–100, default 50) and opaque `cursor`. Responses include `pagination: { limit, next_cursor }`. The cursor encodes the last `(transaction_date, id)` pair and is invalid if malformed.
- **Other collections:** loans, repayments, savings goals, savings entries, trips, budgets, and spending return complete owner-scoped lists without pagination.
- **Error envelope:**

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request contains invalid values.",
    "field_errors": [
      {"field": "amount_kyat", "code": "amount_limit_exceeded", "message": "..."}
    ],
    "request_id": "..."
  }
}
```

- **Status codes:** `401` authentication required, `403` forbidden/origin/CSRF/account state, `404` not found or not owned, `409` conflict/reuse/withdrawal limit, `412` stale revision, `422` validation, `428` missing `If-Match`, `503` database unavailable, `500` sanitized internal error.

### 9.4 ETags and `If-Match`

- Read responses for mutable resources include `ETag: "<revision>"`.
- `PATCH`, `PUT`, and `DELETE` on mutable resources require `If-Match`.
- Missing `If-Match` returns `428`; a mismatched revision returns `412`.
- Clients reload the latest resource and retry with the new ETag.

### 9.5 Idempotency keys and replay behavior

- Financial create and delete operations, plus other idempotent write operations, require `Idempotency-Key` (16–128 printable ASCII characters). PATCH updates use `If-Match` without an idempotency key. Change-password is protected by authentication and the current password; financial-settings writes use authentication and `If-Match`. Neither takes an idempotency key.
- Keys are scoped by principal, HTTP method, and route, so the same key can be used safely on different routes.
- A client retry with the same key and same request payload replays the stored status, body, and headers.
- Reusing a key with a different payload returns `409 idempotency_key_reused`.
- An expired record returns `409 idempotency_key_expired`.
- Concurrent use of the same key returns `409 idempotency_key_in_progress`.
- Records are retained for 24 hours.

### 9.6 CSV export

- Endpoint: `GET /api/v1/reports/export.csv`.
- Authentication: bearer token; data is scoped to the caller.
- Response: UTF-8 CSV with BOM, CRLF line endings, `text/csv`, filename `finpath-dashboard-report.csv`, and `Cache-Control: no-store`.
- Columns: `Section`, `Record`, `Date or period`, `Category`, `Amount (MMK)`, `Comparison (MMK)`, `Status or notes`.
- Sections: account summary, six-month cash flow, expense categories, all transactions, loans and repayments, savings goals and entries, trips, budgets, and spending.
- Cells that could be interpreted as spreadsheet formulas are prefixed to prevent CSV injection.

---

## 10. Architecture and Deployment

### 10.1 Frontend

| Item | Value |
|---|---|
| Framework | React 19.1 |
| Language | TypeScript 5.8 |
| Build tool | Vite 6.3 |
| Styling | Tailwind CSS 3.4 |
| HTTP | Fetch-based API client with typed resources and error handling |
| Tests | Vitest, React Testing Library, jest-dom, jsdom |
| E2E | Playwright 1.48.2 (Chromium) |

The frontend is a single-page tabbed application. Financial values are rendered from backend responses; the browser does not own the financial ledger.

### 10.2 Backend

| Item | Value |
|---|---|
| Framework | FastAPI |
| Language | Python 3.11–3.12 |
| ORM | SQLAlchemy 2 |
| Migrations | Alembic |
| Database driver | psycopg 3 |
| Auth | Argon2id, PyJWT |
| Tests | Pytest |

The backend exposes `/api/v1` routes, owner-scoped services, ETag/idempotency machinery, sanitized error handling, request IDs, and `/health` and `/ready` probes. Public API docs are disabled in production.

### 10.3 Database and migrations

- PostgreSQL 17 in CI and the documented production architecture.
- Six Alembic revisions exist: the initial schema plus registration-identity, registration-privilege, registration-token-guard, verification-grant, and trip-status revisions.
- The schema defines 20 tables and seeds two roles (`user`, `admin`) and eleven transaction categories (three income, eight expense) through a separate provisioning CLI.
- Runtime registration uses a dedicated least-privilege `finpath_registration` identity; normal application traffic uses `finpath_app`; migrations use `finpath_migrator`.
- Financial-history protection, column grants, and append-only audit protections are defined in the migrations and architecture document.

### 10.4 Cloudflare Pages proxy

- The production frontend is served by Cloudflare Pages from the `frontend` root (`npm run build`, output `dist`).
- `frontend/public/_routes.json` limits Pages Functions to `/api/*`, keeping static assets outside Function invocation.
- `frontend/functions/api/[[path]].ts` delegates to `frontend/functions/api/proxy.ts`.
- The proxy forwards to one fixed backend origin supplied by the `RENDER_BACKEND_URL` environment setting; the destination is never taken from the request, so it cannot act as an open proxy.
- It forwards method, path, query, body, and an allowlist of headers (including `Authorization`, `Cookie`, `X-CSRF-Token`, `Idempotency-Key`, `If-Match`, `If-None-Match`, `Origin`, and `Sec-Fetch-Site`).
- It drops client-supplied hop-by-hop and forwarding headers, preserves upstream status/body/content type/location/multiple `Set-Cookie`, forces `Cache-Control: no-store`, and logs no sensitive request data.
- The production client uses the same-origin `/api/v1` base; local development uses `http://localhost:8000/api/v1`.

### 10.5 Render and Neon responsibilities

| Provider | Responsibility |
|---|---|
| Cloudflare Pages | Static frontend hosting, HTTPS, same-origin `/api/*` proxy Function. |
| Render | Runs the FastAPI Docker web service, supplies `PORT`, terminates HTTPS, and exposes the application's health/readiness endpoints. |
| Neon | Documented production database: hosts PostgreSQL 17; runtime, registration, and migration roles remain separate. |

Production startup does not run migrations on every wake-up when `FINPATH_RUN_STARTUP_MIGRATIONS=false`; migrations are applied as a separate controlled operation. The entrypoint still binds `0.0.0.0` and honors Render's `PORT`.

### 10.6 Local, CI, and production environments

| Environment | Frontend | Backend | Database |
|---|---|---|---|
| Local | Vite dev server | Uvicorn/FastAPI | Local PostgreSQL or Docker Compose |
| CI | Vite test/build | Pytest against FastAPI | PostgreSQL 17 service container with temporary roles |
| Production | Cloudflare Pages | Render Docker service | Neon PostgreSQL 17 (documented architecture) |

Local Docker Compose supports migration and seeding at startup. CI creates three least-privilege roles, applies migrations, seeds reference data, and runs backend, frontend, Playwright E2E, Docker image build, and backend container staging-lite jobs. GitHub Actions only builds and tests; it does not deploy.

### 10.7 Verified deployment status

| Check | Result | Date/evidence |
|---|---|---|
| Frontend URL | Live at <https://finpath-website.pages.dev> | Confirmed live 2026-11-12; Cloudflare Pages check-run succeeded |
| Backend URL | Live at <https://finpath-website.onrender.com> | Confirmed live 2026-11-12 |
| `GET /health` | HTTP `200`, `{"status":"ok"}` | Re-probed 2026-11-13 (cold start ≈23 s) |
| `GET /ready` | HTTP `200`, `{"status":"ready"}` | Re-probed 2026-11-13 |
| CI | Green | Run `43174534132`, 2026-11-12 |
| Deployment commit | `45527fffe2fa36e4a0c90dd4f9592aff188be1bf` | `main` = `origin/main` at verification |

The frontend root could not be re-probed from the authoring network during the final documentation pass (connection reset); its live status is supported by the 2026-11-12 owner confirmation and the successful Pages deployment check-run.

### 10.8 Observability

End-to-end observability is implemented (C1–C3.2, commit `33b3e05df7453c35c3b7642473c8f0134fcf1619`). It covers request-ID correlation, structured domain events, and an in-process metrics registry with a protected scrape endpoint. It is **not** distributed tracing, and no metrics are collected in production because metrics are disabled by default.

**Request-ID correlation (C1).**

- The frontend generates a correlation ID with `crypto.randomUUID()` when the caller does not supply one; `Math.random()` is never used.
- A valid caller-supplied `X-Request-ID` (1–128 characters, first character alphanumeric, remainder limited to `A–Z`, `a–z`, `0–9`, `.`, `_`, `:`, `-`) is preserved; missing or malformed values are replaced by a generated ID.
- The Pages Function proxy forwards `X-Request-ID` to the backend and copies it back on the response.
- The backend accepts a valid ID or generates one, stores it in a request context, echoes it in the `X-Request-ID` response header, and exposes it through CORS.
- Successful frontend responses and `ApiError` instances (HTTP and network failures) expose the ID as `requestId`. It is a diagnostic correlation value only and is never used as authentication or user identity.

**Structured logging and domain events (C2).**

- One JSON log record is emitted per completed request with `timestamp`, `severity`, `logger`, `event`, `message`, `request_id`, and bounded fields (`method`, `route`, `status_code`, `duration_ms`, `slow`, `error_type`). Request/response bodies, cookies, authorization headers, query strings, and database details are never logged.
- Domain events use an allowlisted vocabulary (`DOMAIN_EVENT_NAMES`): authentication outcomes and transaction, loan, savings, travel, and report actions.
- `auth_login_failed` requires one of the fixed reasons `invalid_credentials`, `email_verification_required`, `account_suspended`, or `account_closed`; other events reject a reason. Messages are static and no user data is interpolated.
- Login failures use `invalid_credentials` for both an unknown email and a wrong password, so the log does not reveal whether an account exists.
- Write-path events are emitted after a successful database commit, and idempotent replays and archive/restore no-ops are suppressed so a retried request does not duplicate an event. `report_exported` is emitted for a successful CSV export, which is a read operation.
- The request ID is attached to domain-event records by the same logging context filter.

**Metrics registry (C3.1).**

- `backend/app/core/metrics.py` is a dependency-free, in-process registry of counters, gauges, and histograms with no database, network, or configuration dependency.
- Every metric declares its permitted label names and bounded allowed values at registration; undeclared labels, missing labels, and out-of-set values are rejected, so no user-provided or high-cardinality value can become a label.
- Mutation and rendering are lock-guarded, so the registry is safe under concurrent requests and scrapes.
- Rendering produces deterministic Prometheus text exposition (registration order, series sorted by label value) with `# HELP`/`# TYPE` lines, cumulative histogram buckets, `+Inf`, `_sum`, and `_count`.
- Counters are monotonic and non-negative; histogram bounds are strictly increasing and finite; observations are non-negative.

**HTTP metrics (C3.2).**

- A fresh `MetricsRegistry` is created per application instance, and metrics are registered after all routes so the route-label vocabulary is complete.
- Three metrics are registered:
  - `finpath_http_requests_total` — counter, labels `route`, `method`, `status`.
  - `finpath_http_request_duration_seconds` — histogram, labels `route`, `method`; default buckets in seconds: `0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0`.
  - `finpath_http_slow_requests_total` — counter, labels `route`, `method`; incremented at or above `slow_request_threshold_ms` (default `1000`).
- Labels are bounded. `route` is a fixed set of registered route templates plus `unmatched` and `other`; `method` is a fixed HTTP-method set plus `other`; `status` is a fixed set of expected status codes plus `other`. Raw paths, path parameters, query strings, user identifiers, and financial values never become labels.
- Each eligible request is recorded once with its final status and measured duration, including handled exceptions. Recording is best-effort: any failure is swallowed and logged as a static warning, so metrics cannot change a response.
- The `/metrics` scrape route is excluded from instrumentation.

**Protected `/metrics` endpoint (C3.2).**

- The route is excluded from the OpenAPI schema.
- When `metrics_enabled` is `false` (the default) the endpoint returns an empty `404`.
- When enabled, a valid `Authorization: Bearer <token>` credential returns the registry's Prometheus exposition as `text/plain; version=0.0.4; charset=utf-8`. Missing, malformed, duplicate, incorrect, or unconfigured credentials return an indistinguishable generic `401` with `WWW-Authenticate: Bearer`.
- The token is compared with `hmac.compare_digest` over UTF-8 bytes and is never authorized when unset or empty. The configured token is a masked `SecretStr` and is never logged, echoed, or included in an error response.
- The endpoint does not require browser CSRF or trusted-origin checks, and it never instruments its own scrape requests.

**Configuration and production status.**

- `metrics_enabled` defaults to `false`; `metrics_token` defaults to `None`. Enabling metrics without a token does not crash startup; the endpoint fails closed.
- No production metrics configuration has been enabled or changed, and no scrape path, dashboard, or tracing integration exists. User-impact alerting is separate from metrics and is implemented in the repository but is not yet active; see Section 10.9.
- Metrics are in-process only: counters reset on process restart and are not shared across multiple backend instances.

### 10.9 User-impact alerting

A user-impact alerting capability is implemented in the repository but is **not yet active**. Scheduled monitoring begins only after `.github/workflows/alerting.yml` is committed to the default branch and a run succeeds.

- Four synthetic production probes run hourly: frontend availability (`GET https://finpath-website.pages.dev/`, HTTP 200, `Content-Type: text/html`, body contains `id="root"`), backend liveness (`GET https://finpath-website.onrender.com/health`, JSON `{"status":"ok"}`), backend readiness (`GET https://finpath-website.onrender.com/ready`, JSON `{"status":"ready"}`), and a non-mutating invalid-login smoke test through the Pages proxy (`POST https://finpath-website.pages.dev/api/v1/auth/login`, HTTP 401 with `error.code == "authentication_failed"`).
- The authentication probe uses a reserved `alerting-probe@finpath.invalid` address, a dummy password, and `Origin: https://finpath-website.pages.dev`. It never uses a real account and never logs request bodies, emails, passwords, cookies, headers, or arbitrary response content.
- A pure evaluator (`backend/app/core/alerting.py`) classifies failures. Frontend availability or backend-liveness failure is Critical; readiness or authentication-smoke failure is High. Monitoring and configuration faults (missing or duplicate probe results, infrastructure error kinds, an authentication `403`/`422`, or an exhausted budget) are capped at Medium so they are not reported as application outages. The highest severity across failures wins.
- The runner retries once after 5 seconds for timeouts, transport errors, 5xx responses, and malformed/unusable responses, and never for definitive 4xx responses. A successful retry is reported as success. The run targets a 90-second budget; if exhausted, remaining probes report a Medium `monitoring_error`.
- The workflow runs hourly (`0 * * * *` UTC) and on manual `workflow_dispatch`, with `contents: read`, no secrets or environment variables, and a 3-minute job timeout. It has no push or pull-request triggers. A non-zero runner exit fails the workflow, and GitHub Actions' native failed-workflow notification is the delivery mechanism.
- No metrics scrape, dashboard, third-party notification service, automated remediation, or deployment step is part of this capability, and production metrics remain disabled.
- Operational details, exit codes, investigation steps, and activation requirements are in `docs/ALERTING_RUNBOOK.md`.

---

## 11. Testing and Acceptance Criteria

### 11.1 Unit and component tests

- **Frontend:** Vitest with React Testing Library and jsdom.
- **Verified result:** 316 tests passed across 19 files.
- Coverage includes money parsing/formatting, API client behavior, authentication screens and session restoration, tab navigation, each feature section, Dashboard reports, CSV download, Pages proxy behavior, and error/empty/loading states.

### 11.2 Backend, API, and PostgreSQL tests

- **Framework:** Pytest with FastAPI test clients against a real PostgreSQL 17 database.
- **Verified result:** 250 tests passed.
- Coverage includes registration/login/refresh/CSRF, account states, ownership isolation, transaction and loan rules, savings allocations and withdrawals, travel recognition and imports, dashboard calculations, financial settings, ETags, idempotency, error envelopes, database protections, request-ID correlation, the domain-event allowlist, the metrics registry, and HTTP instrumentation.

### 11.3 Playwright end-to-end tests

- **Framework:** Playwright 1.48.2, Chromium, serial execution with a real backend and test database.
- **Verified result:** 10 tests passed across 5 spec files.
- Specs cover authentication, regression flows, reports/CSV, savings withdrawals, and user isolation.

### 11.4 Verified CI test counts

| Job | Result | CI run | Date |
|---|---|---|---|
| Backend tests | 250 passed in 55.46 s | `43174534132` | 2026-11-12 |
| Frontend tests, type check, build | 316 passed (19 files); build succeeded | `43174534132` | 2026-11-12 |
| Playwright E2E (Chromium) | 10 passed in 31.4 s | `43174534132` | 2026-11-12 |

The CI run executed against commit `45527fffe2fa36e4a0c90dd4f9592aff188be1bf` on `main`.

### 11.5 Implemented acceptance criteria

| ID | Criterion | Evidence |
|---|---|---|
| AC-1 | Five accessible tabs exist in the required order. | Frontend shell tests and E2E |
| AC-2 | Dashboard owns only reports, summaries, CSV, and starting-balance settings. | Component tests; no CRUD forms in Dashboard |
| AC-3 | A user can manage only their own records. | Backend ownership tests and `user-isolation` E2E |
| AC-4 | Transactions support create/read/update/delete, category filtering, date/type/text filters, and cursor pagination. | API tests and component tests |
| AC-5 | Loans support principal, repayments, outstanding principal, and append-only corrections. | Backend tests; UI tests cover principal/repayments, not the correction action |
| AC-6 | Savings distinguish actual savings from allocation, support withdrawals, and enforce the available balance. | Backend tests, component tests, `savings-withdrawals` E2E |
| AC-7 | Travel supports trips, budgets, spending, import, completion, and cancellation with correct recognition. | Backend tests and UI tests |
| AC-8 | Account balance follows the documented formula, including effective-date inclusivity, loans, travel, and savings. | Backend calculation tests |
| AC-9 | Whole-Kyat values are enforced and fractional values are rejected. | Backend and frontend money tests |
| AC-10 | Individual entries above 1,000,000,000 MMK are rejected. | Backend validation tests |
| AC-11 | Authentication uses Argon2id, JWT access tokens, rotating refresh tokens, CSRF, and secure cookies. | Auth tests |
| AC-12 | Cross-user access is rejected and account suspension/closure blocks access. | Security/ownership tests |
| AC-13 | Records persist in PostgreSQL and survive refresh. | PostgreSQL-backed API tests and E2E |
| AC-14 | Dashboard reports and CSV derive from persisted data. | Report tests, CSV tests, `reports-csv` E2E |
| AC-15 | Health and readiness endpoints report live status. | Production probes |
| AC-16 | CI passes backend, frontend, Playwright E2E, Docker image build, and backend container staging-lite jobs. | Run `43174534132` |

### 11.6 Deferred acceptance criteria

| ID | Criterion | Status |
|---|---|---|
| AC-D-1 | Admin can list users, change status, assign roles, and read audit logs. | Deferred |
| AC-D-2 | First Admin can be bootstrapped securely with last-Admin protection. | Deferred |
| AC-D-3 | Rate limiting protects authentication and write endpoints. | Not implemented |
| AC-D-4 | Users can change their email address. | Not implemented |
| AC-D-5 | Users can change their password or complete password reset in the UI. | Backend only |
| AC-D-6 | Users can archive/restore savings goals in the UI. | Backend only |
| AC-D-7 | Savings corrections and daily savings plans work end to end. | Not implemented |
| AC-D-8 | Loan deletion is available. | Not implemented |
| AC-D-9 | Row-level security enforces ownership inside PostgreSQL. | Deferred |
| AC-D-10 | Users can apply repayment corrections in the Loans UI. | Backend only |

### 11.7 Known coverage gaps

- No automated tests for Admin features because none exist.
- No rate-limiting tests because rate limiting is absent.
- No multi-factor, passkey, social-login, or email-change tests.
- Playwright runs Chromium only; no Firefox/WebKit matrix.
- Change-password, password-reset, and email-change UI paths are not tested because they are absent.
- Email delivery is tested through the local outbox; live Resend delivery is not part of CI.
- No load, stress, or penetration test is part of CI.
- No automated test asserts Neon backup/restore or Render failure recovery.
- Savings archive/restore have backend tests but no component/E2E coverage because the UI does not expose them.
- Loan repayment corrections have backend tests but no component/E2E coverage because the UI does not expose the action.
- PostgreSQL domain-event boundary tests skip when no test database is configured, so post-commit emission, replay and no-op suppression, and failed-write absence are unverified without one (finding M1).
- The frontend has no test for a malformed caller `X-Request-ID` combined with an unavailable `crypto.randomUUID`, and no test for error-envelope `request_id` validation (findings L1 and L2).

---

## 12. Limitations, Risks, and Open Questions

### 12.1 Product limitations

- **Admin is not available.** Account management, role assignment, suspension/closure operations, and audit-log browsing cannot be performed through the product.
- **Rate limiting is absent.** Authentication and write endpoints are not throttled, leaving brute-force and abuse resistance dependent on infrastructure.
- **No RLS.** Ownership is enforced by application queries; a service-layer defect could expose another user's rows because the shared runtime identity cannot distinguish users in PostgreSQL.
- **Password-reset UI is missing.** The backend supports reset tokens, but users cannot start or complete the flow in the current interface.
- **Savings archive/restore is API-only.** The UI explicitly states that corrections, archive/restore, and daily plans are unavailable.
- **Loan repayment correction is API-only.** The UI marks corrected repayments but offers no correction action.
- **No email change, MFA, passkeys, or social login.**
- **No bank sync, interest, amortization, or multi-currency.**
- **No loan or savings-goal deletion.** Archive/cancel semantics preserve history.
- **Category management is fixed** to eleven seeded categories.
- **Only Chromium is exercised end to end.**
- **CSV is the only export format** and is limited to the Dashboard dataset.

### 12.2 Financial and data risks

- Future-dated transactions and loan movements are included in the current balance because the balance query has no upper date bound.
- Reports use the API server's current date for default ranges, while users may be in a different local timezone. The stored user timezone is not applied to report boundaries.
- The Dashboard computes balance from current travel and savings allocations, which are not date-filtered; historical point-in-time reconstruction is therefore not supported.
- A hard-deleted transaction disappears from history and changes prior totals.
- CSV formula-injection protection alters leading dangerous characters in exported cells, so the file is intended for spreadsheet use rather than byte-for-byte archival.
- Neon and Render are external dependencies; outage, cold-start latency, and provider limits affect availability.

### 12.3 Security and operational risks

- Render Free cold starts were observed at roughly 23 seconds during verification. The client retries only safe `GET` requests for CSRF/profile; refresh POSTs are deliberately not retried automatically.
- Email verification in production depends on Resend configuration and a verified sender domain.
- Secrets are environment-managed; misconfiguration can prevent startup or break authentication.
- Database migrations are a controlled operation; running them on every Render wake-up is intentionally avoided.
- No automated backup/restore verification is documented in this repository.
- Public API docs are disabled in production, which reduces exposure but also removes a live contract browser.
- The `/metrics` endpoint is disabled by default and, when enabled, requires a configured bearer token and returns a generic `401` otherwise, so it fails closed. In-process metrics reset on restart and are not aggregated across instances.
- Post-commit domain-event logging is not defensively wrapped, so a logging failure could surface as a sanitized `500` after a successful write (finding L3).

### 12.4 Open questions

1. What privileged path will implement runtime `user_roles` writes and first-Admin bootstrap?
2. Should rate limiting be introduced at the application, proxy, or provider layer?
3. Should account closure and data deletion become user-initiated, Admin-initiated, or both?
4. Should savings archive/restore and corrections be exposed in the UI, or removed from the API surface?
5. Should future-dated movements be excluded from the current balance or shown as scheduled?
6. Should reports use the user's stored timezone instead of the API server date?
7. Should travel spending ever be linked to Transactions, or remain a separate recognized layer?
8. What backup, retention, and recovery objectives apply to the Neon database?
9. Should the metrics endpoint be enabled in production, and how should it be scraped, secured, and aggregated across instances?

---

## 13. Glossary

| Term | Definition |
|---|---|
| **Access token** | Short-lived JWT bearer credential used to authenticate API requests. |
| **Actual savings** | Contributions minus withdrawals for a savings goal. |
| **Allocation (savings)** | Cash reserved from the available balance by an active savings target. |
| **Archive** | Savings-goal state that keeps actual savings reserved but releases the un-contributed remainder. |
| **CSRF token** | Token bound to a session family and required for cookie-authenticated state changes. |
| **Cursor** | Opaque pagination marker encoding the last transaction date and ID. |
| **Domain event** | A bounded, privacy-safe structured log record for an application action, for example `transaction_created`; names and reasons are allowlisted. |
| **Effective date** | Date whose start defines the opening balance; movements on or after it are included. |
| **ETag** | HTTP validator exposing a record's revision for optimistic concurrency. |
| **Idempotency-Key** | Client-supplied key that makes a state-changing request safe to retry. |
| **MMK** | Myanmar Kyat, the application's only currency, stored as whole integers. |
| **Prometheus exposition** | The `text/plain; version=0.0.4` metrics format rendered by the in-process registry. |
| **Recognized savings allocation** | Amount deducted from available cash for savings goals under the active/archived rules. |
| **Recognized travel expense** | Travel amount deducted from available cash and added to total expenses exactly once. |
| **Refresh token** | Opaque, HttpOnly cookie credential used to rotate sessions. |
| **Request ID** | A validated `X-Request-ID` correlation value shared between the frontend and backend logs for one request; not an identity. |
| **Session family** | Chain of rotated refresh tokens; reuse of a revoked token revokes the family. |
| **Starting balance** | User-supplied opening balance at the start of the effective date. |
| **Trip budget** | Planned travel allocation by category; not an expense by itself. |
| **Trip spending** | Actual trip-owned expense entry, separate from general transactions. |
| **Unreversed repayment** | Loan repayment without a correction; included in repaid totals and cash outflows. |

---

## Appendix A: Dated Implementation Status

**Status as of 2026-11-15**, repository `main` at `33b3e05df7453c35c3b7642473c8f0134fcf1619`.

| Area | Status | Notes |
|---|---|---|
| Registration, verification, login, refresh, logout | Implemented | Backend and frontend; password-reset backend exists without UI |
| Account status enforcement | Implemented | Suspension/closure checks exist; no Admin route changes status |
| Five-tab frontend shell | Implemented | Accessible tabs and keyboard navigation |
| Dashboard overview and reports | Implemented | Overview, cash flow, expenses by category |
| Starting-balance settings | Implemented | ETag-protected set/replace/clear |
| Transactions | Implemented | CRUD, categories, filters, cursor pagination, CSV inclusion |
| Loans | Partially implemented | CRUD except delete; repayments in UI; full-reversal corrections are API-only |
| Savings goals | Partially implemented | Goals, contributions, withdrawals, archive/restore API; archive/restore UI absent; corrections/daily plans absent |
| Travel budget | Implemented | Trips, budgets, spending, import, complete, cancel |
| CSV export | Implemented | `GET /api/v1/reports/export.csv` |
| PostgreSQL persistence | Implemented | 20 tables, 6 Alembic revisions, seeded roles/categories |
| Idempotency and ETags | Implemented | Scoped 24-hour records; `If-Match` protection |
| Observability (C1–C3.2) | Implemented | `X-Request-ID` correlation, allowlisted domain events, Prometheus-format metrics registry, and protected `/metrics`; metrics disabled in production |
| User-impact alerting | Implemented locally, not active | Four synthetic probes, pure evaluator, standard-library runner, and hourly workflow; activation pending default-branch merge and a successful run |
| Admin console, role management, audit UI | Deferred | No endpoints or screens |
| Rate limiting | Not implemented | Known security gap |
| Email change, MFA, passkeys, social login | Deferred | Not implemented |
| RLS | Deferred | Service-layer ownership only |
| Production deployment | Live (Pages and Render verified) | Neon follows the documented production architecture |
| CI | Green | Run `43174534132`: 250 backend, 316 frontend, 10 Playwright tests; commit `33b3e05`: all five CI jobs passed (owner confirmation) |

---

## Appendix B: Verification Evidence

### B.1 Repository state

| Check | Value |
|---|---|
| Branch | `main` |
| HEAD | `33b3e05df7453c35c3b7642473c8f0134fcf1619` (observability C1–C3.2) |
| Upstream | `origin/main` at the same commit |
| Tracked working tree | Documentation-only edits pending (root/backend/frontend READMEs and `AI_WORKFLOW.md`) |
| Untracked documentation | `AGENTS.md`, `PROGRESS.md`, `PROJECT_SPEC.md`, `TASKS.md` |

### B.2 CI evidence

- Workflow: `.github/workflows/ci.yml` (GitHub Actions).
- Run: `43174534132`.
- URL: <https://github.com/alicephoo/FinPath_Website/actions/runs/43174534132>.
- Date: 2026-11-12.
- Commit: `45527fffe2fa36e4a0c90dd4f9592aff188be1bf`.
- Jobs:
  - **Backend tests:** 250 passed in 55.46 s with PostgreSQL 17.
  - **Frontend tests, type check, and build:** 316 passed across 19 files; `tsc -b && vite build` succeeded.
  - **Playwright E2E (Chromium):** 10 passed in 31.4 s across 5 spec files.
- The workflow has read-only repository permissions and does not deploy.

### B.3 Deployment evidence

| Check | Result | Date |
|---|---|---|
| `GET https://finpath-website.onrender.com/health` | `200 {"status":"ok"}` | 2026-11-13 (re-probe); service confirmed live 2026-11-12 |
| `GET https://finpath-website.onrender.com/ready` | `200 {"status":"ready"}` | 2026-11-13 (re-probe); service confirmed live 2026-11-12 |
| `https://finpath-website.pages.dev` | Confirmed live by owner; Cloudflare Pages deployment check-run succeeded | 2026-11-12 |
| Cloudflare Workers Builds check-run | Success | 2026-11-12 |
| Render service | Live; `/health` and `/ready` respond | 2026-11-12 |

The Pages root probe was reset by the authoring network during the final pass. No deployment, provider setting, database migration, or secret was changed while producing this document.

### B.4 Source references

| Topic | Primary source |
|---|---|
| Balance formula and filters | `backend/app/services/transactions.py` |
| Savings allocation and archive/restore | `backend/app/services/savings.py` |
| Travel recognition and completion | `backend/app/services/travel.py` |
| Loans and corrections | `backend/app/services/loans.py` |
| Authentication and sessions | `backend/app/services/auth.py`, `backend/app/core/security.py` |
| Settings and entry caps | `backend/app/core/config.py`, `backend/app/services/financial_settings.py` |
| Routes and error envelope | `backend/app/api/`, `backend/app/api/errors.py`, `backend/app/main.py` |
| API contracts | `API_CONTRACTS.md` |
| Schema and deferred Admin design | `BACKEND_ARCHITECTURE.md` |
| Frontend navigation and journeys | `frontend/src/App.tsx`, `frontend/src/auth/`, `frontend/src/components/` |
| Pages proxy | `frontend/functions/api/`, `frontend/public/_routes.json` |
| CI workflow | `.github/workflows/ci.yml` |
| E2E specs | `frontend/e2e/` |

### B.5 Document scope

This specification was produced by inspecting the repository and verifying CI/deployment evidence. `README.md` and `PROGRESS.md` contain stale statements about test counts, deployment, and unimplemented features; they were not used as authority. No secrets, credentials, private user data, or sensitive environment values are included.

### B.6 Observability verification

| Check | Value |
|---|---|
| Commit | `33b3e05df7453c35c3b7642473c8f0134fcf1619` |
| Message | `feat: add end-to-end observability` |
| Pushed | `origin/main` at the same commit |
| CI | All five GitHub Actions jobs passed (confirmed by the project owner; no run ID or URL supplied) |
| Production metrics | Not enabled or configured |

Observability implementation details are documented in Section 10.8. The combined review (C1–C3.2) recorded one medium finding (M1) and four low findings (L1–L4); none are fixed and none block the observability commit.
