# FinPath Alerting Runbook

> **Status: implemented in the repository, not yet active.** Scheduled
> production alerting begins only after `.github/workflows/alerting.yml` is
> committed to the default branch and a run succeeds. Until then, treat this as
> prepared monitoring, not live coverage. See [Activation](#activation).

## Overview

A small, dependency-free runner sends four synthetic probes to the production
FinPath deployment every hour. A pure evaluator turns the probe results into a
single decision; any required-probe failure raises an alert, which is delivered
as a failed GitHub Actions workflow run. There are no dashboards, metrics
scrapes, third-party notification services, or automated remediation.

- Evaluator: `backend/app/core/alerting.py`
- Runner: `backend/app/core/alerting_runner.py`
- Workflow: `.github/workflows/alerting.yml`
- Tests: `backend/tests/test_alerting.py`, `backend/tests/test_alerting_runner.py`

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
   response to an alert. Remediate manually through the normal release process.

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

## Activation

Scheduled monitoring is **not active** until all of the following hold:

1. `.github/workflows/alerting.yml` is committed and merged to the default
   branch (`main`). Scheduled workflows run from the default branch.
2. GitHub Actions is enabled for the repository. For a private repository,
   scheduled runs consume included Actions minutes.
3. A manual `workflow_dispatch` run completes and is green, confirming the
   probes, credentials-free auth smoke, and runner work in the Actions
   environment.
4. The owner confirms that failed-workflow notifications are received.

After activation, GitHub may disable scheduled workflows after roughly 60 days
of repository inactivity; a manual dispatch or new commit re-enables them.
Schedules are best-effort and may be delayed under load.
