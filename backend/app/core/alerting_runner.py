"""Phase 2B synthetic probe runner for FinPath user-impact alerting.

This runner executes four production probes, classifies each outcome, and
hands the results to the pure evaluator in :mod:`app.core.alerting`. It runs
from a scheduled job; it is not imported by the application at runtime.

Probes:

- ``frontend``       GET  https://finpath-website.pages.dev/
- ``backend_health`` GET  https://finpath-website.onrender.com/health
- ``backend_ready``  GET  https://finpath-website.onrender.com/ready
- ``auth_smoke``     POST https://finpath-website.pages.dev/api/v1/auth/login

Safety:

- Only structured, allowlisted diagnostics are stored on a ``ProbeResult``.
- Response bodies, headers, cookies, credentials, and exception text are never
  logged or included in alert output.
- The authentication probe uses a reserved ``.invalid`` address and a dummy
  password; it never uses a real account.

Timeouts use only the standard library. ``http.client`` exposes a per-socket
operation timeout, not a single OS-level wall-clock timer. The runner therefore
derives a deadline from the total budget and adjusts the socket timeout to the
remaining time before each read, which bounds total probe time without adding a
dependency.

Exit codes:

- ``0`` -- evaluator reports ``healthy``.
- ``1`` -- evaluator reports ``alert``.
- ``2`` -- unexpected runner-level failure (reported without sensitive detail).
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import sys
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from app.core.alerting import (
    PROBE_AUTH_SMOKE,
    PROBE_BACKEND_HEALTH,
    PROBE_BACKEND_READY,
    PROBE_FRONTEND,
    STATUS_HEALTHY,
    MonitoringResult,
    ProbeResult,
    evaluate,
)

__all__ = [
    "AUTH_SMOKE_URL",
    "BACKEND_HEALTH_URL",
    "BACKEND_READY_URL",
    "FRONTEND_URL",
    "RUNNER_BUDGET_SECONDS",
    "ProbeOutcome",
    "ProbeResponse",
    "TransportError",
    "collect_probe_results",
    "main",
    "run_monitoring",
]

# --- Target endpoints -----------------------------------------------------

FRONTEND_URL = "https://finpath-website.pages.dev/"
BACKEND_HEALTH_URL = "https://finpath-website.onrender.com/health"
BACKEND_READY_URL = "https://finpath-website.onrender.com/ready"
AUTH_SMOKE_URL = "https://finpath-website.pages.dev/api/v1/auth/login"
FRONTEND_ORIGIN = "https://finpath-website.pages.dev"

FRONTEND_ROOT_MARKER = b'id="root"'

# Reserved, non-deliverable address plus a dummy password. Never a real
# account or credential.
AUTH_SMOKE_EMAIL = "alerting-probe@finpath.invalid"
AUTH_SMOKE_PASSWORD = "not-a-real-credential"

# --- Timeouts and retry policy -------------------------------------------

FRONTEND_CONNECT_TIMEOUT = 10.0
FRONTEND_TOTAL_TIMEOUT = 20.0
BACKEND_CONNECT_TIMEOUT = 10.0
BACKEND_TOTAL_TIMEOUT = 60.0

RETRY_DELAY_SECONDS = 5.0
MAX_ATTEMPTS = 2

# Keep the whole run bounded even if several probes time out.
RUNNER_BUDGET_SECONDS = 90.0

# Responses that may be transient. Definitive 4xx responses are never retried.
_RETRYABLE_ERROR_KINDS = frozenset(
    {"timeout", "connection_error", "malformed_body", "proxy_error"}
)

MAX_RESPONSE_BYTES = 1_048_576

EXIT_OK = 0
EXIT_ALERT = 1
EXIT_RUNNER_FAILURE = 2

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TransportError(Exception):
    """A classified transport failure carrying an allowlisted error kind."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class ProbeResponse:
    """The minimal, privacy-safe subset of an HTTP response the runner uses."""

    status: int
    content_type: str | None
    body: bytes
    request_id: str | None = None


