"""Pure, dependency-free alert evaluation for user-impact monitoring.

This module converts structured synthetic-probe results into a single
monitoring decision. It performs **no I/O**: no network, database, filesystem,
environment, or clock access beyond generating a UTC timestamp. It is the
Phase 2A evaluator only; probe execution and workflow wiring are separate,
later phases.

Design goals:

- **Canonical requiredness.** Whether a probe is required is decided solely by
  its name via :data:`REQUIRED_PROBE_NAMES`. A caller cannot demote a required
  probe to optional, and ``metrics_scrape`` is always optional.
- **Deterministic classification.** Severity and status depend only on the
  supplied probe results, never on wall-clock time or ambient state.
- **Bounded vocabulary.** Only declared probe names, error kinds, diagnostic
  keys, and diagnostic value tokens are accepted, so no arbitrary or
  user-provided value can enter a result.
- **No false success.** A required probe that is absent is treated as a
  monitoring failure, never as a pass.
- **No free-text diagnostics.** Diagnostic content is limited to a fixed key
  vocabulary, and each key accepts only a strict, per-key allowlist of machine
  tokens. A value is never accepted merely because it matches a character
  pattern, so a secret-like alphanumeric string cannot be stored as a
  diagnostic. The evaluator never accepts free-form text, response bodies,
  credentials, cookies, or authorization headers, and it makes no claim that
  character filtering equals secret redaction.
- **Duplicate detection.** If the same probe name is reported more than once,
  the evaluator does not pick a winner; it records a deterministic
  monitoring/configuration fault instead.

Severity model:

- ``critical`` — frontend availability or backend liveness failed.
- ``high`` — database readiness or authentication smoke failed.
- ``medium`` — explicitly supported degraded conditions: monitoring or
  configuration faults (missing or duplicated probe results, or an
  infrastructure error kind), which must not be reported as an application
  outage.
- ``none`` — every required probe passed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Iterator

__all__ = [
    "DIAGNOSTIC_COMPONENTS",
    "DIAGNOSTIC_ENDPOINTS",
    "DIAGNOSTIC_KEYS",
    "DIAGNOSTIC_REASONS",
    "DIAGNOSTIC_STATUS_CODES",
    "DIAGNOSTIC_VALUE_MAX_LENGTH",
    "ERROR_KINDS",
    "INFRASTRUCTURE_ERROR_KINDS",
    "PROBE_AUTH_SMOKE",
    "PROBE_BACKEND_HEALTH",
    "PROBE_BACKEND_READY",
    "PROBE_FRONTEND",
    "PROBE_METRICS_SCRAPE",
    "PROBE_NAMES",
    "REQUIRED_PROBE_NAMES",
    "SEVERITIES",
    "STATUS_ALERT",
    "STATUS_HEALTHY",
    "SUMMARY_MAX_LENGTH",
    "MonitoringResult",
    "ProbeResult",
    "evaluate",
]

# --- Bounded vocabularies -------------------------------------------------

# Required probes. A missing required result is a monitoring failure, never a
# pass, because absence cannot prove the probe succeeded. Requiredness is
# canonical: it is derived from this set, not supplied by the caller.
PROBE_FRONTEND = "frontend"
PROBE_BACKEND_HEALTH = "backend_health"
PROBE_BACKEND_READY = "backend_ready"
PROBE_AUTH_SMOKE = "auth_smoke"

# Optional probes. Failures here are recorded but never trigger an alert on
# their own.
PROBE_METRICS_SCRAPE = "metrics_scrape"

PROBE_NAMES = frozenset(
    {
        PROBE_FRONTEND,
        PROBE_BACKEND_HEALTH,
        PROBE_BACKEND_READY,
        PROBE_AUTH_SMOKE,
        PROBE_METRICS_SCRAPE,
    }
)

REQUIRED_PROBE_NAMES = frozenset(
    {
        PROBE_FRONTEND,
        PROBE_BACKEND_HEALTH,
        PROBE_BACKEND_READY,
        PROBE_AUTH_SMOKE,
    }
)

ERROR_KINDS = frozenset(
    {
        "timeout",
        "connection_error",
        "status_mismatch",
        "body_mismatch",
        "malformed_body",
        "proxy_error",
        "configuration_error",
        "monitoring_error",
    }
)

# Faults in the monitoring or configuration path itself. These are real
# problems worth surfacing, but they must not be reported as an application
# outage, so they are capped at ``medium`` regardless of the probe name.
INFRASTRUCTURE_ERROR_KINDS = frozenset(
    {"configuration_error", "monitoring_error"}
)

# Structured diagnostics use a fixed key vocabulary, and each key accepts only
# a strict, per-key allowlist of machine tokens. A value is never accepted
# merely because it matches a character pattern, so a secret-like alphanumeric
# string (for example an API key or a JWT) cannot be stored as a diagnostic.
DIAGNOSTIC_REASONS = frozenset(
    {
        "duplicate_probe",
        "missing_required_probe",
        "timeout",
        "connection_error",
        "status_mismatch",
        "body_mismatch",
        "malformed_body",
        "proxy_error",
        "configuration_error",
        "monitoring_error",
    }
)
DIAGNOSTIC_COMPONENTS = frozenset(
    {
        "frontend",
        "backend",
        "database",
        "auth",
        "proxy",
        "monitoring",
    }
)
DIAGNOSTIC_ENDPOINTS = frozenset(
    {
        "/",
        "/health",
        "/ready",
        "/metrics",
        "/api/v1/auth/login",
    }
)
DIAGNOSTIC_STATUS_CODES = frozenset(
    {
        "200",
        "201",
        "204",
        "301",
        "302",
        "400",
        "401",
        "403",
        "404",
        "405",
        "409",
        "422",
        "429",
        "500",
        "502",
        "503",
        "504",
    }
)

_DIAGNOSTIC_ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "reason": DIAGNOSTIC_REASONS,
    "component": DIAGNOSTIC_COMPONENTS,
    "endpoint": DIAGNOSTIC_ENDPOINTS,
    "expected": DIAGNOSTIC_STATUS_CODES,
    "observed": DIAGNOSTIC_STATUS_CODES,
}

DIAGNOSTIC_KEYS = frozenset(_DIAGNOSTIC_ALLOWED_VALUES)

STATUS_HEALTHY = "healthy"
STATUS_ALERT = "alert"
STATUSES = frozenset({STATUS_HEALTHY, STATUS_ALERT})

SEVERITIES = ("none", "medium", "high", "critical")
_SEVERITY_RANK = {"none": 0, "medium": 1, "high": 2, "critical": 3}

# Application-failure severity by probe name. Unknown names fall back to
# ``medium`` (a supported degraded condition), never to ``none``.
_PROBE_SEVERITY = {
    PROBE_FRONTEND: "critical",
    PROBE_BACKEND_HEALTH: "critical",
    PROBE_BACKEND_READY: "high",
    PROBE_AUTH_SMOKE: "high",
}

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Same contract the backend applies to X-Request-ID values.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

DIAGNOSTIC_VALUE_MAX_LENGTH = 128
SUMMARY_MAX_LENGTH = 500


# --- Helpers --------------------------------------------------------------


def _utc_now() -> str:
    """Return the current time as a UTC ISO-8601 string ending in ``Z``."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() == timezone.utc.utcoffset(None)


