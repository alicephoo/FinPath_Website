"""Deterministic, bounded on-call diagnosis for FinPath.

This module is intentionally **not** an LLM agent. It consumes a privacy-safe
alert summary, applies an explicit rulebook, and produces a human-readable
diagnosis. For one isolated fixture scenario it can also generate and verify a
small patch, but only inside an ephemeral copy of an allowlisted test fixture.

Hard rules:

- Never execute alert or log content.
- Never read `.env`, secrets, credentials, cookies, or raw response bodies.
- Never patch application code, authentication, financial logic, migrations,
  CI/workflows, secrets, or production configuration.
- Never claim a repair succeeded until a focused verification test passes.

The production path is diagnosis-and-escalate. The only automatic repairs are
explicitly seeded fixture defects used to demonstrate the workflow safely.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Sequence

from app.core.alerting import (
    ERROR_KINDS,
    INFRASTRUCTURE_ERROR_KINDS,
    PROBE_AUTH_SMOKE,
    PROBE_BACKEND_HEALTH,
    PROBE_BACKEND_READY,
    PROBE_FRONTEND,
    PROBE_NAMES,
    SEVERITIES,
    STATUS_ALERT,
    STATUS_HEALTHY,
)

__all__ = [
    "SCENARIOS",
    "AlertParseError",
    "AlertSummary",
    "Diagnosis",
    "PatchError",
    "RepairResult",
    "Scenario",
    "VerificationResult",
    "apply_fix",
    "classify",
    "generate_patch",
    "is_allowed_path",
    "main",
    "parse_alert_log",
    "parse_alert_summary",
    "render_report",
    "run_diagnose",
    "run_simulation",
]

BACKEND_DIR = Path(__file__).resolve().parents[2]

MAX_LOG_CHARS = 5_000_000
MAX_VERIFICATION_OUTPUT = 1_500
MAX_VERIFICATION_SECONDS = 120

ALLOWED_PREFIX = "tests/fixtures/oncall/"
ALLOWED_SUFFIXES = (".html", ".json")

PROBE_ORDER = (
    PROBE_FRONTEND,
    PROBE_BACKEND_HEALTH,
    PROBE_BACKEND_READY,
    PROBE_AUTH_SMOKE,
)
PROBE_RANK = {
    PROBE_FRONTEND: 3,
    PROBE_BACKEND_HEALTH: 3,
    PROBE_BACKEND_READY: 2,
    PROBE_AUTH_SMOKE: 2,
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_STATUS_RE = re.compile(r"(?:^|\s)status:\s*(healthy|alert)\b", re.MULTILINE)
_SEVERITY_RE = re.compile(
    r"(?:^|\s)severity:\s*(none|medium|high|critical)\b", re.MULTILINE
)
_FAILED_RE = re.compile(
    r"(?:^|\s)failed_probes:\s*([A-Za-z0-9_, ]+)", re.MULTILINE
)
_PROBE_KIND_RE = re.compile(
    r"(?:^|\s)-\s*(frontend|backend_health|backend_ready|auth_smoke):"
    r"\s*([a-z_]+)\s*$",
    re.MULTILINE,
)


class AlertParseError(ValueError):
    """Raised when an alert summary or log cannot be safely parsed."""


class PatchError(ValueError):
    """Raised when a proposed repair is outside the allowlist or invalid."""


# --- Data structures ------------------------------------------------------


@dataclass(frozen=True)
class AlertSummary:
    """Validated, privacy-safe view of an alert."""

    status: str
    severity: str
    failed_probes: tuple[str, ...]
    error_kinds: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "failed_probes", tuple(self.failed_probes))
        object.__setattr__(self, "error_kinds", tuple(self.error_kinds))

        if self.status not in (STATUS_HEALTHY, STATUS_ALERT):
            raise ValueError("unknown alert status")
        if self.severity not in SEVERITIES:
            raise ValueError("unknown severity")

        probes = self.failed_probes
        if len(set(probes)) != len(probes):
            raise ValueError("duplicate failed probe")
        if any(probe not in PROBE_NAMES for probe in probes):
            raise ValueError("unknown failed probe")

        kinds = dict(self.error_kinds)
        if len(kinds) != len(self.error_kinds):
            raise ValueError("duplicate error kind")
        if set(kinds) != set(probes):
            raise ValueError("error kinds must cover exactly the failed probes")
        if any(kind not in ERROR_KINDS for kind in kinds.values()):
            raise ValueError("unknown error kind")

        if self.status == STATUS_HEALTHY:
            if probes:
                raise ValueError("healthy summary cannot list failed probes")
            if self.severity != "none":
                raise ValueError("healthy summary must have severity none")
        else:
            if not probes:
                raise ValueError("alert summary must list failed probes")
            if self.severity == "none":
                raise ValueError("alert summary must have a non-none severity")


@dataclass(frozen=True)
class Rule:
    category: str
    headline: str
    rationale: str
    next_step: str


@dataclass(frozen=True)
class Diagnosis:
    category: str
    action: str
    headline: str
    rationale: str
    next_step: str


@dataclass(frozen=True)
class RepairResult:
    target: str
    patch_name: str
    applied_path: str


@dataclass(frozen=True)
class VerificationResult:
    command: str
    passed: bool
    skipped: bool = False
    output_tail: str = ""


@dataclass(frozen=True)
class Scenario:
    name: str
    summary: AlertSummary
    kind: str  # "repair" or "escalate"
    category: str
    headline: str
    rationale: str
    next_step: str
    target: str | None = None
    fixed_content: str | None = None
    env_var: str | None = None
    verification_test: str | None = None


# --- Parsing --------------------------------------------------------------


def _validate_failed_probes(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise AlertParseError("failed_probes must be a list")
    probes = tuple(value)
    if any(not isinstance(probe, str) for probe in probes):
        raise AlertParseError("failed_probes must contain strings")
    if len(set(probes)) != len(probes):
        raise AlertParseError("duplicate failed probe")
    if any(probe not in PROBE_NAMES for probe in probes):
        raise AlertParseError("unknown failed probe")
    return probes


def parse_alert_summary(payload: Mapping[str, object]) -> AlertSummary:
    """Validate a structured alert summary and reject unknown content."""

    if not isinstance(payload, Mapping):
        raise AlertParseError("alert summary must be an object")

    allowed = {"status", "severity", "failed_probes", "probes"}
    if set(payload) - allowed:
        raise AlertParseError("alert summary contains unknown fields")

    status = payload.get("status")
    severity = payload.get("severity")
    probes = payload.get("probes", {})

    if not isinstance(status, str) or status not in (
        STATUS_HEALTHY,
        STATUS_ALERT,
    ):
        raise AlertParseError("unknown alert status")
    if not isinstance(severity, str) or severity not in SEVERITIES:
        raise AlertParseError("unknown severity")
    failed = _validate_failed_probes(payload.get("failed_probes"))
    if not isinstance(probes, Mapping):
        raise AlertParseError("probes must be an object")

    kinds: dict[str, str] = {}
    for key, value in probes.items():
        if not isinstance(key, str) or key not in PROBE_NAMES:
            raise AlertParseError("unknown probe in probes")
        if key not in failed:
            raise AlertParseError("probes contains a probe not in failed_probes")
        if not isinstance(value, str) or value not in ERROR_KINDS:
            raise AlertParseError("unknown error kind")
        kinds[key] = value

    for probe in failed:
        kinds.setdefault(probe, "monitoring_error")

    try:
        return AlertSummary(
            status=status,
            severity=severity,
            failed_probes=failed,
            error_kinds=tuple(sorted(kinds.items())),
        )
    except ValueError as exc:
        raise AlertParseError(str(exc)) from exc


def _normalize_failed_probes(text: str) -> tuple[str, ...]:
    text = text.strip()
    if text in ("", "none"):
        return ()
    return tuple(part.strip() for part in text.split(",") if part.strip())


def parse_alert_log(text: str) -> AlertSummary:
    """Parse only the runner's allowlisted summary lines from a failed log.

    Duplicate identical summary lines are accepted. Conflicting values for
    ``status``, ``severity``, ``failed_probes``, or a probe's error kind are
    rejected rather than silently taking the last match.
    """

    if not isinstance(text, str):
        raise AlertParseError("log must be text")
    if len(text) > MAX_LOG_CHARS:
        raise AlertParseError("log exceeds the maximum size")

    cleaned = _ANSI_RE.sub("", text)

    statuses = set(_STATUS_RE.findall(cleaned))
    if not statuses:
        raise AlertParseError("missing status line")
    if len(statuses) > 1:
        raise AlertParseError("conflicting status lines")
    status = next(iter(statuses))

    severities = set(_SEVERITY_RE.findall(cleaned))
    if len(severities) > 1:
        raise AlertParseError("conflicting severity lines")

    failed_sets = {
        _normalize_failed_probes(match)
        for match in _FAILED_RE.findall(cleaned)
    }
    if len(failed_sets) > 1:
        raise AlertParseError("conflicting failed_probes lines")

    if status == STATUS_HEALTHY:
        if severities and severities != {"none"}:
            raise AlertParseError("healthy summary has a non-none severity")
        if failed_sets and failed_sets != {()}:
            raise AlertParseError("healthy summary lists failed probes")
        return AlertSummary(
            status=STATUS_HEALTHY,
            severity="none",
            failed_probes=(),
            error_kinds=(),
        )

    if not severities:
        raise AlertParseError("missing severity line")
    severity = next(iter(severities))

    if not failed_sets:
        raise AlertParseError("missing failed_probes line")
    failed = next(iter(failed_sets))

    kinds: dict[str, str] = {}
    for probe, kind in _PROBE_KIND_RE.findall(cleaned):
        if probe in kinds and kinds[probe] != kind:
            raise AlertParseError("conflicting probe kind lines")
        kinds[probe] = kind

    return parse_alert_summary(
        {
            "status": status,
            "severity": severity,
            "failed_probes": list(failed),
            "probes": kinds,
        }
    )


def _build_summary(
    status: str, severity: str, probes: Mapping[str, str]
) -> AlertSummary:
    return AlertSummary(
        status=status,
        severity=severity,
        failed_probes=tuple(sorted(probes)),
        error_kinds=tuple(sorted(probes.items())),
    )


# --- Classification -------------------------------------------------------


def _probe_effective_rank(probe: str, kind: str) -> int:
    if kind in INFRASTRUCTURE_ERROR_KINDS:
        return 1
    return PROBE_RANK[probe]


def _rule_for(probe: str, kind: str) -> Rule:
    # An authentication Origin/validation failure is a configuration fault, but
    # it has a more specific diagnosis than the generic infrastructure bucket.
    if probe == PROBE_AUTH_SMOKE and kind == "configuration_error":
        return Rule(
            "auth_origin_configuration",
            "Authentication smoke configuration fault",
            "A 403 Origin rejection or 422 validation response is a "
            "probe/configuration fault, not a real authentication outage.",
            "Check the trusted Origin and Pages proxy configuration; "
            "escalate. Do not change authentication code automatically.",
        )

    if kind in INFRASTRUCTURE_ERROR_KINDS:
        return Rule(
            "monitoring",
            "Monitoring or configuration fault",
            "The probe reported a monitoring or configuration failure. This is "
            "not evidence of an application outage.",
            "Check the probe configuration, workflow environment, and alerting "
            "runner; escalate to the owner.",
        )

    if probe == PROBE_FRONTEND:
        if kind == "body_mismatch":
            return Rule(
                "frontend_marker_contract",
                "Frontend root marker mismatch",
                'The deployed HTML does not contain the expected `id="root"` '
                "marker. This is a known code/deploy defect; the production "
                "application path is outside the automatic repair allowlist.",
                "Inspect the latest Pages build and the frontend entry HTML; "
                "prepare a human-reviewed fix.",
            )
        if kind in ("timeout", "connection_error"):
            return Rule(
                "provider_outage",
                "Frontend probe could not connect",
                "The Pages origin did not respond within the probe budget. This "
                "is an external availability or network failure, not a code "
                "defect the agent can repair.",
                "Check Cloudflare Pages status and the deployment; escalate.",
            )
        if kind == "status_mismatch":
            return Rule(
                "frontend_status",
                "Frontend returned an unexpected status",
                "The Pages origin returned a non-200 response, which usually "
                "indicates a deployment, routing, or provider problem.",
                "Check the Pages deployment and proxy routing; escalate.",
            )
        if kind == "malformed_body":
            return Rule(
                "frontend_response",
                "Frontend response was malformed",
                "The Pages response was not usable HTML. Inspect the deployment "
                "artifact and proxy response.",
                "Check the latest build, then escalate.",
            )
        if kind == "proxy_error":
            return Rule(
                "proxy",
                "Frontend proxy failure",
                "The Pages proxy or its upstream failed.",
                "Check the Pages Function and upstream routing; escalate.",
            )

    if probe == PROBE_BACKEND_HEALTH:
        return Rule(
            "backend_liveness",
            "Backend liveness failed",
            'The backend health endpoint did not return the expected '
            '`{"status":"ok"}` response. Treat this as a liveness or provider '
            "failure.",
            "Check Render service health and backend logs; escalate.",
        )

    if probe == PROBE_BACKEND_READY:
        if kind in ("timeout", "connection_error"):
            return Rule(
                "provider_outage",
                "Backend readiness probe could not connect",
                "The readiness endpoint did not respond within the probe budget.",
                "Check Render and Neon status; escalate.",
            )
        if kind == "status_mismatch":
            return Rule(
                "database_or_provider",
                "Backend readiness failed",
                "A 503 or database-unavailable readiness response is a "
                "High-severity database/provider failure.",
                "Check Neon database status and backend readiness logs; escalate.",
            )
        return Rule(
            "readiness_contract",
            "Backend readiness contract mismatch",
            'The readiness response did not match the expected '
            '`{"status":"ready"}` contract. This is not safe to auto-repair.',
            "Inspect the deployed backend version and readiness handler; escalate.",
        )

    if probe == PROBE_AUTH_SMOKE:
        if kind == "configuration_error":
            return Rule(
                "auth_origin_configuration",
                "Authentication smoke configuration fault",
                "A 403 Origin rejection or 422 validation response is a "
                "probe/configuration fault, not a real authentication outage.",
                "Check the trusted Origin and Pages proxy configuration; "
                "escalate. Do not change authentication code automatically.",
            )
        if kind == "proxy_error":
            return Rule(
                "proxy",
                "Authentication smoke proxy failure",
                "The Pages Function proxy or the Render upstream failed.",
                "Check Cloudflare Pages Functions and the Render upstream; escalate.",
            )
        if kind in ("timeout", "connection_error"):
            return Rule(
                "provider_outage",
                "Authentication smoke could not connect",
                "The login smoke request did not complete within the probe budget.",
                "Check Pages/Render availability and network egress; escalate.",
            )
        if kind in ("status_mismatch", "body_mismatch", "malformed_body"):
            return Rule(
                "auth_contract",
                "Authentication smoke contract mismatch",
                "The login endpoint did not return the expected 401 "
                "`authentication_failed` envelope. Inspect recent auth/proxy "
                "changes.",
                "Escalate to the owner; do not alter authentication behavior "
                "automatically.",
            )

    return Rule(
        "unknown",
        "Unclassified failure",
        "The probe/error combination is not covered by the rulebook.",
        "Escalate with the evidence report.",
    )


def classify(
    summary: AlertSummary, *, scenario: str | None = None
) -> Diagnosis:
    """Classify a validated alert. Production paths always escalate."""

    if summary.status == STATUS_HEALTHY:
        return Diagnosis(
            "healthy",
            "none",
            "No alert observed",
            "The summary reports a healthy run.",
            "No action required.",
        )

    if scenario is not None:
        spec = SCENARIOS.get(scenario)
        if spec is None:
            raise ValueError("unknown scenario")
        return Diagnosis(
            spec.category,
            "repair",
            spec.headline,
            spec.rationale,
            spec.next_step,
        )

    kinds = dict(summary.error_kinds)
    ordered = sorted(
        summary.failed_probes,
        key=lambda probe: (
            -_probe_effective_rank(probe, kinds[probe]),
            PROBE_ORDER.index(probe),
        ),
    )
    probe = ordered[0]
    rule = _rule_for(probe, kinds[probe])
    return Diagnosis(
        rule.category,
        "escalate",
        rule.headline,
        rule.rationale,
        rule.next_step,
    )


# --- Patch safety and generation -----------------------------------------


def is_allowed_path(path: str) -> bool:
    """Return True only for explicit fixture paths under the allowlist."""

    if not isinstance(path, str) or not path:
        return False
    if "\\" in path or "\x00" in path:
        return False
    normalized = posixpath.normpath(path)
    if posixpath.isabs(normalized) or normalized == "..":
        return False
    if normalized.startswith("../"):
        return False
    if not normalized.startswith(ALLOWED_PREFIX):
        return False
    if not normalized.endswith(ALLOWED_SUFFIXES):
        return False
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    if any(part.startswith(".") for part in parts):
        return False
    lowered = normalized.lower()
    if any(
        token in lowered
        for token in ("secret", "credential", ".env", "id_rsa", "private")
    ):
        return False
    if any(
        part in ("app", ".github", "migrations", "frontend")
        for part in parts
    ):
        return False
    return True


def generate_patch(target: str, before: str, after: str) -> str:
    """Generate a unified diff for an allowlisted fixture path."""

    if not is_allowed_path(target):
        raise PatchError("target is outside the repair allowlist")
    if before == after:
        raise PatchError("no change to patch")
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{target}",
        tofile=f"b/{target}",
        lineterm="",
    )
    patch = "\n".join(diff)
    if not patch.strip():
        raise PatchError("empty patch")
    return patch + "\n"


def apply_fix(work_dir: Path, target: str, fixed_content: str) -> Path:
    """Apply a fixed fixture body inside an ephemeral work directory."""

    if not is_allowed_path(target):
        raise PatchError("target is outside the repair allowlist")
    root = Path(work_dir).resolve()
    destination = (root / target).resolve()
    if not destination.is_relative_to(root):
        raise PatchError("target escapes the work directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(fixed_content, encoding="utf-8")
    return destination


# --- Controlled scenarios -------------------------------------------------

_FRONTEND_BROKEN = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>FinPath on-call fixture</title>
  </head>
  <body>
    <!-- Intentionally broken fixture for the On-Call simulation. -->
    <div class="app-shell"></div>
  </body>
</html>
"""