@dataclass(frozen=True)
class ProbeOutcome:
    """A single probe attempt before it becomes a ``ProbeResult``."""

    passed: bool
    error_kind: str | None = None
    actual_status: int | None = None
    request_id: str | None = None


Transport = Callable[..., ProbeResponse]


# --- HTTP transport -------------------------------------------------------


def _set_remaining_timeout(connection: http.client.HTTPSConnection,
                           deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TransportError("timeout")
    if connection.sock is not None:
        connection.sock.settimeout(remaining)


def _read_body(
    response: http.client.HTTPResponse,
    connection: http.client.HTTPSConnection,
    deadline: float,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        _set_remaining_timeout(connection, deadline)
        chunk = response.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise TransportError("malformed_body")
        chunks.append(chunk)
    return b"".join(chunks)


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    connect_timeout: float,
    total_timeout: float,
) -> ProbeResponse:
    """Perform one HTTPS request with bounded timeouts and no redirects."""

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise TransportError("configuration_error")

    deadline = time.monotonic() + total_timeout
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443, timeout=connect_timeout
    )
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    request_headers = {
        "User-Agent": "FinPathAlerting/1.0",
        "Connection": "close",
    }
    if headers:
        request_headers.update(headers)

    try:
        connection.connect()
        _set_remaining_timeout(connection, deadline)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        try:
            response_body = _read_body(response, connection, deadline)
            return ProbeResponse(
                status=response.status,
                content_type=response.getheader("Content-Type"),
                body=response_body,
                request_id=response.getheader("X-Request-ID"),
            )
        finally:
            response.close()
    except TransportError:
        raise
    except (socket.timeout, TimeoutError) as exc:
        raise TransportError("timeout") from exc
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise TransportError("connection_error") from exc
    finally:
        connection.close()


# --- Probe implementations ------------------------------------------------


def _is_html(content_type: str | None) -> bool:
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower() == "text/html"


def _parse_json_object(body: bytes) -> dict | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _probe_frontend(
    transport: Transport, connect_timeout: float, total_timeout: float
) -> ProbeOutcome:
    try:
        response = transport(
            FRONTEND_URL,
            method="GET",
            headers={"Accept": "text/html"},
            body=None,
            connect_timeout=connect_timeout,
            total_timeout=total_timeout,
        )
    except TransportError as exc:
        return ProbeOutcome(passed=False, error_kind=exc.kind)

    if response.status != 200:
        return ProbeOutcome(
            passed=False,
            error_kind="status_mismatch",
            actual_status=response.status,
            request_id=response.request_id,
        )
    if not _is_html(response.content_type):
        return ProbeOutcome(
            passed=False,
            error_kind="body_mismatch",
            actual_status=response.status,
            request_id=response.request_id,
        )
    if FRONTEND_ROOT_MARKER not in response.body:
        return ProbeOutcome(
            passed=False,
            error_kind="body_mismatch",
            actual_status=response.status,
            request_id=response.request_id,
        )
    return ProbeOutcome(
        passed=True, actual_status=response.status, request_id=response.request_id
    )


def _probe_json_status(
    transport: Transport,
    url: str,
    expected_status_text: str,
    connect_timeout: float,
    total_timeout: float,
) -> ProbeOutcome:
    try:
        response = transport(
            url,
            method="GET",
            headers={"Accept": "application/json"},
            body=None,
            connect_timeout=connect_timeout,
            total_timeout=total_timeout,
        )
    except TransportError as exc:
        return ProbeOutcome(passed=False, error_kind=exc.kind)

    if response.status != 200:
        return ProbeOutcome(
            passed=False,
            error_kind="status_mismatch",
            actual_status=response.status,
            request_id=response.request_id,
        )
    payload = _parse_json_object(response.body)
    if payload is None:
        return ProbeOutcome(
            passed=False,
            error_kind="malformed_body",
            actual_status=response.status,
            request_id=response.request_id,
        )
    if payload.get("status") != expected_status_text:
        return ProbeOutcome(
            passed=False,
            error_kind="body_mismatch",
            actual_status=response.status,
            request_id=response.request_id,
        )
    return ProbeOutcome(
        passed=True, actual_status=response.status, request_id=response.request_id
    )


