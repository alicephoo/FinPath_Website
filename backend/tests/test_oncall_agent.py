"""Offline tests for the deterministic FinPath on-call agent.

No network, database, Docker, secrets, or external services are used. The
repair tests operate only on explicitly seeded fixture content and temporary
directories.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.core.oncall_agent import (
    SCENARIOS,
    AlertParseError,
    AlertSummary,
    PatchError,
    apply_fix,
    classify,
    generate_patch,
    is_allowed_path,
    main,
    parse_alert_log,
    parse_alert_summary,
    render_report,
    run_diagnose,
    run_simulation,
)

_VALID_LOG = """\
alerting\tRun FinPath production probes\t2026-11-15T00:00:00Z status: alert
alerting\tRun FinPath production probes\t2026-11-15T00:00:00Z severity: high
alerting\tRun FinPath production probes\t2026-11-15T00:00:00Z failed_probes: backend_ready
alerting\tRun FinPath production probes\t2026-11-15T00:00:00Z - backend_ready: status_mismatch
alerting\tRun FinPath production probes\t2026-11-15T00:00:00Z summary: HIGH alert: 1 probe(s) failed: backend_ready (status_mismatch).
"""


class _FakeRunner:
    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "1 passed",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return subprocess.CompletedProcess(
            command, self.returncode, stdout=self.stdout, stderr=self.stderr
        )


def _summary(probe: str, kind: str, severity: str) -> AlertSummary:
    return parse_alert_summary(
        {
            "status": "alert",
            "severity": severity,
            "failed_probes": [probe],
            "probes": {probe: kind},
        }
    )


# --- Summary parsing ------------------------------------------------------


def test_parse_valid_summary() -> None:
    summary = parse_alert_summary(
        {
            "status": "alert",
            "severity": "high",
            "failed_probes": ["backend_ready"],
            "probes": {"backend_ready": "status_mismatch"},
        }
    )

    assert summary.failed_probes == ("backend_ready",)
    assert summary.error_kinds == (("backend_ready", "status_mismatch"),)


def test_parse_missing_probe_kind_defaults_to_monitoring_error() -> None:
    summary = parse_alert_summary(
        {
            "status": "alert",
            "severity": "medium",
            "failed_probes": ["frontend"],
            "probes": {},
        }
    )

    assert summary.error_kinds == (("frontend", "monitoring_error"),)


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "alert", "severity": "high", "failed_probes": ["frontend"], "extra": 1},
        {"status": "alert", "severity": "high", "failed_probes": ["nope"]},
        {"status": "alert", "severity": "high", "failed_probes": ["frontend"], "probes": {"frontend": "nope"}},
        {"status": "alert", "severity": "high", "failed_probes": ["frontend", "frontend"]},
        {"status": "alert", "severity": "high", "failed_probes": []},
        {"status": "healthy", "severity": "none", "failed_probes": ["frontend"]},
        {"status": "healthy", "severity": "high", "failed_probes": []},
        {"status": "alert", "severity": "high", "failed_probes": "frontend"},
        {"status": "alert", "severity": "high", "failed_probes": ["frontend"], "probes": []},
        {"status": "alert", "severity": "high", "failed_probes": ["frontend"], "probes": {"backend_ready": "timeout"}},
    ],
)
def test_parse_rejects_malformed_summaries(payload: dict) -> None:
    with pytest.raises(AlertParseError):
        parse_alert_summary(payload)


def test_parse_rejects_non_mapping() -> None:
    with pytest.raises(AlertParseError):
        parse_alert_summary(["not", "a", "mapping"])  # type: ignore[arg-type]


# --- Log parsing ----------------------------------------------------------


def test_parse_log_valid() -> None:
    summary = parse_alert_log(_VALID_LOG)

    assert summary.status == "alert"
    assert summary.severity == "high"
    assert summary.failed_probes == ("backend_ready",)
    assert summary.error_kinds == (("backend_ready", "status_mismatch"),)


def test_parse_log_handles_ansi_and_prefixes() -> None:
    text = (
        "\x1b[31malerting\tStep\t2026-11-15T00:00:00Z status: alert\x1b[0m\n"
        "alerting\tStep\t2026-11-15T00:00:00Z severity: critical\n"
        "alerting\tStep\t2026-11-15T00:00:00Z failed_probes: frontend\n"
        "alerting\tStep\t2026-11-15T00:00:00Z - frontend: body_mismatch\n"
    )

    summary = parse_alert_log(text)

    assert summary.failed_probes == ("frontend",)
    assert summary.error_kinds == (("frontend", "body_mismatch"),)


def test_parse_log_rejects_conflicting_status_lines() -> None:
    with pytest.raises(AlertParseError):
        parse_alert_log(_VALID_LOG + "status: healthy\n")


def test_parse_log_accepts_duplicate_identical_status_lines() -> None:
    summary = parse_alert_log(_VALID_LOG + "status: alert\n")

    assert summary.status == "alert"


def test_parse_log_accepts_duplicate_identical_summary_blocks() -> None:
    summary = parse_alert_log(_VALID_LOG + _VALID_LOG)

    assert summary.failed_probes == ("backend_ready",)
    assert summary.error_kinds == (("backend_ready", "status_mismatch"),)


def test_parse_log_rejects_conflicting_severities() -> None:
    with pytest.raises(AlertParseError):
        parse_alert_log(_VALID_LOG + "severity: medium\n")


def test_parse_log_rejects_conflicting_failed_probes() -> None:
    with pytest.raises(AlertParseError):
        parse_alert_log(_VALID_LOG + "failed_probes: frontend\n")


def test_parse_log_rejects_conflicting_probe_kinds() -> None:
    text = _VALID_LOG + (
        "alerting\tStep\t2026-11-15T00:00:01Z - backend_ready: timeout\n"
    )

    with pytest.raises(AlertParseError):
        parse_alert_log(text)


def test_parse_log_accepts_duplicate_identical_probe_kind_lines() -> None:
    text = _VALID_LOG + (
        "alerting\tStep\t2026-11-15T00:00:01Z - backend_ready: status_mismatch\n"
    )

    summary = parse_alert_log(text)

    assert dict(summary.error_kinds)["backend_ready"] == "status_mismatch"


def test_parse_log_healthy() -> None:
    summary = parse_alert_log("status: healthy\nseverity: none\nfailed_probes: none\n")

    assert summary.status == "healthy"
    assert summary.failed_probes == ()
    assert classify(summary).action == "none"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "severity: high\nfailed_probes: frontend\n",
        "status: alert\nfailed_probes: frontend\n- frontend: timeout\n",
        "status: alert\nseverity: high\n- frontend: timeout\n",
        "status: alert\nseverity: high\nfailed_probes: frontend\n- frontend: not_a_kind\n",
    ],
)
def test_parse_log_rejects_incomplete_or_unknown(text: str) -> None:
    with pytest.raises(AlertParseError):
        parse_alert_log(text)


# --- Classification -------------------------------------------------------


@pytest.mark.parametrize(
    "probe,kind,severity,category",
    [
        ("frontend", "body_mismatch", "critical", "frontend_marker_contract"),
        ("frontend", "status_mismatch", "critical", "frontend_status"),
        ("frontend", "timeout", "critical", "provider_outage"),
        ("frontend", "connection_error", "critical", "provider_outage"),
        ("frontend", "malformed_body", "critical", "frontend_response"),
        ("frontend", "proxy_error", "critical", "proxy"),
        ("frontend", "configuration_error", "medium", "monitoring"),
        ("frontend", "monitoring_error", "medium", "monitoring"),
        ("backend_health", "status_mismatch", "critical", "backend_liveness"),
        ("backend_health", "body_mismatch", "critical", "backend_liveness"),
        ("backend_health", "configuration_error", "medium", "monitoring"),
        ("backend_health", "monitoring_error", "medium", "monitoring"),
        ("backend_ready", "status_mismatch", "high", "database_or_provider"),
        ("backend_ready", "timeout", "high", "provider_outage"),
        ("backend_ready", "connection_error", "high", "provider_outage"),
        ("backend_ready", "body_mismatch", "high", "readiness_contract"),
        ("backend_ready", "malformed_body", "high", "readiness_contract"),
        ("auth_smoke", "configuration_error", "medium", "auth_origin_configuration"),
        ("auth_smoke", "proxy_error", "high", "proxy"),
        ("auth_smoke", "timeout", "high", "provider_outage"),
        ("auth_smoke", "connection_error", "high", "provider_outage"),
        ("auth_smoke", "status_mismatch", "high", "auth_contract"),
        ("auth_smoke", "body_mismatch", "high", "auth_contract"),
        ("auth_smoke", "malformed_body", "high", "auth_contract"),
        ("auth_smoke", "monitoring_error", "medium", "monitoring"),
    ],
)
def test_classification(
    probe: str, kind: str, severity: str, category: str
) -> None:
    diagnosis = classify(_summary(probe, kind, severity))

    assert diagnosis.action == "escalate"
    assert diagnosis.category == category


def test_classification_selects_highest_severity_failure() -> None:
    summary = parse_alert_summary(
        {
            "status": "alert",
            "severity": "critical",
            "failed_probes": ["backend_ready", "frontend"],
            "probes": {
                "backend_ready": "status_mismatch",
                "frontend": "body_mismatch",
            },
        }
    )

    diagnosis = classify(summary)

    assert diagnosis.category == "frontend_marker_contract"


def test_classification_honours_infrastructure_cap() -> None:
    summary = parse_alert_summary(
        {
            "status": "alert",
            "severity": "medium",
            "failed_probes": ["frontend", "backend_health"],
            "probes": {
                "frontend": "monitoring_error",
                "backend_health": "configuration_error",
            },
        }
    )

    assert classify(summary).category == "monitoring"


def test_classify_repair_scenario() -> None:
    diagnosis = classify(
        _summary("frontend", "body_mismatch", "critical"),
        scenario="frontend_marker_fixture",
    )

    assert diagnosis.action == "repair"
    assert diagnosis.category == "frontend_marker_contract"


def test_classify_unknown_scenario_raises() -> None:
    with pytest.raises(ValueError):
        classify(_summary("frontend", "body_mismatch", "critical"), scenario="nope")


# --- Allowlist and patch safety ------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/fixtures/oncall/broken_frontend_page.html",
        "tests/fixtures/oncall/broken_auth_origin.json",
    ],
)
def test_allowed_fixture_paths(path: str) -> None:
    assert is_allowed_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "backend/app/core/oncall_agent.py",
        "backend/app/api/auth.py",
        "frontend/index.html",
        "frontend/functions/api/proxy.ts",
        ".github/workflows/alerting.yml",
        "backend/migrations/0001.py",
        "tests/fixtures/oncall/../../app/core/x.html",
        "/tests/fixtures/oncall/x.html",
        "tests/fixtures/oncall/secrets.json",
        "tests/fixtures/oncall/x.txt",
        "tests/fixtures/oncall/x.html.bak",
    ],
)
def test_sensitive_or_escaping_paths_rejected(path: str) -> None:
    assert is_allowed_path(path) is False


def test_generate_patch_returns_unified_diff() -> None:
    patch = generate_patch(
        "tests/fixtures/oncall/broken_frontend_page.html",
        "<div class=\"app-shell\"></div>\n",
        "<div id=\"root\" class=\"app-shell\"></div>\n",
    )

    assert patch.startswith("--- a/tests/fixtures/oncall/broken_frontend_page.html")
    assert '+<div id="root" class="app-shell"></div>' in patch


def test_generate_patch_rejects_application_path() -> None:
    with pytest.raises(PatchError):
        generate_patch("backend/app/core/oncall_agent.py", "a", "b")


def test_generate_patch_rejects_no_change() -> None:
    with pytest.raises(PatchError):
        generate_patch("tests/fixtures/oncall/x.html", "a", "a")


def test_apply_fix_writes_inside_work_dir(tmp_path: Path) -> None:
    repaired = apply_fix(
        tmp_path,
        "tests/fixtures/oncall/x.html",
        "<div id=\"root\"></div>\n",
    )

    assert repaired.is_relative_to(tmp_path.resolve())
    assert repaired.read_text(encoding="utf-8") == "<div id=\"root\"></div>\n"


@pytest.mark.parametrize(
    "target",
    [
        "../outside.html",
        "tests/fixtures/oncall/../../outside.html",
        "backend/app/core/oncall_agent.py",
    ],
)
def test_apply_fix_rejects_escape_or_sensitive(
    tmp_path: Path, target: str
) -> None:
    with pytest.raises(PatchError):
        apply_fix(tmp_path, target, "x")


# --- Controlled simulations ----------------------------------------------


def test_frontend_fixture_repair_is_verified(tmp_path: Path) -> None:
    runner = _FakeRunner(0)
    output = tmp_path / "out"
    work = tmp_path / "work"

    code = run_simulation(
        "frontend_marker_fixture",
        output_dir=output,
        work_dir=work,
        runner=runner,
    )

    assert code == 0
    repaired = work / "tests/fixtures/oncall/broken_frontend_page.html"
    assert 'id="root"' in repaired.read_text(encoding="utf-8")
    patch = (output / "oncall-proposed.patch").read_text(encoding="utf-8")
    assert '+    <div id="root" class="app-shell"></div>' in patch
    report = (output / "oncall-report.md").read_text(encoding="utf-8")
    assert "repair_verified" in report
    assert "PASSED" in report
    assert len(runner.calls) == 1
    command, kwargs = runner.calls[0]
    assert "--noconftest" in command
    assert kwargs["env"]["ONCALL_REPAIRED_FIXTURE"] == str(repaired)


def test_auth_fixture_repair_is_verified(tmp_path: Path) -> None:
    runner = _FakeRunner(0)
    output = tmp_path / "out"
    work = tmp_path / "work"

    code = run_simulation(
        "auth_origin_fixture",
        output_dir=output,
        work_dir=work,
        runner=runner,
    )

    assert code == 0
    repaired = work / "tests/fixtures/oncall/broken_auth_origin.json"
    assert json.loads(repaired.read_text(encoding="utf-8"))["trusted_origin"] == (
        "https://finpath-website.pages.dev"
    )
    report = (output / "oncall-report.md").read_text(encoding="utf-8")
    assert "repair_verified" in report


def test_verification_failure_prevents_success_claim(tmp_path: Path) -> None:
    runner = _FakeRunner(1)
    output = tmp_path / "out"

    code = run_simulation(
        "frontend_marker_fixture",
        output_dir=output,
        work_dir=tmp_path / "work",
        runner=runner,
    )

    assert code == 1
    report = (output / "oncall-report.md").read_text(encoding="utf-8")
    assert "repair_unverified" in report
    assert "FAILED" in report
    assert "repair_verified" not in report


def test_skipped_verification_is_not_a_verified_repair(tmp_path: Path) -> None:
    output = tmp_path / "out"

    code = run_simulation(
        "frontend_marker_fixture",
        output_dir=output,
        work_dir=tmp_path / "work",
        skip_verification=True,
    )

    assert code == 1
    report = (output / "oncall-report.md").read_text(encoding="utf-8")
    assert "Verification skipped" in report
    assert "repair_unverified" in report


def test_skipped_pytest_output_cannot_produce_repair_verified(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(0, stdout="1 skipped, 85 deselected")
    output = tmp_path / "out"

    code = run_simulation(
        "frontend_marker_fixture",
        output_dir=output,
        work_dir=tmp_path / "work",
        runner=runner,
    )

    assert code == 1
    report = (output / "oncall-report.md").read_text(encoding="utf-8")
    assert "repair_unverified" in report
    assert "repair_verified" not in report
    assert "FAILED" in report


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "no tests ran in 0.01s",
        "0 passed, 1 deselected",
        "1 passed, 1 failed",
    ],
)
def test_inconclusive_pytest_output_is_not_verified(
    tmp_path: Path, stdout: str
) -> None:
    runner = _FakeRunner(0, stdout=stdout)
    output = tmp_path / "out"

    code = run_simulation(
        "frontend_marker_fixture",
        output_dir=output,
        work_dir=tmp_path / "work",
        runner=runner,
    )

    assert code == 1
    report = (output / "oncall-report.md").read_text(encoding="utf-8")
    assert "repair_unverified" in report
    assert "repair_verified" not in report


def test_escalation_scenario_produces_no_patch(tmp_path: Path) -> None:
    output = tmp_path / "out"

    code = run_simulation(
        "backend_ready_outage",
        output_dir=output,
        work_dir=tmp_path / "work",
    )

    assert code == 0
    report = (output / "oncall-report.md").read_text(encoding="utf-8")
    assert "- Action: escalate" in report
    assert "database_or_provider" in report
    assert not (output / "oncall-proposed.patch").exists()


# --- Reports and entry points --------------------------------------------


def test_report_does_not_reproduce_untrusted_log_content() -> None:
    log = _VALID_LOG + "alerting\tStep\t2026-11-15T00:00:00Z SECRET-TOKEN=abc123\n"
    summary = parse_alert_log(log)
    diagnosis = classify(summary)
    report = render_report(
        summary, diagnosis, mode="automatic", run_id="123"
    )

    assert "SECRET-TOKEN" not in report
    assert "abc123" not in report
    assert "backend_ready" in report


def test_run_diagnose_writes_safe_artifacts(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(_VALID_LOG, encoding="utf-8")
    output = tmp_path / "out"

    assert run_diagnose(log, output, run_id="42") == 0
    assert (output / "oncall-report.md").exists()
    assert (output / "oncall-diagnosis.json").exists()
    payload = json.loads(
        (output / "oncall-diagnosis.json").read_text(encoding="utf-8")
    )
    assert payload["diagnosis"]["action"] == "escalate"
    assert "backend_ready" in payload["summary"]["failed_probes"]


def test_run_diagnose_handles_runner_failure(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("alerting runner failed: internal error (RuntimeError)\n")
    output = tmp_path / "out"

    assert run_diagnose(log, output, run_id="43") == 0
    report = (output / "oncall-report.md").read_text(encoding="utf-8")
    assert "runner_failure" in report


def test_run_diagnose_reports_unparsed_log(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("nothing useful here\n")
    output = tmp_path / "out"

    assert run_diagnose(log, output, run_id="44") == 2
    report = (output / "oncall-report.md").read_text(encoding="utf-8")
    assert "unparsed_alert_summary" in report


def test_main_diagnose(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(_VALID_LOG, encoding="utf-8")
    output = tmp_path / "out"

    assert (
        main(
            [
                "diagnose",
                "--log-file",
                str(log),
                "--output-dir",
                str(output),
                "--run-id",
                "99",
            ]
        )
        == 0
    )
    assert (output / "oncall-report.md").exists()


def test_main_simulate_escalation(tmp_path: Path) -> None:
    assert (
        main(
            [
                "simulate",
                "--scenario",
                "backend_ready_outage",
                "--output-dir",
                str(tmp_path / "out"),
                "--work-dir",
                str(tmp_path / "work"),
            ]
        )
        == 0
    )


def test_main_simulate_skipped_verification(tmp_path: Path) -> None:
    assert (
        main(
            [
                "simulate",
                "--scenario",
                "frontend_marker_fixture",
                "--output-dir",
                str(tmp_path / "out"),
                "--work-dir",
                str(tmp_path / "work"),
                "--skip-verification",
            ]
        )
        == 1
    )


def test_scenarios_are_bounded() -> None:
    assert set(SCENARIOS) == {
        "frontend_marker_fixture",
        "auth_origin_fixture",
        "backend_ready_outage",
        "unknown_failure",
    }
    for spec in SCENARIOS.values():
        if spec.kind == "repair":
            assert spec.target and is_allowed_path(spec.target)
            assert spec.fixed_content
            assert spec.env_var
            assert spec.verification_test


# --- Simulation-only verification tests ----------------------------------
# These are skipped in normal runs and executed by the agent's verification
# step with the repaired fixture path supplied through an environment variable.


def test_repaired_frontend_fixture_has_root_marker() -> None:
    path = os.environ.get("ONCALL_REPAIRED_FIXTURE")
    if not path:
        pytest.skip("simulation-only verification")
    assert 'id="root"' in Path(path).read_text(encoding="utf-8")


def test_repaired_auth_origin_has_trusted_origin() -> None:
    path = os.environ.get("ONCALL_REPAIRED_AUTH_FIXTURE")
    if not path:
        pytest.skip("simulation-only verification")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["trusted_origin"] == "https://finpath-website.pages.dev"