_FRONTEND_FIXED = _FRONTEND_BROKEN.replace(
    '<div class="app-shell"></div>',
    '<div id="root" class="app-shell"></div>',
)

_AUTH_BROKEN = """{
  "trusted_origin": "",
  "note": "Intentionally broken fixture for the On-Call simulation."
}
"""

_AUTH_FIXED = """{
  "trusted_origin": "https://finpath-website.pages.dev",
  "note": "Intentionally broken fixture for the On-Call simulation."
}
"""


def _build_scenarios() -> dict[str, Scenario]:
    return {
        "frontend_marker_fixture": Scenario(
            name="frontend_marker_fixture",
            summary=_build_summary(
                STATUS_ALERT, "critical", {PROBE_FRONTEND: "body_mismatch"}
            ),
            kind="repair",
            category="frontend_marker_contract",
            headline="Isolated fixture is missing the root marker",
            rationale="The seeded fixture intentionally omits the root marker "
            "that the frontend probe requires. This is a controlled "
            "demonstration, not a production defect.",
            next_step="Apply the proposed fixture patch and run the focused "
            "verification test.",
            target="tests/fixtures/oncall/broken_frontend_page.html",
            fixed_content=_FRONTEND_FIXED,
            env_var="ONCALL_REPAIRED_FIXTURE",
            verification_test="repaired_frontend_fixture_has_root_marker",
        ),
        "auth_origin_fixture": Scenario(
            name="auth_origin_fixture",
            summary=_build_summary(
                STATUS_ALERT, "medium", {PROBE_AUTH_SMOKE: "configuration_error"}
            ),
            kind="repair",
            category="auth_origin_configuration",
            headline="Isolated fixture is missing the trusted Origin",
            rationale="The seeded fixture intentionally has an empty trusted "
            "origin. This is a controlled demonstration, not a production "
            "authentication change.",
            next_step="Apply the proposed fixture patch and run the focused "
            "verification test.",
            target="tests/fixtures/oncall/broken_auth_origin.json",
            fixed_content=_AUTH_FIXED,
            env_var="ONCALL_REPAIRED_AUTH_FIXTURE",
            verification_test="repaired_auth_origin_has_trusted_origin",
        ),
        "backend_ready_outage": Scenario(
            name="backend_ready_outage",
            summary=_build_summary(
                STATUS_ALERT, "high", {PROBE_BACKEND_READY: "status_mismatch"}
            ),
            kind="escalate",
            category="database_or_provider",
            headline="Backend readiness failed",
            rationale="A 503 or unavailable database is a High-severity "
            "provider failure. No code repair is appropriate.",
            next_step="Check Neon and Render status; escalate to the owner.",
        ),
        "unknown_failure": Scenario(
            name="unknown_failure",
            summary=_build_summary(
                STATUS_ALERT, "medium", {PROBE_AUTH_SMOKE: "monitoring_error"}
            ),
            kind="escalate",
            category="monitoring",
            headline="Monitoring or configuration fault",
            rationale="The probe reported a monitoring/configuration fault, not "
            "an application outage.",
            next_step="Check probe configuration and the workflow environment.",
        ),
    }


