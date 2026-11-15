"""Unit tests for the pure alert evaluator (Phase 2A).

These tests require no network, database, Docker, environment secrets, or
external services. They construct :class:`ProbeResult` values directly and
assert on the evaluator's deterministic classification, canonical requiredness,
duplicate handling, structured diagnostics, and timestamp behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.alerting import (
    DIAGNOSTIC_COMPONENTS,
    DIAGNOSTIC_ENDPOINTS,
    DIAGNOSTIC_KEYS,
    DIAGNOSTIC_REASONS,
    DIAGNOSTIC_STATUS_CODES,
    DIAGNOSTIC_VALUE_MAX_LENGTH,
    ERROR_KINDS,
    INFRASTRUCTURE_ERROR_KINDS,
    PROBE_AUTH_SMOKE,
    PROBE_BACKEND_HEALTH,
    PROBE_BACKEND_READY,
    PROBE_FRONTEND,
    PROBE_METRICS_SCRAPE,
    PROBE_NAMES,
    REQUIRED_PROBE_NAMES,
    SEVERITIES,
    STATUS_ALERT,
    STATUS_HEALTHY,
    SUMMARY_MAX_LENGTH,
    MonitoringResult,
    ProbeResult,
    evaluate,
)


class _RaisingIterable:
    """An iterable that fails during iteration.

    This models an *unexpected* programming error, not malformed input, so the
    evaluator must let the exception propagate rather than swallow it.
    """

    def __iter__(self):  # noqa: D105 - deliberately raises
        raise RuntimeError("iteration failed")


class _RaisesDuringIteration:
    """An iterable whose iteration raises a chosen exception.

    ``iter(value)`` succeeds, but advancing the iterator raises. This
    distinguishes a failure to *obtain* an iterator (expected malformed input)
    from a failure *while iterating* (an unexpected programming error).
    """

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __iter__(self):
        return self

    def __next__(self):
        raise self._exc


class _TypeErrorOnIteratorCreation:
    """An object whose ``__iter__`` raises ``TypeError`` when called.

    Because it provides an iterator implementation, this is a broken custom
    iterator rather than a non-iterable, so the exception must propagate.
    """

    def __iter__(self):
        raise TypeError("broken __iter__")


_ALLOWED_BY_KEY = {
    "reason": DIAGNOSTIC_REASONS,
    "component": DIAGNOSTIC_COMPONENTS,
    "endpoint": DIAGNOSTIC_ENDPOINTS,
    "expected": DIAGNOSTIC_STATUS_CODES,
    "observed": DIAGNOSTIC_STATUS_CODES,
}


def _ok(name: str) -> ProbeResult:
    return ProbeResult(
        name=name,
        passed=True,
        expected_status=200,
        actual_status=200,
    )


def _fail(
    name: str,
    error_kind: str | None = None,
    *,
    required: bool = True,
    actual_status: int | None = None,
    diagnostics: tuple[tuple[str, str], ...] = (),
) -> ProbeResult:
    return ProbeResult(
        name=name,
        passed=False,
        required=required,
        expected_status=200,
        actual_status=actual_status,
        error_kind=error_kind,
        diagnostics=diagnostics,
    )


def _healthy_input() -> list[ProbeResult]:
    return [
        _ok(PROBE_FRONTEND),
        _ok(PROBE_BACKEND_HEALTH),
        _ok(PROBE_BACKEND_READY),
        _ok(PROBE_AUTH_SMOKE),
    ]


def _checks_for(result: MonitoringResult, name: str) -> list[ProbeResult]:
    return [check for check in result.checks if check.name == name]


def _check(result: MonitoringResult, name: str) -> ProbeResult:
    matches = _checks_for(result, name)
    assert matches, f"missing check for {name!r}"
    return matches[0]


# --- 1. Healthy -----------------------------------------------------------


def test_all_required_probes_pass_is_healthy() -> None:
    result = evaluate(_healthy_input())

    assert result.status == STATUS_HEALTHY
    assert result.severity == "none"
    assert result.failed_probes == ()
    assert result.checks
    assert "passed" in result.summary.lower()


# --- 2-5. Per-probe severity ----------------------------------------------


def test_frontend_failure_is_critical() -> None:
    probes = _healthy_input()
    probes[0] = _fail(PROBE_FRONTEND, "status_mismatch", actual_status=500)

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert result.severity == "critical"
    assert PROBE_FRONTEND in result.failed_probes


def test_backend_liveness_failure_is_critical() -> None:
    probes = _healthy_input()
    probes[1] = _fail(PROBE_BACKEND_HEALTH, "connection_error")

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert result.severity == "critical"
    assert PROBE_BACKEND_HEALTH in result.failed_probes


def test_database_readiness_failure_is_high() -> None:
    probes = _healthy_input()
    probes[2] = _fail(PROBE_BACKEND_READY, "status_mismatch", actual_status=503)

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert result.severity == "high"
    assert PROBE_BACKEND_READY in result.failed_probes


def test_authentication_failure_is_high() -> None:
    probes = _healthy_input()
    probes[3] = _fail(PROBE_AUTH_SMOKE, "body_mismatch", actual_status=200)

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert result.severity == "high"
    assert PROBE_AUTH_SMOKE in result.failed_probes


# --- 6. Optional probes ---------------------------------------------------


def test_optional_probe_failure_does_not_alert() -> None:
    probes = _healthy_input()
    probes.append(_fail(PROBE_METRICS_SCRAPE, "timeout"))

    result = evaluate(probes)

    assert result.status == STATUS_HEALTHY
    assert result.severity == "none"
    assert result.failed_probes == ()
    assert PROBE_METRICS_SCRAPE not in result.failed_probes
    # The optional failure is still recorded for diagnostics.
    assert _check(result, PROBE_METRICS_SCRAPE).passed is False
    assert "optional" in result.summary.lower()


# --- 7. Missing required probe --------------------------------------------


def test_missing_required_probe_triggers_alert() -> None:
    probes = [
        _ok(PROBE_FRONTEND),
        _ok(PROBE_BACKEND_HEALTH),
        _ok(PROBE_AUTH_SMOKE),
    ]  # PROBE_BACKEND_READY intentionally absent

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert PROBE_BACKEND_READY in result.failed_probes
    # A missing probe is a monitoring fault, never an application outage.
    assert result.severity == "medium"
    synthesized = _check(result, PROBE_BACKEND_READY)
    assert synthesized.passed is False
    assert synthesized.required is True
    assert synthesized.error_kind == "monitoring_error"
    assert "monitoring_error" in result.summary


# --- 8. Timeout classification --------------------------------------------


def test_timeout_is_classified() -> None:
    probes = _healthy_input()
    probes[1] = _fail(PROBE_BACKEND_HEALTH, "timeout")

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert _check(result, PROBE_BACKEND_HEALTH).error_kind == "timeout"
    assert "timeout" in result.summary


# --- 9-10. Infrastructure faults vs application failures ------------------


def test_configuration_error_is_not_an_application_outage() -> None:
    # A malformed probe Origin surfaces as configuration_error, not a genuine
    # authentication outage.
    probes = _healthy_input()
    probes[3] = _fail(
        PROBE_AUTH_SMOKE, "configuration_error", actual_status=403
    )

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert result.severity == "medium"
    assert result.severity != "high"
    assert PROBE_AUTH_SMOKE in result.failed_probes
    assert "configuration_error" in result.summary


def test_monitoring_error_is_not_an_application_outage() -> None:
    probes = _healthy_input()
    probes[1] = _fail(PROBE_BACKEND_HEALTH, "monitoring_error")

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert result.severity == "medium"
    assert result.severity != "critical"
    assert _check(result, PROBE_BACKEND_HEALTH).error_kind == "monitoring_error"


# --- 11. Highest severity wins --------------------------------------------


def test_multiple_failures_select_highest_severity() -> None:
    probes = _healthy_input()
    probes[0] = _fail(PROBE_FRONTEND, "status_mismatch", actual_status=500)
    probes[2] = _fail(PROBE_BACKEND_READY, "status_mismatch", actual_status=503)

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert result.severity == "critical"
    # Failed probes are ordered most severe first, then by name.
    assert result.failed_probes[0] == PROBE_FRONTEND
    assert set(result.failed_probes) == {PROBE_FRONTEND, PROBE_BACKEND_READY}


def test_highest_severity_ignores_lower_infrastructure_faults() -> None:
    probes = _healthy_input()
    probes[1] = _fail(PROBE_BACKEND_HEALTH, "connection_error")  # critical
    probes[3] = _fail(PROBE_AUTH_SMOKE, "configuration_error")  # medium

    result = evaluate(probes)

    assert result.severity == "critical"


# --- Correction 1: canonical requiredness ---------------------------------


def test_requiredness_is_canonical_not_caller_controlled() -> None:
    # A caller cannot demote a required probe to optional...
    for name in REQUIRED_PROBE_NAMES:
        assert ProbeResult(name=name, passed=True, required=False).required is True

    # ...and cannot promote the optional probe to required.
    assert (
        ProbeResult(name=PROBE_METRICS_SCRAPE, passed=True, required=True).required
        is False
    )


def test_caller_cannot_suppress_a_required_failure_by_marking_optional() -> None:
    probes = _healthy_input()
    probes[0] = _fail(PROBE_FRONTEND, "status_mismatch", required=False)

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert result.severity == "critical"
    assert PROBE_FRONTEND in result.failed_probes


def test_caller_cannot_force_optional_probe_to_alert() -> None:
    probes = _healthy_input()
    probes.append(_fail(PROBE_METRICS_SCRAPE, "timeout", required=True))

    result = evaluate(probes)

    assert result.status == STATUS_HEALTHY
    assert result.severity == "none"
    assert PROBE_METRICS_SCRAPE not in result.failed_probes


# --- Correction 3: duplicate probe detection ------------------------------


def test_duplicate_required_probe_is_medium_configuration_error() -> None:
    probes = _healthy_input()
    # Same required probe reported twice, once passing and once failing.
    probes.append(_ok(PROBE_FRONTEND))

    result = evaluate(probes)

    assert result.status == STATUS_ALERT
    assert result.severity == "medium"
    assert result.failed_probes == (PROBE_FRONTEND,)
    # No original result is retained; a single deterministic fault replaces it.
    checks = _checks_for(result, PROBE_FRONTEND)
    assert len(checks) == 1
    synthesized = checks[0]
    assert synthesized.passed is False
    assert synthesized.error_kind == "configuration_error"
    assert synthesized.diagnostics == (("reason", "duplicate_probe"),)


def test_duplicate_is_detected_regardless_of_pass_fail_order() -> None:
    passing = _ok(PROBE_FRONTEND)
    failing = _fail(PROBE_FRONTEND, "status_mismatch", actual_status=500)

    first = evaluate([*_healthy_input(), passing, failing])
    second = evaluate([*_healthy_input(), failing, passing])

    # Never silently select the passing or the failing result.
    assert first.status == STATUS_ALERT
    assert second.status == STATUS_ALERT
    assert first.severity == second.severity == "medium"
    assert first.failed_probes == second.failed_probes == (PROBE_FRONTEND,)


def test_duplicate_of_two_passing_results_is_still_a_fault() -> None:
    probes = _healthy_input()
    probes.append(_ok(PROBE_FRONTEND))  # both frontend results pass

    result = evaluate(probes)

    # A duplicate is a configuration fault even when every copy passed.
    assert result.status == STATUS_ALERT
    assert result.severity == "medium"
    assert result.failed_probes == (PROBE_FRONTEND,)


def test_duplicate_optional_probe_alerts_despite_being_optional() -> None:
    probes = _healthy_input()
    probes.append(_fail(PROBE_METRICS_SCRAPE, "timeout"))
    probes.append(_ok(PROBE_METRICS_SCRAPE))

    result = evaluate(probes)

    # Optional probes do not alert on failure, but an input-integrity fault
    # (a duplicate) always does.
    assert result.status == STATUS_ALERT
    assert result.severity == "medium"
    assert result.failed_probes == (PROBE_METRICS_SCRAPE,)
    synthesized = _check(result, PROBE_METRICS_SCRAPE)
    assert synthesized.error_kind == "configuration_error"
    assert synthesized.diagnostics == (("reason", "duplicate_probe"),)


# --- 12. Empty or invalid input -------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        (),
        "not-a-probe-list",
        b"bytes-are-not-probes",
        ("junk", 123, object()),
        object(),
        {"frontend": "not-a-probe"},
    ],
)
def test_expected_invalid_input_is_handled_safely(value: object) -> None:
    result = evaluate(value)  # type: ignore[arg-type]

    assert result.status == STATUS_ALERT
    assert result.severity == "medium"
    assert set(result.failed_probes) == set(REQUIRED_PROBE_NAMES)


def test_unexpected_iterator_failure_is_not_swallowed() -> None:
    # An iterator that raises is a programming error, not malformed input, so
    # the evaluator must surface it instead of degrading to "missing probes".
    with pytest.raises(RuntimeError):
        evaluate(_RaisingIterable())  # type: ignore[arg-type]


def test_unexpected_generator_error_is_not_swallowed() -> None:
    def _boom():
        yield _ok(PROBE_FRONTEND)
        raise ValueError("iterator bug")

    with pytest.raises(ValueError):
        evaluate(_boom())


def test_type_error_during_iteration_is_not_swallowed() -> None:
    # ``iter(value)`` succeeds here, so the TypeError comes from iteration and
    # is a programming error, not a non-iterable malformed input.
    with pytest.raises(TypeError):
        evaluate(_RaisesDuringIteration(TypeError("iteration bug")))


def test_non_iterable_input_is_treated_as_missing_probes() -> None:
    # A non-iterable is expected malformed input and must not raise.
    result = evaluate(object())  # type: ignore[arg-type]

    assert result.status == STATUS_ALERT
    assert set(result.failed_probes) == set(REQUIRED_PROBE_NAMES)


@pytest.mark.parametrize("value", [object(), 42, 3.14, True, complex(1, 2)])
def test_genuinely_non_iterable_input_is_handled_safely(value: object) -> None:
    # Objects that define neither __iter__ nor __getitem__ remain safe input.
    result = evaluate(value)  # type: ignore[arg-type]

    assert result.status == STATUS_ALERT
    assert result.severity == "medium"
    assert set(result.failed_probes) == set(REQUIRED_PROBE_NAMES)


def test_broken_custom_iterator_typeerror_propagates() -> None:
    # __iter__ exists but raises TypeError during iterator creation: this is a
    # broken custom iterator, not a non-iterable, so it must propagate.
    with pytest.raises(TypeError):
        evaluate(_TypeErrorOnIteratorCreation())  # type: ignore[arg-type]


def test_invalid_items_are_ignored_but_missing_probes_still_alert() -> None:
    result = evaluate([_ok(PROBE_FRONTEND), "junk", 42])

    assert result.status == STATUS_ALERT
    assert PROBE_FRONTEND not in result.failed_probes
    assert PROBE_BACKEND_HEALTH in result.failed_probes


def test_single_probe_result_is_accepted() -> None:
    result = evaluate(_ok(PROBE_FRONTEND))  # type: ignore[arg-type]

    assert result.status == STATUS_ALERT
    assert PROBE_FRONTEND not in result.failed_probes
    assert set(result.failed_probes) == set(REQUIRED_PROBE_NAMES) - {
        PROBE_FRONTEND
    }


# --- 13. Structured diagnostics and bounds --------------------------------


def test_diagnostics_are_structured_and_normalized() -> None:
    probe = ProbeResult(
        name=PROBE_FRONTEND,
        passed=False,
        error_kind="status_mismatch",
        diagnostics=(("observed", "500"), ("reason", "status_mismatch")),
    )

    # Stored as a sorted, immutable tuple of allowlisted tokens.
    assert probe.diagnostics == (
        ("observed", "500"),
        ("reason", "status_mismatch"),
    )


def test_allowlisted_diagnostic_values_are_accepted_for_every_key() -> None:
    for key, allowed in _ALLOWED_BY_KEY.items():
        value = sorted(allowed)[0]
        probe = ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=((key, value),),
        )
        assert probe.diagnostics == ((key, value),)


def test_diagnostic_values_are_key_specific() -> None:
    # A token that is valid for one key must be rejected for another key, which
    # proves the validation is per-key rather than a generic token pattern.
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=(("component", "status_mismatch"),),
        )
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=(("reason", "frontend"),),
        )
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=(("endpoint", "not-an-endpoint"),),
        )
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=(("expected", "not-a-status"),),
        )


def test_correct_auth_endpoint_diagnostic_is_accepted() -> None:
    probe = ProbeResult(
        name=PROBE_AUTH_SMOKE,
        passed=False,
        error_kind="status_mismatch",
        diagnostics=(("endpoint", "/api/v1/auth/login"),),
    )

    assert probe.diagnostics == (("endpoint", "/api/v1/auth/login"),)


def test_incorrect_auth_endpoint_diagnostic_is_rejected() -> None:
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_AUTH_SMOKE,
            passed=False,
            error_kind="status_mismatch",
            diagnostics=(("endpoint", "/api/auth/login"),),
        )


def test_secret_like_values_are_rejected_for_every_diagnostic_key() -> None:
    # These match a permissive "alphanumeric token" regex, so a generic pattern
    # check would have stored them. Strict per-key allowlists must reject them.
    secret_like_values = (
        "sk_live_51H8xQ2eZvKYlo2C",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "password123",
    )
    for key in sorted(DIAGNOSTIC_KEYS):
        for secret in secret_like_values:
            with pytest.raises(ValueError):
                ProbeResult(
                    name=PROBE_FRONTEND,
                    passed=False,
                    diagnostics=((key, secret),),
                )


def test_free_text_diagnostics_are_rejected() -> None:
    # Free-form text (spaces, punctuation, prose) is not accepted, so sensitive
    # content cannot be retained by accident.
    for value in (
        "user said password=top-secret",
        "raw response body: <html>",
        "a" * (DIAGNOSTIC_VALUE_MAX_LENGTH + 1),
    ):
        with pytest.raises(ValueError):
            ProbeResult(
                name=PROBE_FRONTEND,
                passed=False,
                error_kind="status_mismatch",
                diagnostics=(("reason", value),),
            )


def test_diagnostics_reject_unknown_keys_and_bad_shapes() -> None:
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=(("secret", "value"),),
        )
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=(("reason",),),  # not a pair
        )
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=(("reason", "timeout"), ("reason", "timeout")),
        )  # duplicate key
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics="reason=status_mismatch",  # free string
        )
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=(("reason", 500),),  # non-string value
        )


@pytest.mark.parametrize("value", [42, 3.14, object()])
def test_genuinely_non_iterable_diagnostics_raise_value_error(
    value: object,
) -> None:
    with pytest.raises(ValueError):
        ProbeResult(name=PROBE_FRONTEND, passed=False, diagnostics=value)


def test_type_error_during_diagnostics_iteration_propagates() -> None:
    # Obtaining the iterator succeeds; the TypeError comes from iteration and
    # must not be disguised as invalid diagnostic input (ValueError).
    with pytest.raises(TypeError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=_RaisesDuringIteration(TypeError("iteration bug")),
        )


def test_broken_custom_iterator_in_diagnostics_propagates() -> None:
    # __iter__ exists but raises TypeError during creation: a broken custom
    # iterator must propagate, not be treated as a non-iterable (ValueError).
    with pytest.raises(TypeError):
        ProbeResult(
            name=PROBE_FRONTEND,
            passed=False,
            diagnostics=_TypeErrorOnIteratorCreation(),
        )


def test_diagnostic_errors_do_not_expose_untrusted_content() -> None:
    secret_value = "AKIAIOSFODNN7EXAMPLE"
    secret_key = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
    rejected = (
        ((secret_key, "timeout"),),  # unknown key
        (("reason", secret_value),),  # value not allowlisted
        (("reason", 123456789),),  # non-string value
        (("reason", "x" * (DIAGNOSTIC_VALUE_MAX_LENGTH + 1)),),  # too long
        (("reason", "timeout"), ("reason", "timeout")),  # duplicate key
        ((secret_key,),),  # not a (key, value) pair
    )
    for diagnostics in rejected:
        with pytest.raises(ValueError) as excinfo:
            ProbeResult(
                name=PROBE_FRONTEND,
                passed=False,
                diagnostics=diagnostics,
            )
        message = str(excinfo.value)
        assert secret_value not in message
        assert secret_key not in message
        assert "123456789" not in message


def test_probe_result_has_no_free_text_detail_field() -> None:
    probe = _ok(PROBE_FRONTEND)
    assert not hasattr(probe, "detail")
    with pytest.raises(TypeError):
        ProbeResult(name=PROBE_FRONTEND, passed=True, detail="free text")


def test_summary_is_length_bounded() -> None:
    result = MonitoringResult(
        status=STATUS_ALERT,
        severity="high",
        failed_probes=(PROBE_AUTH_SMOKE,),
        summary="x" * (SUMMARY_MAX_LENGTH * 2),
    )

    assert len(result.summary) <= SUMMARY_MAX_LENGTH
    assert result.summary.endswith("...")


def test_request_id_is_treated_as_bounded_correlation_metadata() -> None:
    valid = ProbeResult(name=PROBE_FRONTEND, passed=True, request_id="abc-123")
    assert valid.request_id == "abc-123"

    spaced = ProbeResult(
        name=PROBE_FRONTEND, passed=True, request_id="bad id with spaces"
    )
    assert spaced.request_id is None

    oversized = ProbeResult(
        name=PROBE_FRONTEND, passed=True, request_id="a" * 300
    )
    assert oversized.request_id is None

    missing = ProbeResult(name=PROBE_FRONTEND, passed=True)
    assert missing.request_id is None


# --- 14. Timestamp --------------------------------------------------------


def test_timestamp_is_utc() -> None:
    result = evaluate(_healthy_input())

    assert result.timestamp.endswith("Z")
    parsed = datetime.fromisoformat(result.timestamp[:-1] + "+00:00")
    assert parsed.utcoffset() == timedelta(0)


def test_monitoring_result_rejects_non_utc_timestamp() -> None:
    with pytest.raises(ValueError):
        MonitoringResult(
            status=STATUS_ALERT,
            severity="high",
            failed_probes=(PROBE_AUTH_SMOKE,),
            timestamp="2026-01-01T00:00:00+05:00",
        )


# --- Data-structure validation --------------------------------------------


def test_probe_result_rejects_unknown_name() -> None:
    with pytest.raises(ValueError):
        ProbeResult(name="arbitrary_probe", passed=True)
    with pytest.raises(ValueError):
        ProbeResult(name="Bad Name", passed=True)


def test_probe_result_rejects_unknown_error_kind() -> None:
    with pytest.raises(ValueError):
        ProbeResult(
            name=PROBE_FRONTEND, passed=False, error_kind="not_a_kind"
        )


def test_probe_result_rejects_malformed_error_kind_types_safely() -> None:
    # Non-string, unhashable, and bytes error kinds must raise ValueError,
    # never TypeError from a set membership test.
    for bad_kind in (123, ["timeout"], {"timeout"}, b"timeout", object()):
        with pytest.raises(ValueError):
            ProbeResult(
                name=PROBE_FRONTEND, passed=False, error_kind=bad_kind
            )


def test_probe_result_rejects_invalid_status_and_latency() -> None:
    with pytest.raises(ValueError):
        ProbeResult(name=PROBE_FRONTEND, passed=True, actual_status=999)
    with pytest.raises(ValueError):
        ProbeResult(name=PROBE_FRONTEND, passed=True, latency_ms=-1)
    with pytest.raises(ValueError):
        ProbeResult(name=PROBE_FRONTEND, passed=True, latency_ms=float("inf"))


def test_monitoring_result_rejects_inconsistent_status() -> None:
    with pytest.raises(ValueError):
        MonitoringResult(status=STATUS_HEALTHY, severity="critical")
    with pytest.raises(ValueError):
        MonitoringResult(status=STATUS_ALERT, severity="none")
    with pytest.raises(ValueError):
        MonitoringResult(
            status=STATUS_ALERT,
            severity="high",
            failed_probes=(),
        )


# --- Vocabulary sanity ----------------------------------------------------


def test_severity_and_error_kind_vocabularies_are_bounded() -> None:
    assert SEVERITIES == ("none", "medium", "high", "critical")
    assert INFRASTRUCTURE_ERROR_KINDS <= ERROR_KINDS
    assert REQUIRED_PROBE_NAMES <= PROBE_NAMES
    assert PROBE_FRONTEND in REQUIRED_PROBE_NAMES
    assert PROBE_BACKEND_HEALTH in REQUIRED_PROBE_NAMES
    assert PROBE_BACKEND_READY in REQUIRED_PROBE_NAMES
    assert PROBE_AUTH_SMOKE in REQUIRED_PROBE_NAMES
    assert PROBE_METRICS_SCRAPE not in REQUIRED_PROBE_NAMES


def test_diagnostic_keys_and_allowlists_are_bounded() -> None:
    assert DIAGNOSTIC_KEYS == {
        "reason",
        "component",
        "endpoint",
        "expected",
        "observed",
    }
    for allowed in _ALLOWED_BY_KEY.values():
        assert allowed, "each diagnostic key must have a non-empty allowlist"
        assert all(isinstance(token, str) for token in allowed)
    assert DIAGNOSTIC_VALUE_MAX_LENGTH > 0
    # The evaluator's own synthesized reasons must be allowlisted.
    assert "duplicate_probe" in DIAGNOSTIC_REASONS
    assert "missing_required_probe" in DIAGNOSTIC_REASONS
