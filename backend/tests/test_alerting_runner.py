"""Offline unit tests for the Phase 2B alerting runner.

Every probe is exercised through an injected in-memory transport, so no test
makes a network request. The runner module imports only the standard library
and the pure evaluator; it does not read application configuration.
"""

from __future__ import annotations

import json

import pytest

from app.core.alerting import (
    DIAGNOSTIC_COMPONENTS,
    DIAGNOSTIC_ENDPOINTS,
    DIAGNOSTIC_KEYS,
    DIAGNOSTIC_REASONS,
    DIAGNOSTIC_STATUS_CODES,
    PROBE_AUTH_SMOKE,
    PROBE_BACKEND_HEALTH,
    PROBE_BACKEND_READY,
    PROBE_FRONTEND,
    STATUS_ALERT,
    STATUS_HEALTHY,
    MonitoringResult,
)
from app.core.alerting_runner import (
    AUTH_SMOKE_EMAIL,
    AUTH_SMOKE_PASSWORD,
    AUTH_SMOKE_URL,
    BACKEND_HEALTH_URL,
    BACKEND_READY_URL,
    FRONTEND_ORIGIN,
    FRONTEND_URL,
    ProbeResponse,
    TransportError,
    main,
    run_monitoring,
)

_ALLOWED_BY_KEY = {
    "reason": DIAGNOSTIC_REASONS,
    "component": DIAGNOSTIC_COMPONENTS,
    "endpoint": DIAGNOSTIC_ENDPOINTS,
    "expected": DIAGNOSTIC_STATUS_CODES,
    "observed": DIAGNOSTIC_STATUS_CODES,
}

_FRONTEND_OK = ProbeResponse(
    200, "text/html; charset=utf-8", b'<!doctype html><div id="root"></div>'
)
_HEALTH_OK = ProbeResponse(200, "application/json", b'{"status":"ok"}')
_READY_OK = ProbeResponse(200, "application/json", b'{"status":"ready"}')
_AUTH_OK = ProbeResponse(
    401, "application/json", b'{"error":{"code":"authentication_failed"}}'
)


