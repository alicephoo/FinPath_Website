# FinPath On-Call Runbook

> **Status: implemented; trigger observed, failure-gated diagnosis not yet
> exercised.** The deterministic agent and its manual simulation are tested
> locally. The native `workflow_run` trigger has fired **three times** (run IDs
> `43710672840`, `43745980275`, `43774052244`), but every investigation job was
> **skipped** because the corresponding alerting run succeeded. The
> failure-gated diagnosis path and the manual `workflow_dispatch` path have not
> run in GitHub Actions. See [Verification status](#verification-status).

## What this is

A **deterministic, bounded On-Call Agent**, not an LLM. It consumes the safe
alert summary from a failed production alerting run, applies an explicit
rulebook, and publishes a diagnosis. For isolated fixture scenarios it can also
generate, apply, and test a small patch. It never modifies application code,
production configuration, databases, or deployments.

- Agent: `backend/app/core/oncall_agent.py`
- Tests: `backend/tests/test_oncall_agent.py`
- Workflow: `.github/workflows/oncall-agent.yml`
- Fixtures: `backend/tests/fixtures/oncall/`

## Trigger behavior

- **Automatic:** `workflow_run` with `workflows: ["FinPath production alerting"]`
  and `types: [completed]`. The job starts only when the triggering run's
  conclusion is `failure`.
- **Manual:** `workflow_dispatch` with a bounded scenario choice. Manual runs do
  not send production probes.
- **Permissions:** `contents: read` and `actions: read` only. No secrets, write
  tokens, Issues, comments, branches, pull requests, commits, or deployments.
- **Timeout:** 10 minutes. Concurrency is grouped by the triggering run.

The workflow does **not** check out the failed run's commit or any pull-request
ref. It checks out the default branch and reads only the completed run's log.

## Investigation process

1. Retrieve the failed run log with `gh run view <run-id> --log` using
   `actions: read`.
2. Parse only the allowlisted summary lines produced by
   `backend/app/core/alerting_runner.py`:
   - `status: alert`
   - `severity: ...`
   - `failed_probes: ...`
   - `- <probe>: <error_kind>`
3. Reject malformed, unknown, or incomplete summaries. Never execute log
   content. Never reproduce raw logs in the report or artifacts.
4. Apply the classification rulebook and write `oncall-report.md` and
   `oncall-diagnosis.json`.

## Classification rules

| Probe / error kind | Category | Action |
|---|---|---|
| `frontend` / `body_mismatch` | `frontend_marker_contract` | Diagnose and escalate in production; fixture-only repair in simulation |
| `frontend` / `status_mismatch` | `frontend_status` | Escalate |
| `frontend` / `timeout`, `connection_error` | `provider_outage` | Escalate |
| `frontend` / `malformed_body` | `frontend_response` | Escalate |
| `frontend` / `proxy_error` | `proxy` | Escalate |
| `backend_health` / any | `backend_liveness` | Escalate |
| `backend_ready` / `status_mismatch` | `database_or_provider` | Escalate (503 / unavailable DB) |
| `backend_ready` / `timeout`, `connection_error` | `provider_outage` | Escalate |
| `backend_ready` / `body_mismatch`, `malformed_body` | `readiness_contract` | Escalate |
| `auth_smoke` / `configuration_error` | `auth_origin_configuration` | Escalate; check Origin/proxy, not auth |
| `auth_smoke` / `proxy_error` | `proxy` | Escalate |
| `auth_smoke` / `timeout`, `connection_error` | `provider_outage` | Escalate |
| `auth_smoke` / `status_mismatch`, `body_mismatch`, `malformed_body` | `auth_contract` | Escalate |
| `configuration_error`, `monitoring_error` | `monitoring` | Escalate as a monitoring/configuration fault |
| Anything unclassified | `unknown` | Escalate |

When several probes fail, the highest effective severity (with infrastructure
error kinds capped at Medium) selects the diagnosis. Production paths always
escalate; no application repair is attempted.

## Repair allowlist

Only these paths may be patched:

- `tests/fixtures/oncall/*.html`
- `tests/fixtures/oncall/*.json`

Absolute paths, `..`, hidden paths, and any path containing `secret`,
`credential`, `.env`, `id_rsa`, `private`, `app`, `.github`, `migrations`, or
`frontend` are rejected. Authentication, financial logic, migrations,
CI/workflows, secrets, production configuration, and application business logic
are never patched.

## Escalation conditions

Escalate, and do not propose a code fix, for:

- Provider timeouts, connection failures, and proxy failures.
- Backend readiness failures (503 / database unavailable).
- Backend liveness failures.
- Authentication Origin/validation configuration faults.
- Missing/duplicate/monitoring probe data.
- Any unknown probe/error combination.
- Any repair that fails its focused verification test.

## Verification process

For a controlled repair scenario, the agent:

1. Reads the intentionally broken fixture.
2. Generates a unified diff to the fixed content.
3. Applies the fix only inside an ephemeral work directory.
4. Runs a fixed focused test:
   `python -m pytest --noconftest tests/test_oncall_agent.py -q -k <test>`.
5. Reports `repair_verified` only when the test passes. A failed or skipped test
   is reported as `repair_unverified` and the run exits non-zero.

The fixed verification command is chosen by the agent, never by alert data.

## Artifacts and report review

The workflow uploads `oncall-artifacts` (14-day retention) containing:

- `oncall-report.md` — human-readable diagnosis and evidence.
- `oncall-diagnosis.json` — structured, privacy-safe diagnosis.
- `oncall-proposed.patch` — only for a repair scenario.

The job summary repeats the report. Review the category, action, evidence, and
verification result before taking any action. A patch is a proposal for human
review; it is never merged automatically.

## Manual simulation

From a checked-out repository:

```bash
cd backend
python -m app.core.oncall_agent simulate \
  --scenario frontend_marker_fixture \
  --output-dir "$RUNNER_TEMP/oncall-artifacts" \
  --work-dir "$RUNNER_TEMP/oncall-repair"
```

Available scenarios: `frontend_marker_fixture`, `auth_origin_fixture`,
`backend_ready_outage`, `unknown_failure`. Or use **Actions → FinPath on-call
agent → Run workflow** and choose a scenario; this GitHub Actions manual
dispatch path has not been executed yet — the scenarios above have only been run
through the local CLI.

Diagnose a saved failed-run log:

```bash
cd backend
python -m app.core.oncall_agent diagnose \
  --log-file /path/to/run.log \
  --output-dir /path/to/artifacts \
  --run-id <run-id>
```

## Security restrictions

- Read-only repository and Actions access; no secrets or environment variables.
- No production database, provider, deployment, or configuration changes.
- No Issues, comments, branches, pull requests, commits, or merges.
- Logs are treated as untrusted; no log content is executed or reproduced.
- The agent does not execute model-generated instructions because it uses no
  model.
- `workflow_run` can access more privilege than the triggering workflow; this
  workflow deliberately keeps `contents: read` and `actions: read` only.

## Known limitations

- **Deterministic, not an LLM.** It cannot reason about novel failures. Unknown
  cases escalate.
- **Diagnosis first.** Production application code is outside the repair
  allowlist; only isolated fixtures can be repaired in the demonstration.
- **Log parsing is coupled to the runner's summary format.** If the runner's
  output changes, update the parser and tests together.
- **Failure-gated diagnosis is not yet observed.** The native `workflow_run`
  trigger has fired three times, but each investigation was skipped because the
  alerting run succeeded; a real failure-triggered run is still required for
  full verification.
- **Actions must be enabled** and the workflow must exist on the default branch
  for `workflow_run` to fire.
- **The agent does not notify anyone.** It publishes an artifact and job
  summary; the failed alerting run's native notification remains the alert.

## What the agent cannot do

- Modify React, FastAPI, authentication, financial logic, migrations, CI,
  workflows, secrets, or production configuration.
- Access production databases or deploy anything.
- Create Issues, comments, branches, pull requests, or commits.
- Prove a repair without a passing focused test.
- Replace human review or an on-call rotation.

## Verification status

- **Verified by local CLI simulation:** `frontend_marker_fixture` and
  `auth_origin_fixture` propose a patch, apply it to an ephemeral directory, and
  pass the focused test. `backend_ready_outage` escalates without a patch.
  `diagnose` produces a safe report from a sample failed-run log.
- **Trigger observed, investigation skipped:** the native `workflow_run` trigger
  has fired three times, and each investigation job was skipped because the
  triggering alerting run succeeded:

  | Run ID | Event | Result | Date (UTC) |
  |---|---|---|---|
  | `43710672840` | workflow_run | skipped | 2026-11-15T11:48:12Z |
  | `43745980275` | workflow_run | skipped | 2026-11-15T18:18:12Z |
  | `43774052244` | workflow_run | skipped | 2026-11-15T23:17:59Z |

- **Not yet exercised in GitHub Actions:** the failure-gated diagnosis path
  (`gh run view --log` → `oncall_agent diagnose`) and the manual
  `workflow_dispatch` simulation path. Both have only been exercised through the
  local CLI. Do not claim that the diagnosis path has run successfully in GitHub
  Actions.
- **Remaining to verify:** run a manual `workflow_dispatch` scenario, or observe
  a genuine failed alerting run, to exercise the investigation end to end. Do
  not create an artificial production failure to force this.
