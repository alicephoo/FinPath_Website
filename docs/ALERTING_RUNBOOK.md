# FinPath Alerting Runbook

> **Status: active.** `.github/workflows/alerting.yml` is merged to the default
> branch, registered, and active. It has completed **three scheduled runs
> successfully**, all healthy, so no failed alert has been observed yet. The
> workflow also supports manual `workflow_dispatch`. See
> [Execution evidence](#execution-evidence).

## Overview

A small, dependency-free runner sends four synthetic probes to the production
FinPath deployment every hour. A pure evaluator turns the probe results into a
single decision; any required-probe failure raises an alert, which is delivered
as a failed GitHub Actions workflow run. There are no dashboards, metrics
scrapes, or third-party notification services. A separate deterministic on-call
agent can read the failed-run summary and publish a diagnosis; it never changes
production code or deploys. See [ONCALL_RUNBOOK.md](ONCALL_RUNBOOK.md).

- Evaluator: `backend/app/core/alerting.py`
- Runner: `backend/app/core/alerting_runner.py`
- Workflow: `.github/workflows/alerting.yml`
- Tests: `backend/tests/test_alerting.py`, `backend/tests/test_alerting_runner.py`
- On-call agent: `backend/app/core/oncall_agent.py`, `.github/workflows/oncall-agent.yml`, [ONCALL_RUNBOOK.md](ONCALL_RUNBOOK.md)

## Monitored probes

| Probe | Request | Pass condition | Severity on failure |
|---|---|---|---|
| `frontend` | `GET https://finpath-website.pages.dev/` | HTTP 200, `Content-Type: text/html`, body contains `id="root"` | Critical |
| `backend_health` | `GET https://finpath-website.onrender.com/health` | HTTP 200, JSON `{"status":"ok"}` | Critical |
| `backend_ready` | `GET https://finpath-website.onrender.com/ready` | HTTP 200, JSON `{"status":"ready"}`; a 503 or unavailable database is a High readiness failure | High |
| `auth_smoke` | `POST https://finpath-website.pages.dev/api/v1/auth/login` through the Pages proxy | HTTP 401 and JSON `error.code == "authentication_failed"` | High |

The authentication probe sends `Origin: https://finpath-website.pages.dev`, a
reserved `alerting-probe@finpath.invalid` address, and a dummy password. It never
uses a real account, and it never logs the request body, email, password, cookies,
or arbitrary response content. The probe assumes the login failure is
non-mutating, as the CI invalid-login smoke test does.

## Severity classification

- **Critical** — frontend availability or backend liveness failed.
- **High** — database readiness or authentication smoke failed.
- **Medium** — monitoring or configuration faults: missing or duplicate probe
  results, an infrastructure error kind, an authentication `403` Origin
  rejection or `422` validation response (a probe/configuration fault, not an
  authentication outage), or an exhausted execution budget.
- **None** — every required probe passed.

When several probes fail, the highest severity wins. Optional probes do not
exist in this runner; `metrics_scrape` is not probed and production metrics
remain disabled.

## Retry policy and execution budget

- Each probe is attempted at most **twice**: one retry after **5 seconds**.
- Retries apply to timeouts, transport errors, 5xx responses, and
  malformed/unusable responses. Definitive 4xx responses are **not** retried.
- A successful retry is reported as success; it does not raise an alert. The
  result records `attempts = 2`.
- Connect/total timeouts: frontend **10 s / 20 s**; backend health, readiness,
  and authentication **10 s / 60 s**.
- The whole run targets a **90-second** budget. If that budget is exhausted,
  the remaining probes report `monitoring_error` at **Medium** rather than an
  application outage. This avoids false outage claims but can under-report
  severity during severe slowness; see [Known limitations](#known-limitations).

## Workflow schedule and triggers

- **Schedule:** hourly at `0 * * * *` (UTC), on the hour.
- **Manual:** `workflow_dispatch` for a deliberate run.
- **Not triggered** by pushes or pull requests.
- **Permissions:** `contents: read` only. No secrets, environment variables, or
  write permissions.
- **Timeout:** 3 minutes for the job.
- Concurrency groups runs so a manual dispatch cannot overlap a scheduled run.
- Runs `python -m app.core.alerting_runner` from the `backend` directory on
  Python 3.12 (the project supports 3.11–3.12). No dependencies are installed.

## Execution evidence

`FinPath production alerting` (workflow id `418855193`, state `active`) has completed three scheduled runs, all successful and healthy. **No failed alert has been observed yet**, so alert delivery and the investigation path are unexercised.

| Run ID | Event | Conclusion | Date (UTC) |
|---|---|---|---|
| [`43710614668`](https://github.com/alicephoo/FinPath_Website/actions/runs/43710614668) | schedule | success | 2026-11-15T11:47:28Z |
| [`43745912820`](https://github.com/alicephoo/FinPath_Website/actions/runs/43745912820) | schedule | success | 2026-11-15T18:17:27Z |
| [`43773993903`](https://github.com/alicephoo/FinPath_Website/actions/runs/43773993903) | schedule | success | 2026-11-15T23:17:19Z |

All three runs executed against the default branch. A successful run means the runner exited `0` (all required probes passed). No `workflow_dispatch` run has been executed. Each run's authentication probe makes one invalid-login request that produces a single expected `auth_login_failed` warning log entry in production; it does not create a session or write to the database.

## Exit codes and interpreting a failed run

| Exit code | Meaning | Workflow result |
|---|---|---|
| `0` | All required probes passed (`healthy`, severity `none`) | Green run |
| `1` | At least one probe failed (`alert`) | Red run — the alert |
| `2` | Unexpected runner-level failure | Red run — an operations problem, not necessarily an outage |

A red **scheduled** run is the alert. GitHub Actions' native failed-workflow
notification is the delivery mechanism; there are no GitHub Issues, emails, or
third-party services.

To interpret a red run, open the **Run FinPath production probes** step and read
the concise summary:

- `status` — `healthy` or `alert`
- `severity` — `none`, `medium`, `high`, or `critical`
- `failed_probes` — one or more of `frontend`, `backend_health`,
  `backend_ready`, `auth_smoke`
- per-probe `error_kind` values:
  `timeout`, `connection_error`, `status_mismatch`, `body_mismatch`,
  `malformed_body`, `proxy_error`, `configuration_error`, `monitoring_error`
- `summary` — built only from allowlisted probe names and error kinds

An exit code `2` prints only a fixed internal-error line. Investigate the
workflow environment or runner code; no sensitive details are printed.

## Safe investigation steps

1. Note the probe name(s), `error_kind`, and the run timestamp from the workflow
   summary.
2. Classify the fault:
   - `configuration_error` on `auth_smoke` — check the trusted `Origin` and probe
     configuration, not user authentication.
   - `proxy_error` — check the Cloudflare Pages Function proxy and its upstream.
   - `timeout` / `connection_error` — check provider status (Cloudflare Pages,
     Render, Neon) and network egress.
   - `status_mismatch` / `body_mismatch` / `malformed_body` — check the deployed
     version and the expected response contract.
3. Where a request ID is available, the runner copies the backend's
   `X-Request-ID` response header (validated and bounded). Use it to correlate
   with the backend's structured request logs. It is diagnostic metadata, not a
   secret or an identity.
4. Prefer probe names, error kinds, and request IDs when discussing an incident.
   Do **not** paste credentials, cookies, authorization headers, request or
   response bodies, connection strings, or other personal data into issues,
   chat, or tickets.
5. Do not attempt automatic remediation, deployment, or database changes in
   response to an alert. The deterministic on-call agent may diagnose and, only
   for an isolated fixture, propose a patch for human review; it never changes
   production code. Remediate manually through the normal release process. See
   [ONCALL_RUNBOOK.md](ONCALL_RUNBOOK.md).

## Known limitations

- **Medium — global-budget exhaustion can under-report severity.** If the
  90-second budget runs out, unfinished probes become `monitoring_error`
  (Medium) rather than `critical`/`high`. A severe slowdown could therefore look
  less severe than a hard outage.
- **Low — transport coverage.** The raw HTTPS transport is exercised through
  injected transports in tests; there is no live socket test.
- **Low — timeout model.** Timeouts are per-socket-operation deadlines derived
  from the total budget, not an OS-level wall-clock alarm.
- **Low — `proxy_error` is High.** Only `configuration_error` and
  `monitoring_error` are capped at Medium, so a proxy failure on `auth_smoke`
  alerts High.
- **Low — sparse diagnostics.** Probes attach only an allowlisted `endpoint`
  diagnostic.
- **Notification dependency.** Alert delivery requires GitHub Actions to be
  enabled and the owner's failed-workflow notifications to be configured.

## On-call agent

A separate deterministic workflow, `.github/workflows/oncall-agent.yml`, triggers
when this alerting workflow completes with `failure`. It reads only the safe
alert summary, publishes a diagnosis, and — only for an isolated fixture —
proposes and tests a patch. It is read-only and never changes production code.
Run a manual simulation from **Actions → FinPath on-call agent → Run workflow**
and choose a scenario. See [ONCALL_RUNBOOK.md](ONCALL_RUNBOOK.md) for
classification rules, the repair allowlist, artifacts, and security
restrictions.

## Activation status

Scheduled monitoring is **active**:

1. `.github/workflows/alerting.yml` is merged to the default branch (`main`). ✅
2. GitHub Actions is enabled for the repository. ✅
3. Three scheduled runs have completed successfully, confirming the probes, the
   credentials-free auth smoke, and the runner work in the Actions environment. ✅
4. The owner must still confirm that failed-workflow notifications are received.
   No failed alert has been observed, so this has not been verified end to end.

A manual `workflow_dispatch` run has not been executed; it remains the simplest
deliberate way to exercise the workflow. GitHub may disable scheduled workflows
after roughly 60 days of repository inactivity; a manual dispatch or new commit
re-enables them. Schedules are best-effort and may be delayed under load.