def _truncate(value: str, *, max_length: int) -> str:
    """Bound an internal string; this is length limiting, not redaction."""

    if len(value) > max_length:
        keep = max(max_length - 3, 0)
        return value[:keep].rstrip() + "..."
    return value


def _sanitize_request_id(value: object) -> str | None:
    """Return a bounded correlation ID, or ``None`` when unusable."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _REQUEST_ID_PATTERN.fullmatch(candidate):
        return None
    return candidate


def _iter_maybe(value: object) -> Iterator[object] | None:
    """Return an iterator for ``value``, or ``None`` if it is not iterable.

    A genuinely non-iterable object -- one that defines neither ``__iter__``
    nor ``__getitem__`` -- is expected malformed input, so ``None`` is returned
    and the caller handles it safely. An object that *does* provide an iterator
    implementation is trusted to work: if obtaining its iterator raises
    (including ``TypeError``), that is a broken custom iterator and the
    exception propagates rather than being mistaken for a non-iterable.
    """

    value_type = type(value)
    if not (
        hasattr(value_type, "__iter__") or hasattr(value_type, "__getitem__")
    ):
        return None
    return iter(value)  # type: ignore[call-overload]


def _normalize_diagnostics(value: object) -> tuple[tuple[str, str], ...]:
    """Validate structured diagnostics into a sorted, unique tuple.

    Keys must be in :data:`DIAGNOSTIC_KEYS`, and each value must appear in that
    key's strict allowlist. Anything else (including free text and secret-like
    alphanumeric strings) is rejected rather than stored, so sensitive content
    cannot be retained by accident.
    """

    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)):
        raise ValueError(
            "diagnostics must be a sequence of (key, value) pairs."
        )
    iterator = _iter_maybe(value)
    if iterator is None:
        raise ValueError(
            "diagnostics must be a sequence of (key, value) pairs."
        )
    # Materialize outside any ``try``: an exception raised while producing
    # items is a programming error and must propagate, not be disguised as
    # invalid diagnostic input.
    items = list(iterator)

    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("Each diagnostic must be a (key, value) pair.")
        key, diagnostic_value = item
        # Error messages deliberately omit the rejected key and value so that
        # untrusted or secret-like content is never echoed back.
        if not isinstance(key, str) or key not in _DIAGNOSTIC_ALLOWED_VALUES:
            raise ValueError("Unknown diagnostic key.")
        if not isinstance(diagnostic_value, str):
            raise ValueError("Diagnostic value must be a string.")
        if len(diagnostic_value) > DIAGNOSTIC_VALUE_MAX_LENGTH:
            raise ValueError("Diagnostic value exceeds the maximum length.")
        if diagnostic_value not in _DIAGNOSTIC_ALLOWED_VALUES[key]:
            raise ValueError("Diagnostic value is not an allowed token.")
        if key in seen:
            raise ValueError("Duplicate diagnostic key.")
        seen.add(key)
        normalized.append((key, diagnostic_value))
    return tuple(sorted(normalized))


def _validate_http_status(value: object, *, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an HTTP status code or None.")
    if not 100 <= value <= 599:
        raise ValueError(f"{field_name} must be between 100 and 599.")


def _severity_for(result: "ProbeResult") -> str:
    """Return the severity contributed by a single failed probe result."""

    if result.error_kind in INFRASTRUCTURE_ERROR_KINDS:
        return "medium"
    return _PROBE_SEVERITY.get(result.name, "medium")


# --- Data structures ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One synthetic-probe outcome.

    ``required`` is accepted for interface compatibility but is always
    replaced by the canonical value for ``name``: required for the probes in
    :data:`REQUIRED_PROBE_NAMES`, optional otherwise. ``diagnostics`` is a
    bounded, structured key/value tuple; free-text detail is not supported.
    """

    name: str
    passed: bool
    required: bool = True
    expected_status: int | None = None
    actual_status: int | None = None
    error_kind: str | None = None
    attempts: int = 1
    latency_ms: float | None = None
    request_id: str | None = None
    diagnostics: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_PATTERN.fullmatch(
            self.name
        ):
            raise ValueError(f"Invalid probe name: {self.name!r}")
        if self.name not in PROBE_NAMES:
            raise ValueError(f"Unknown probe name: {self.name!r}")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a bool.")
        if not isinstance(self.required, bool):
            raise ValueError("required must be a bool.")

        # Canonical requiredness: the caller cannot change it.
        object.__setattr__(
            self,
            "required",
            self.name in REQUIRED_PROBE_NAMES,
        )

        _validate_http_status(self.expected_status, field_name="expected_status")
        _validate_http_status(self.actual_status, field_name="actual_status")

        if self.error_kind is not None:
            if (
                not isinstance(self.error_kind, str)
                or self.error_kind not in ERROR_KINDS
            ):
                raise ValueError(f"Unknown error_kind: {self.error_kind!r}")

        if (
            isinstance(self.attempts, bool)
            or not isinstance(self.attempts, int)
            or self.attempts < 0
        ):
            raise ValueError("attempts must be a non-negative integer.")

        if self.latency_ms is not None:
            if (
                isinstance(self.latency_ms, bool)
                or not isinstance(self.latency_ms, (int, float))
                or not math.isfinite(float(self.latency_ms))
                or self.latency_ms < 0
            ):
                raise ValueError(
                    "latency_ms must be a finite, non-negative number or None."
                )

        if self.request_id is not None:
            object.__setattr__(
                self,
                "request_id",
                _sanitize_request_id(self.request_id),
            )

        object.__setattr__(
            self,
            "diagnostics",
            _normalize_diagnostics(self.diagnostics),
        )