def _probe_backend_health(
    transport: Transport, connect_timeout: float, total_timeout: float
) -> ProbeOutcome:
    return _probe_json_status(
        transport,
        BACKEND_HEALTH_URL,
        "ok",
        connect_timeout,
        total_timeout,
    )


def _probe_backend_ready(
    transport: Transport, connect_timeout: float, total_timeout: float
) -> ProbeOutcome:
    return _probe_json_status(
        transport,
        BACKEND_READY_URL,
        "ready",
        connect_timeout,
        total_timeout,
    )


def _probe_auth(
    transport: Transport, connect_timeout: float, total_timeout: float
) -> ProbeOutcome:
    payload = json.dumps(
        {"email": AUTH_SMOKE_EMAIL, "password": AUTH_SMOKE_PASSWORD}
    ).encode("utf-8")
    try:
        response = transport(
            AUTH_SMOKE_URL,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": FRONTEND_ORIGIN,
            },
            body=payload,
            connect_timeout=connect_timeout,
            total_timeout=total_timeout,
        )
    except TransportError as exc:
        return ProbeOutcome(passed=False, error_kind=exc.kind)

    status = response.status
    if status == 401:
        body = _parse_json_object(response.body)
        if body is None:
            return ProbeOutcome(
                passed=False,
                error_kind="malformed_body",
                actual_status=status,
                request_id=response.request_id,
            )
        error = body.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if code == "authentication_failed":
            return ProbeOutcome(
                passed=True,
                actual_status=status,
                request_id=response.request_id,
            )
        return ProbeOutcome(
            passed=False,
            error_kind="body_mismatch",
            actual_status=status,
            request_id=response.request_id,
        )
    if status in (403, 422):
        # Origin rejection or request-validation failure: a probe or
        # configuration fault, not an authentication outage.
        return ProbeOutcome(
            passed=False,
            error_kind="configuration_error",
            actual_status=status,
            request_id=response.request_id,
        )
    if status in (502, 503):
        return ProbeOutcome(
            passed=False,
            error_kind="proxy_error",
            actual_status=status,
            request_id=response.request_id,
        )
    return ProbeOutcome(
        passed=False,
        error_kind="status_mismatch",
        actual_status=status,
        request_id=response.request_id,
    )


@dataclass(frozen=True)
class _ProbeSpec:
    name: str
    endpoint: str
    expected_status: int
    connect_timeout: float
    total_timeout: float
    probe: Callable[[Transport, float, float], ProbeOutcome]


_PROBES = (
    _ProbeSpec(
        PROBE_FRONTEND,
        "/",
        200,
        FRONTEND_CONNECT_TIMEOUT,
        FRONTEND_TOTAL_TIMEOUT,
        _probe_frontend,
    ),
    _ProbeSpec(
        PROBE_BACKEND_HEALTH,
        "/health",
        200,
        BACKEND_CONNECT_TIMEOUT,
        BACKEND_TOTAL_TIMEOUT,
        _probe_backend_health,
    ),
    _ProbeSpec(
        PROBE_BACKEND_READY,
        "/ready",
        200,
        BACKEND_CONNECT_TIMEOUT,
        BACKEND_TOTAL_TIMEOUT,
        _probe_backend_ready,
    ),
    _ProbeSpec(
        PROBE_AUTH_SMOKE,
        "/api/v1/auth/login",
        401,
        BACKEND_CONNECT_TIMEOUT,
        BACKEND_TOTAL_TIMEOUT,
        _probe_auth,
    ),
)