SCENARIOS = _build_scenarios()


# --- Verification and reports --------------------------------------------


_PASSED_RE = re.compile(r"\b[1-9]\d*\s+passed\b")
_NON_PASS_MARKERS = ("skipped", "failed", "error", "no tests ran")


def _verification_passed(returncode: object, output: str) -> bool:
    """Accept a verification only with a positive pass and no skip/failure."""

    if returncode != 0:
        return False
    lowered = output.lower()
    if any(marker in lowered for marker in _NON_PASS_MARKERS):
        return False
    return bool(_PASSED_RE.search(lowered))


def run_verification(
    spec: Scenario,
    repaired_path: Path,
    *,
    runner: Callable[..., object] | None = None,
) -> VerificationResult:
    """Run the fixed focused test for a repair scenario."""

    if spec.verification_test is None or spec.env_var is None:
        raise ValueError("scenario has no verification test")

    command = [
        sys.executable,
        "-m",
        "pytest",
        "--noconftest",
        "tests/test_oncall_agent.py",
        "-q",
        "-k",
        spec.verification_test,
    ]
    env = os.environ.copy()
    env[spec.env_var] = str(repaired_path)
    active_runner = runner if runner is not None else subprocess.run

    try:
        completed = active_runner(
            command,
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=MAX_VERIFICATION_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            " ".join(command), False, False, "verification timed out"
        )
    except OSError as exc:
        return VerificationResult(
            " ".join(command),
            False,
            False,
            f"verification could not start: {type(exc).__name__}",
        )

    stdout = getattr(completed, "stdout", "") or ""
    stderr = getattr(completed, "stderr", "") or ""
    output = (stdout + stderr).strip()
    return VerificationResult(
        " ".join(command),
        _verification_passed(getattr(completed, "returncode", 1), output),
        False,
        output[-MAX_VERIFICATION_OUTPUT:],
    )