class FakeTransport:
    """A scripted transport keyed by URL."""

    def __init__(self, responses: dict[str, list[object]]) -> None:
        self._responses = {
            url: list(items) for url, items in responses.items()
        }
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs) -> ProbeResponse:
        self.calls.append((url, kwargs))
        queue = self._responses.get(url, [])
        if not queue:
            raise AssertionError(f"unexpected extra call to {url}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def call_count(self, url: str) -> int:
        return sum(1 for called, _ in self.calls if called == url)


class RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _ok_responses() -> dict[str, list[object]]:
    return {
        FRONTEND_URL: [_FRONTEND_OK],
        BACKEND_HEALTH_URL: [_HEALTH_OK],
        BACKEND_READY_URL: [_READY_OK],
        AUTH_SMOKE_URL: [_AUTH_OK],
    }


def _run(responses: dict[str, list[object]]) -> tuple[MonitoringResult, FakeTransport, RecordingSleep]:
    transport = FakeTransport(responses)
    sleep = RecordingSleep()
    result = run_monitoring(transport=transport, sleep=sleep)
    return result, transport, sleep


def _check(result: MonitoringResult, name: str):
    matches = [check for check in result.checks if check.name == name]
    assert matches, f"missing check for {name!r}"
    return matches[0]


# --- Success --------------------------------------------------------------


def test_all_four_probes_succeed() -> None:
    result, transport, _ = _run(_ok_responses())

    assert result.status == STATUS_HEALTHY
    assert result.severity == "none"
    assert result.failed_probes == ()
    assert {check.name for check in result.checks} == {
        PROBE_FRONTEND,
        PROBE_BACKEND_HEALTH,
        PROBE_BACKEND_READY,
        PROBE_AUTH_SMOKE,
    }
    assert all(check.passed for check in result.checks)
    for url in (
        FRONTEND_URL,
        BACKEND_HEALTH_URL,
        BACKEND_READY_URL,
        AUTH_SMOKE_URL,
    ):
        assert transport.call_count(url) == 1


def test_auth_probe_uses_reserved_origin_and_invalid_email() -> None:
    _, transport, _ = _run(_ok_responses())

    call = [c for c in transport.calls if c[0] == AUTH_SMOKE_URL][0]
    kwargs = call[1]
    assert kwargs["method"] == "POST"
    assert kwargs["headers"]["Origin"] == FRONTEND_ORIGIN
    payload = json.loads(kwargs["body"])
    assert payload["email"].endswith(".invalid")
    assert payload["password"] == AUTH_SMOKE_PASSWORD


# --- Frontend -------------------------------------------------------------


def test_frontend_wrong_status_is_critical() -> None:
    responses = _ok_responses()
    responses[FRONTEND_URL] = [
        ProbeResponse(404, "text/html", b'<div id="root"></div>')
    ]

    result, _, _ = _run(responses)

    assert result.status == STATUS_ALERT
    assert result.severity == "critical"
    check = _check(result, PROBE_FRONTEND)
    assert check.error_kind == "status_mismatch"
    assert check.actual_status == 404


def test_frontend_wrong_content_type_is_body_mismatch() -> None:
    responses = _ok_responses()
    responses[FRONTEND_URL] = [
        ProbeResponse(200, "application/json", b'<div id="root"></div>')
    ]

    result, _, _ = _run(responses)

    assert result.severity == "critical"
    assert _check(result, PROBE_FRONTEND).error_kind == "body_mismatch"


def test_frontend_missing_root_marker_is_body_mismatch() -> None:
    responses = _ok_responses()
    responses[FRONTEND_URL] = [
        ProbeResponse(200, "text/html", b"<html><body>no marker</body></html>")
    ]

    result, _, _ = _run(responses)

    assert result.severity == "critical"
    assert _check(result, PROBE_FRONTEND).error_kind == "body_mismatch"


# --- Backend health -------------------------------------------------------


def test_backend_health_wrong_status_is_critical() -> None:
    responses = _ok_responses()
    responses[BACKEND_HEALTH_URL] = [
        ProbeResponse(503, "text/plain", b"unavailable"),
        ProbeResponse(503, "text/plain", b"unavailable"),
    ]

    result, _, _ = _run(responses)

    assert result.severity == "critical"
    check = _check(result, PROBE_BACKEND_HEALTH)
    assert check.error_kind == "status_mismatch"
    assert check.attempts == 2  # 5xx responses are retried once


def test_backend_health_wrong_body_is_body_mismatch() -> None:
    responses = _ok_responses()
    responses[BACKEND_HEALTH_URL] = [
        ProbeResponse(200, "application/json", b'{"status":"down"}')
    ]

    result, _, _ = _run(responses)

    assert result.severity == "critical"
    assert _check(result, PROBE_BACKEND_HEALTH).error_kind == "body_mismatch"


# --- Backend readiness ----------------------------------------------------


def test_backend_ready_503_is_high_severity() -> None:
    responses = _ok_responses()
    responses[BACKEND_READY_URL] = [
        ProbeResponse(503, "application/json", b'{"status":"unavailable"}'),
        ProbeResponse(503, "application/json", b'{"status":"unavailable"}'),
    ]

    result, _, _ = _run(responses)

    assert result.status == STATUS_ALERT
    assert result.severity == "high"
    check = _check(result, PROBE_BACKEND_READY)
    assert check.error_kind == "status_mismatch"
    assert check.attempts == 2


def test_backend_ready_wrong_body_is_body_mismatch() -> None:
    responses = _ok_responses()
    responses[BACKEND_READY_URL] = [
        ProbeResponse(200, "application/json", b'{"status":"ok"}')
    ]

    result, _, _ = _run(responses)

    assert result.severity == "high"
    assert _check(result, PROBE_BACKEND_READY).error_kind == "body_mismatch"


# --- Authentication smoke -------------------------------------------------


def test_auth_expected_401_is_success() -> None:
    result, _, _ = _run(_ok_responses())

    check = _check(result, PROBE_AUTH_SMOKE)
    assert check.passed is True
    assert check.actual_status == 401
    assert result.status == STATUS_HEALTHY


def test_auth_origin_rejection_is_configuration_error() -> None:
    responses = _ok_responses()
    responses[AUTH_SMOKE_URL] = [
        ProbeResponse(403, "application/json", b'{"error":{"code":"forbidden"}}')
    ]

    result, transport, _ = _run(responses)

    check = _check(result, PROBE_AUTH_SMOKE)
    assert check.error_kind == "configuration_error"
    # A configuration fault must not present as an authentication outage.
    assert result.severity == "medium"
    # Definitive 4xx responses are never retried.
    assert check.attempts == 1
    assert transport.call_count(AUTH_SMOKE_URL) == 1


def test_auth_validation_422_is_configuration_error() -> None:
    responses = _ok_responses()
    responses[AUTH_SMOKE_URL] = [
        ProbeResponse(422, "application/json", b'{"detail":[]}')
    ]

    result, transport, _ = _run(responses)

    check = _check(result, PROBE_AUTH_SMOKE)
    assert check.error_kind == "configuration_error"
    assert result.severity == "medium"
    assert transport.call_count(AUTH_SMOKE_URL) == 1


def test_auth_proxy_failure_is_proxy_error() -> None:
    responses = _ok_responses()
    responses[AUTH_SMOKE_URL] = [
        ProbeResponse(502, "text/plain", b"bad gateway"),
        ProbeResponse(502, "text/plain", b"bad gateway"),
    ]

    result, _, _ = _run(responses)

    check = _check(result, PROBE_AUTH_SMOKE)
    assert check.error_kind == "proxy_error"
    assert check.attempts == 2  # proxy failures are transient and retried
    assert result.status == STATUS_ALERT


def test_auth_unexpected_response_is_status_mismatch() -> None:
    responses = _ok_responses()
    responses[AUTH_SMOKE_URL] = [
        ProbeResponse(200, "application/json", b'{"token":"redacted"}')
    ]

    result, transport, _ = _run(responses)

    check = _check(result, PROBE_AUTH_SMOKE)
    assert check.error_kind == "status_mismatch"
    assert check.actual_status == 200
    assert transport.call_count(AUTH_SMOKE_URL) == 1


def test_auth_401_with_wrong_body_is_body_mismatch() -> None:
    responses = _ok_responses()
    responses[AUTH_SMOKE_URL] = [
        ProbeResponse(401, "application/json", b'{"error":{"code":"other"}}')
    ]

    result, _, _ = _run(responses)

    check = _check(result, PROBE_AUTH_SMOKE)
    assert check.error_kind == "body_mismatch"
    assert check.attempts == 1


# --- Retries --------------------------------------------------------------


def test_retry_after_transient_failure_then_success() -> None:
    responses = _ok_responses()
    responses[FRONTEND_URL] = [TransportError("timeout"), _FRONTEND_OK]

    result, transport, sleep = _run(responses)

    check = _check(result, PROBE_FRONTEND)
    assert check.passed is True
    assert check.attempts == 2
    assert transport.call_count(FRONTEND_URL) == 2
    assert sleep.delays == [5.0]
    assert result.status == STATUS_HEALTHY


def test_retry_exhausted_still_alerts() -> None:
    responses = _ok_responses()
    responses[FRONTEND_URL] = [
        TransportError("connection_error"),
        TransportError("connection_error"),
    ]

    result, transport, sleep = _run(responses)

    check = _check(result, PROBE_FRONTEND)
    assert check.passed is False
    assert check.error_kind == "connection_error"
    assert check.attempts == 2
    assert transport.call_count(FRONTEND_URL) == 2
    assert sleep.delays == [5.0]
    assert result.status == STATUS_ALERT
    assert result.severity == "critical"


def test_definitive_4xx_is_not_retried() -> None:
    responses = _ok_responses()
    responses[BACKEND_READY_URL] = [
        ProbeResponse(404, "application/json", b'{"status":"missing"}')
    ]

    result, transport, sleep = _run(responses)

    check = _check(result, PROBE_BACKEND_READY)
    assert check.attempts == 1
    assert transport.call_count(BACKEND_READY_URL) == 1
    assert sleep.delays == []


# --- Execution and output -------------------------------------------------


def test_all_probes_execute_when_one_fails() -> None:
    responses = _ok_responses()
    responses[FRONTEND_URL] = [
        ProbeResponse(404, "text/html", b"<html></html>")
    ]

    result, transport, _ = _run(responses)

    assert result.status == STATUS_ALERT
    assert len(result.checks) == 4
    for url in (
        FRONTEND_URL,
        BACKEND_HEALTH_URL,
        BACKEND_READY_URL,
        AUTH_SMOKE_URL,
    ):
        assert transport.call_count(url) == 1


def test_diagnostics_are_structured_and_allowlisted() -> None:
    result, _, _ = _run(_ok_responses())

    for check in result.checks:
        assert isinstance(check.diagnostics, tuple)
        for key, value in check.diagnostics:
            assert key in DIAGNOSTIC_KEYS
            assert value in _ALLOWED_BY_KEY[key]


def test_request_id_is_sanitized() -> None:
    responses = _ok_responses()
    responses[BACKEND_HEALTH_URL] = [
        ProbeResponse(
            200, "application/json", b'{"status":"ok"}', request_id="req-123"
        )
    ]
    responses[BACKEND_READY_URL] = [
        ProbeResponse(
            200,
            "application/json",
            b'{"status":"ready"}',
            request_id="not a valid request id",
        )
    ]

    result, _, _ = _run(responses)

    assert _check(result, PROBE_BACKEND_HEALTH).request_id == "req-123"
    assert _check(result, PROBE_BACKEND_READY).request_id is None


def test_output_does_not_leak_credentials_or_response_bodies(capsys) -> None:
    sentinel = "SECRET-RESPONSE-BODY"
    responses = _ok_responses()
    responses[AUTH_SMOKE_URL] = [
        ProbeResponse(
            401,
            "application/json",
            json.dumps(
                {
                    "error": {"code": "authentication_failed"},
                    "debug": sentinel,
                }
            ).encode("utf-8"),
        )
    ]

    transport = FakeTransport(responses)
    exit_code = main(transport=transport, sleep=RecordingSleep())
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert exit_code == 0
    assert sentinel not in combined
    assert AUTH_SMOKE_EMAIL not in combined
    assert AUTH_SMOKE_PASSWORD not in combined

    result = run_monitoring(
        transport=FakeTransport(responses), sleep=RecordingSleep()
    )
    assert sentinel not in repr(result)
    assert AUTH_SMOKE_EMAIL not in repr(result)
    assert AUTH_SMOKE_PASSWORD not in repr(result)


# --- Exit codes -----------------------------------------------------------


def test_main_success_exit_code() -> None:
    assert main(transport=FakeTransport(_ok_responses())) == 0


def test_main_alert_exit_code() -> None:
    responses = _ok_responses()
    responses[FRONTEND_URL] = [
        ProbeResponse(404, "text/html", b"<html></html>")
    ]

    assert main(transport=FakeTransport(responses)) == 1


def test_main_unexpected_failure_exit_code(capsys) -> None:
    class _BrokenTransport:
        def __call__(self, url: str, **kwargs):
            raise RuntimeError("boom-secret-detail")

    exit_code = main(transport=_BrokenTransport())
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "internal error" in captured.err
    assert "boom-secret-detail" not in captured.out + captured.err