# --- Retry and result assembly -------------------------------------------


def _safe_request_id(value: str | None) -> str | None:
    if isinstance(value, str) and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return None


def _is_retryable(outcome: ProbeOutcome) -> bool:
    if outcome.passed:
        return False
    if outcome.actual_status is not None and outcome.actual_status >= 500:
        return True
    return outcome.error_kind in _RETRYABLE_ERROR_KINDS


def _run_probe(
    spec: _ProbeSpec,
    transport: Transport,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    deadline: float,
) -> ProbeResult:
    start = monotonic()
    attempts = 0
    outcome = ProbeOutcome(passed=False, error_kind="monitoring_error")

    while attempts < MAX_ATTEMPTS:
        if deadline - monotonic() <= 0:
            # Overall budget exhausted: report a monitoring fault rather than
            # an application outage.
            outcome = ProbeOutcome(passed=False, error_kind="monitoring_error")
            break
        attempts += 1
        remaining = deadline - monotonic()
        total_timeout = min(spec.total_timeout, max(remaining, 0.1))
        connect_timeout = min(spec.connect_timeout, total_timeout)
        outcome = spec.probe(transport, connect_timeout, total_timeout)

        if outcome.passed or not _is_retryable(outcome):
            break
        if attempts >= MAX_ATTEMPTS:
            break
        if deadline - monotonic() <= RETRY_DELAY_SECONDS:
            break
        sleep(RETRY_DELAY_SECONDS)

    latency_ms = max((monotonic() - start) * 1000.0, 0.0)
    return ProbeResult(
        name=spec.name,
        passed=outcome.passed,
        expected_status=spec.expected_status,
        actual_status=outcome.actual_status,
        error_kind=outcome.error_kind,
        attempts=attempts,
        latency_ms=latency_ms,
        request_id=_safe_request_id(outcome.request_id),
        diagnostics=(("endpoint", spec.endpoint),),
    )


def collect_probe_results(
    *,
    transport: Transport | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    budget: float = RUNNER_BUDGET_SECONDS,
) -> tuple[ProbeResult, ...]:
    """Run all four probes and return their results (never raises on failure)."""

    active_transport = transport if transport is not None else _http_request
    active_sleep = sleep if sleep is not None else time.sleep
    active_monotonic = monotonic if monotonic is not None else time.monotonic
    deadline = active_monotonic() + max(budget, 0.0)

    return tuple(
        _run_probe(
            spec,
            active_transport,
            active_sleep,
            active_monotonic,
            deadline,
        )
        for spec in _PROBES
    )


def run_monitoring(**kwargs: object) -> MonitoringResult:
    """Collect probe results and evaluate them into a monitoring decision."""

    return evaluate(collect_probe_results(**kwargs))


# --- CLI ------------------------------------------------------------------


def _print_report(result: MonitoringResult) -> None:
    print(f"status: {result.status}")
    print(f"severity: {result.severity}")
    failed = ", ".join(result.failed_probes) if result.failed_probes else "none"
    print(f"failed_probes: {failed}")
    for check in result.checks:
        if not check.passed:
            print(f"- {check.name}: {check.error_kind or 'unclassified'}")
    print(f"summary: {result.summary}")


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Transport | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> int:
    """Run monitoring and return a process exit code."""

    try:
        result = run_monitoring(
            transport=transport,
            sleep=sleep,
            monotonic=monotonic,
        )
    except Exception as exc:  # noqa: BLE001 - never leak internal detail
        print(
            f"alerting runner failed: internal error ({type(exc).__name__})",
            file=sys.stderr,
        )
        return EXIT_RUNNER_FAILURE

    _print_report(result)
    return EXIT_OK if result.status == STATUS_HEALTHY else EXIT_ALERT


if __name__ == "__main__":
    raise SystemExit(main())