def _report_header(
    summary: AlertSummary, *, mode: str, run_id: str
) -> list[str]:
    lines = [
        "# FinPath On-Call Diagnosis",
        "",
        f"- Mode: {mode}",
        f"- Run: {run_id}",
        f"- Status: {summary.status}",
        f"- Severity: {summary.severity}",
        "- Failed probes: "
        + (", ".join(summary.failed_probes) or "none"),
    ]
    if summary.error_kinds:
        lines.append(
            "- Error kinds: "
            + ", ".join(f"{probe}={kind}" for probe, kind in summary.error_kinds)
        )
    return lines


def render_report(
    summary: AlertSummary,
    diagnosis: Diagnosis,
    *,
    mode: str,
    run_id: str,
    repair: RepairResult | None = None,
    verification: VerificationResult | None = None,
) -> str:
    lines = _report_header(summary, mode=mode, run_id=run_id)
    lines += [
        "",
        "## Diagnosis",
        f"- Category: {diagnosis.category}",
        f"- Action: {diagnosis.action}",
        f"- Headline: {diagnosis.headline}",
        "",
        diagnosis.rationale,
        "",
        f"**Next step:** {diagnosis.next_step}",
        "",
        "## Evidence",
    ]
    for probe, kind in summary.error_kinds:
        lines.append(f"- `{probe}`: `{kind}`")

    lines += ["", "## Repair"]
    if repair is None:
        lines.append(
            "No automatic repair was produced. Production application code is "
            "outside the repair allowlist."
        )
    else:
        lines += [
            f"- Target: `{repair.target}`",
            f"- Patch: `{repair.patch_name}`",
            f"- Applied to: `{repair.applied_path}`",
            "- Scope: allowlisted test fixture only; no application code.",
        ]

    lines += ["", "## Verification"]
    if verification is None:
        lines.append("Not applicable.")
    elif verification.skipped:
        lines.append("Verification skipped. This is **not** a verified repair.")
    else:
        lines.append(f"- Command: `{verification.command}`")
        lines.append(
            f"- Result: {'PASSED' if verification.passed else 'FAILED'}"
        )
        if verification.output_tail:
            lines += ["", "```text", verification.output_tail, "```"]

    lines += [
        "",
        "## Security and limitations",
        "- Read-only repository and Actions access; no secrets, writes, "
        "deployments, or production database access.",
        "- The agent never executes alert or log content.",
        "- A diagnosis is not a repair. A repair is claimed only when the "
        "focused verification test passes.",
        "- Automatic production repairs are intentionally out of scope; "
        "application code is not in the allowlist.",
        "",
    ]
    return "\n".join(lines)