@dataclass(frozen=True, slots=True)
class MonitoringResult:
    """The overall monitoring decision produced by :func:`evaluate`.

    ``summary`` is a human-readable description produced by :func:`evaluate`
    from allowlisted probe names and error kinds. It is only length-bounded
    (:data:`SUMMARY_MAX_LENGTH`); it is **not** sanitized, escaped, or
    secret-redacted. This class performs no redaction of arbitrary text, so a
    caller that constructs a ``MonitoringResult`` directly, or otherwise
    supplies its own summary, must never place credentials, tokens, cookies,
    headers, response bodies, or other sensitive content in ``summary``.
    """

    status: str
    severity: str
    failed_probes: tuple[str, ...] = ()
    summary: str = ""
    checks: tuple[ProbeResult, ...] = ()
    timestamp: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "failed_probes", tuple(self.failed_probes))
        object.__setattr__(self, "checks", tuple(self.checks))

        if self.status not in STATUSES:
            raise ValueError(f"Unknown status: {self.status!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"Unknown severity: {self.severity!r}")

        if self.status == STATUS_HEALTHY and self.severity != "none":
            raise ValueError("A healthy result must have severity 'none'.")
        if self.status == STATUS_ALERT and self.severity == "none":
            raise ValueError("An alert result must have a non-'none' severity.")

        if any(
            not isinstance(name, str) or name not in PROBE_NAMES
            for name in self.failed_probes
        ):
            raise ValueError(
                "failed_probes must contain only known probe names."
            )
        if self.status == STATUS_HEALTHY and self.failed_probes:
            raise ValueError("A healthy result cannot list failed probes.")
        if self.status == STATUS_ALERT and not self.failed_probes:
            raise ValueError("An alert result must list at least one probe.")

        if any(
            not isinstance(check, ProbeResult) for check in self.checks
        ):
            raise ValueError("checks must contain only ProbeResult values.")

        if not _is_utc_timestamp(self.timestamp):
            raise ValueError(
                "timestamp must be a UTC ISO-8601 string ending in 'Z'."
            )

        if not isinstance(self.summary, str):
            raise ValueError("summary must be a string.")
        object.__setattr__(
            self,
            "summary",
            _truncate(self.summary, max_length=SUMMARY_MAX_LENGTH),
        )


# --- Evaluation -----------------------------------------------------------


def _coerce_results(value: object) -> tuple[ProbeResult, ...]:
    """Return only well-formed :class:`ProbeResult` items from ``value``.

    Expected malformed input is handled safely: ``None``, strings, bytes, and
    other non-iterables are treated as no input, and any item that is not a
    :class:`ProbeResult` is ignored. Invalid input therefore degrades to
    "missing probes" rather than raising.

    Only a genuinely non-iterable object is handled. An object that provides an
    iterator implementation is trusted to work: any exception it raises --
    including while the iterator is being created -- is a programming error in
    the caller, so it propagates instead of being silently swallowed.
    """

    if value is None or isinstance(value, (str, bytes, bytearray)):
        return ()
    if isinstance(value, ProbeResult):
        return (value,)
    iterator = _iter_maybe(value)
    if iterator is None:
        # Not iterable: expected malformed input, handled as no input.
        return ()
    # Materialize outside any ``try``: an exception raised while producing
    # items (including ``TypeError``) is a programming error, not malformed
    # input, and must propagate instead of being silently swallowed.
    return tuple(item for item in iterator if isinstance(item, ProbeResult))


def evaluate(probe_results: Iterable[ProbeResult] | None) -> MonitoringResult:
    """Evaluate probe results into a single monitoring decision.

    Required probes are :data:`REQUIRED_PROBE_NAMES`. Any required failure, a
    missing required probe, or a duplicated probe name produces
    ``status == "alert"``. Optional probe failures are recorded in ``checks``
    but never trigger an alert on their own.
    """

    checks = _coerce_results(probe_results)

    counts: dict[str, int] = {}
    for check in checks:
        counts[check.name] = counts.get(check.name, 0) + 1
    duplicated = tuple(
        sorted(name for name, count in counts.items() if count > 1)
    )

    # Duplicate names are ambiguous, so no original result is kept: a single
    # deterministic monitoring/configuration fault replaces them.
    by_name: dict[str, ProbeResult] = {}
    for check in checks:
        if check.name in duplicated:
            continue
        by_name.setdefault(check.name, check)

    duplicate_checks = tuple(
        ProbeResult(
            name=name,
            passed=False,
            error_kind="configuration_error",
            diagnostics=(("reason", "duplicate_probe"),),
        )
        for name in duplicated
    )

    provided_names = set(by_name) | set(duplicated)
    missing = tuple(sorted(REQUIRED_PROBE_NAMES - provided_names))
    missing_checks = tuple(
        ProbeResult(
            name=name,
            passed=False,
            error_kind="monitoring_error",
            diagnostics=(("reason", "missing_required_probe"),),
        )
        for name in missing
    )

    all_checks = tuple(
        sorted(
            (*by_name.values(), *missing_checks, *duplicate_checks),
            key=lambda check: check.name,
        )
    )

    required_failures = tuple(
        check for check in all_checks if check.required and not check.passed
    )
    # A duplicated optional probe is still an input-integrity fault, so it is
    # alert-worthy even though optional probe failures normally are not.
    optional_duplicate_failures = tuple(
        check for check in duplicate_checks if not check.required
    )
    alerting = (*required_failures, *optional_duplicate_failures)

    if not alerting:
        optional_failed = sum(
            1
            for check in all_checks
            if not check.required and not check.passed
        )
        summary = "All required probes passed."
        if optional_failed:
            summary += f" {optional_failed} optional probe(s) failed."
        return MonitoringResult(
            status=STATUS_HEALTHY,
            severity="none",
            failed_probes=(),
            summary=summary,
            checks=all_checks,
        )

    ordered = tuple(
        sorted(
            alerting,
            key=lambda check: (
                -_SEVERITY_RANK[_severity_for(check)],
                check.name,
            ),
        )
    )
    severity = max(
        (_severity_for(check) for check in ordered),
        key=lambda name: _SEVERITY_RANK[name],
    )
    failed_probes = tuple(dict.fromkeys(check.name for check in ordered))
    described = ", ".join(
        f"{check.name} ({check.error_kind or 'unclassified'})"
        for check in ordered
    )
    summary = (
        f"{severity.upper()} alert: {len(failed_probes)} probe(s) failed: "
        f"{described}."
    )
    return MonitoringResult(
        status=STATUS_ALERT,
        severity=severity,
        failed_probes=failed_probes,
        summary=summary,
        checks=all_checks,
    )