def _report_runner_failure(*, mode: str, run_id: str) -> str:
    return "\n".join(
        [
            "# FinPath On-Call Diagnosis",
            "",
            f"- Mode: {mode}",
            f"- Run: {run_id}",
            "- Result: the alerting runner failed before producing a probe summary.",
            "",
            "## Diagnosis",
            "- Category: runner_failure",
            "- Action: escalate",
            "",
            "The runner emitted a fixed internal-error line. No probe results "
            "are available, so this is treated as a monitoring/runner failure, "
            "not evidence of an application outage.",
            "",
            "**Next step:** Inspect the failed alerting run's "
            "`Run FinPath production probes` step and escalate to the owner.",
            "",
            "## Security and limitations",
            "- No log content is reproduced. No secrets, writes, or deployments.",
            "",
        ]
    )


def _report_unparsed(*, mode: str, run_id: str) -> str:
    return "\n".join(
        [
            "# FinPath On-Call Diagnosis",
            "",
            f"- Mode: {mode}",
            f"- Run: {run_id}",
            "- Result: no safe alert summary could be parsed.",
            "",
            "## Diagnosis",
            "- Category: unparsed_alert_summary",
            "- Action: escalate",
            "",
            "The failed run log did not contain the allowlisted alert summary. "
            "The log is treated as untrusted and is not reproduced.",
            "",
            "**Next step:** Inspect the failed alerting run manually and "
            "escalate to the owner.",
            "",
            "## Security and limitations",
            "- No log content is reproduced. No secrets, writes, or deployments.",
            "",
        ]
    )


def _write_outputs(
    output_dir: Path,
    report: str,
    payload: Mapping[str, object],
    patch: str | None = None,
) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "oncall-report.md").write_text(report, encoding="utf-8")
    (destination / "oncall-diagnosis.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if patch is not None:
        (destination / "oncall-proposed.patch").write_text(
            patch, encoding="utf-8"
        )


def _diagnosis_payload(
    *,
    mode: str,
    run_id: str,
    summary: AlertSummary,
    diagnosis: Diagnosis,
    repair: RepairResult | None,
    verification: VerificationResult | None,
) -> dict[str, object]:
    return {
        "mode": mode,
        "run_id": run_id,
        "summary": {
            "status": summary.status,
            "severity": summary.severity,
            "failed_probes": list(summary.failed_probes),
            "error_kinds": [list(pair) for pair in summary.error_kinds],
        },
        "diagnosis": {
            "category": diagnosis.category,
            "action": diagnosis.action,
            "headline": diagnosis.headline,
            "rationale": diagnosis.rationale,
            "next_step": diagnosis.next_step,
        },
        "repair": None
        if repair is None
        else {
            "target": repair.target,
            "patch": repair.patch_name,
            "applied_to": repair.applied_path,
        },
        "verification": None
        if verification is None
        else {
            "command": verification.command,
            "passed": verification.passed,
            "skipped": verification.skipped,
        },
    }


# --- Entry points ---------------------------------------------------------


def run_diagnose(
    log_file: Path, output_dir: Path, *, run_id: str = "unknown"
) -> int:
    """Diagnose a failed alerting run from its log. Production never repairs."""

    try:
        text = Path(log_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        _write_outputs(
            output_dir,
            _report_unparsed(mode="automatic", run_id=run_id),
            {"mode": "automatic", "run_id": run_id, "error": "log_unreadable"},
        )
        return 2

    try:
        summary = parse_alert_log(text)
    except AlertParseError:
        if "alerting runner failed: internal error" in text:
            report = _report_runner_failure(mode="automatic", run_id=run_id)
            _write_outputs(
                output_dir,
                report,
                {
                    "mode": "automatic",
                    "run_id": run_id,
                    "error": "runner_failure",
                },
            )
            return 0
        report = _report_unparsed(mode="automatic", run_id=run_id)
        _write_outputs(
            output_dir,
            report,
            {
                "mode": "automatic",
                "run_id": run_id,
                "error": "unparsed_alert_summary",
            },
        )
        return 2

    diagnosis = classify(summary)
    report = render_report(
        summary, diagnosis, mode="automatic", run_id=run_id
    )
    _write_outputs(
        output_dir,
        report,
        _diagnosis_payload(
            mode="automatic",
            run_id=run_id,
            summary=summary,
            diagnosis=diagnosis,
            repair=None,
            verification=None,
        ),
    )
    return 0 if summary.status == STATUS_ALERT else 1


def run_simulation(
    scenario: str,
    *,
    output_dir: Path,
    work_dir: Path,
    skip_verification: bool = False,
    runner: Callable[..., object] | None = None,
) -> int:
    """Run a controlled scenario. Only repair scenarios produce a patch."""

    spec = SCENARIOS.get(scenario)
    if spec is None:
        raise ValueError("unknown scenario")

    diagnosis = classify(
        spec.summary,
        scenario=scenario if spec.kind == "repair" else None,
    )
    repair: RepairResult | None = None
    verification: VerificationResult | None = None
    patch: str | None = None

    if spec.kind == "repair":
        if spec.target is None or spec.fixed_content is None:
            raise ValueError("repair scenario is incomplete")
        before = (BACKEND_DIR / spec.target).read_text(encoding="utf-8")
        patch = generate_patch(spec.target, before, spec.fixed_content)
        repaired = apply_fix(Path(work_dir), spec.target, spec.fixed_content)
        repair = RepairResult(spec.target, "oncall-proposed.patch", str(repaired))

        if skip_verification:
            verification = VerificationResult("", False, skipped=True)
            diagnosis = replace(
                diagnosis,
                action="repair_unverified",
                headline="Repair proposed but not verified",
                next_step="Run the focused verification test; do not merge an "
                "unverified patch.",
            )
        else:
            verification = run_verification(
                spec, repaired, runner=runner
            )
            if verification.passed:
                diagnosis = replace(
                    diagnosis,
                    action="repair_verified",
                    headline="Fixture repair applied and verified",
                    next_step="Review the patch and test output; human approval "
                    "is still required before any merge.",
                )
            else:
                diagnosis = replace(
                    diagnosis,
                    action="repair_unverified",
                    headline="Repair proposed but verification failed",
                    next_step="Inspect the test failure; do not merge the patch.",
                )

    report = render_report(
        spec.summary,
        diagnosis,
        mode="simulation",
        run_id=scenario,
        repair=repair,
        verification=verification,
    )
    _write_outputs(
        output_dir,
        report,
        _diagnosis_payload(
            mode="simulation",
            run_id=scenario,
            summary=spec.summary,
            diagnosis=diagnosis,
            repair=repair,
            verification=verification,
        ),
        patch,
    )

    if spec.kind == "escalate":
        return 0
    return 0 if verification is not None and verification.passed else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic, bounded FinPath on-call agent."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    diagnose = sub.add_parser("diagnose", help="diagnose a failed alerting log")
    diagnose.add_argument("--log-file", required=True)
    diagnose.add_argument("--output-dir", required=True)
    diagnose.add_argument("--run-id", default="unknown")

    simulate = sub.add_parser(
        "simulate", help="run a controlled scenario without production probes"
    )
    simulate.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    simulate.add_argument("--output-dir", required=True)
    simulate.add_argument("--work-dir", required=True)
    simulate.add_argument("--skip-verification", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "diagnose":
            return run_diagnose(
                Path(args.log_file),
                Path(args.output_dir),
                run_id=str(args.run_id),
            )
        return run_simulation(
            str(args.scenario),
            output_dir=Path(args.output_dir),
            work_dir=Path(args.work_dir),
            skip_verification=bool(args.skip_verification),
        )
    except (AlertParseError, PatchError, ValueError) as exc:
        print(f"on-call agent rejected input: {type(exc).__name__}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - never leak internal detail
        print(f"on-call agent failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
