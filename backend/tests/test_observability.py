"""Tests for structured request logging and request correlation IDs."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.errors import ApiError, api_error_handler, validation_error_handler
from app.core.config import settings
from app.core.observability import (
    AUTH_LOGIN_FAILURE_REASONS,
    DOMAIN_EVENT_NAMES,
    REQUEST_ID_HEADER,
    REQUEST_ID_MAX_LENGTH,
    REQUEST_ID_PATTERN,
    JsonFormatter,
    RequestContextFilter,
    RequestContextMiddleware,
    log_domain_event,
    reset_request_id,
    set_request_id,
)
from app.main import app


def _error_app() -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(RequestContextMiddleware)
    test_app.add_exception_handler(ApiError, api_error_handler)
    test_app.add_exception_handler(RequestValidationError, validation_error_handler)

    @test_app.get("/forbidden")
    def forbidden() -> None:
        raise ApiError(403, "forbidden", "The request is not allowed.")

    @test_app.get("/boom")
    def boom() -> None:
        raise RuntimeError("top-secret internal failure detail")

    @test_app.get("/validate")
    def validate(q: int) -> dict[str, int]:
        return {"q": q}

    return test_app


async def _get(
    target_app: FastAPI,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=target_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.get(path, headers=headers)


def _request_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None)
        in {"http_request_completed", "http_request_failed"}
    ]


def _single_request_record(
    caplog: pytest.LogCaptureFixture,
) -> logging.LogRecord:
    records = _request_records(caplog)
    assert len(records) == 1
    return records[0]


def test_missing_request_id_is_generated_and_returned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        response = asyncio.run(_get(app, "/health"))

    request_id = response.headers[REQUEST_ID_HEADER]
    assert response.status_code == 200
    assert REQUEST_ID_PATTERN.fullmatch(request_id)
    assert len(request_id) <= REQUEST_ID_MAX_LENGTH
    assert _single_request_record(caplog).request_id == request_id


def test_valid_request_id_is_propagated() -> None:
    response = asyncio.run(
        _get(
            app,
            "/health",
            headers={REQUEST_ID_HEADER: "client-request-123"},
        )
    )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "client-request-123"


@pytest.mark.parametrize(
    "incoming",
    [
        "contains a space",
        "<script>alert(1)</script>",
        "semicolon;value",
        "-leading-hyphen",
        "a" * (REQUEST_ID_MAX_LENGTH + 1),
    ],
)
def test_malformed_or_oversized_request_ids_are_replaced(
    incoming: str,
) -> None:
    response = asyncio.run(
        _get(app, "/health", headers={REQUEST_ID_HEADER: incoming})
    )

    generated = response.headers[REQUEST_ID_HEADER]
    assert response.status_code == 200
    assert generated != incoming
    assert REQUEST_ID_PATTERN.fullmatch(generated)
    assert len(generated) <= REQUEST_ID_MAX_LENGTH


def test_completed_request_is_logged_with_structured_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        response = asyncio.run(_get(app, "/health"))

    record = _single_request_record(caplog)
    assert record.event == "http_request_completed"
    assert record.method == "GET"
    assert record.route == "/health"
    assert record.status_code == 200
    assert record.duration_ms >= 0
    assert record.slow is False
    assert record.request_id == response.headers[REQUEST_ID_HEADER]


def test_slow_request_is_logged_at_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings, "slow_request_threshold_ms", 0)

    with caplog.at_level(logging.INFO):
        asyncio.run(_get(app, "/health"))

    record = _single_request_record(caplog)
    assert record.levelno == logging.WARNING
    assert record.slow is True
    assert record.duration_ms >= 0


def test_4xx_response_is_logged_without_server_error_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        response = asyncio.run(
            _get(
                _error_app(),
                "/forbidden",
                headers={REQUEST_ID_HEADER: "forbidden-request-1"},
            )
        )

    record = _single_request_record(caplog)
    assert response.status_code == 403
    assert response.headers[REQUEST_ID_HEADER] == "forbidden-request-1"
    assert response.json()["error"]["request_id"] == "forbidden-request-1"
    assert record.status_code == 403
    assert record.levelno == logging.INFO
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_validation_error_keeps_request_id_and_is_not_a_server_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        response = asyncio.run(
            _get(
                _error_app(),
                "/validate?q=not-an-integer",
                headers={REQUEST_ID_HEADER: "validation-request-1"},
            )
        )

    record = _single_request_record(caplog)
    assert response.status_code == 422
    assert response.headers[REQUEST_ID_HEADER] == "validation-request-1"
    assert response.json()["error"]["request_id"] == "validation-request-1"
    assert record.status_code == 422
    assert record.levelno == logging.INFO
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_unhandled_error_returns_safe_500_and_logs_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        response = asyncio.run(
            _get(
                _error_app(),
                "/boom",
                headers={REQUEST_ID_HEADER: "boom-request-1"},
            )
        )

    record = _single_request_record(caplog)
    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "boom-request-1"
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected server error occurred.",
            "field_errors": [],
            "request_id": "boom-request-1",
        }
    }
    assert "top-secret" not in response.text
    assert record.event == "http_request_failed"
    assert record.levelno == logging.ERROR
    assert record.error_type == "RuntimeError"
    assert record.exc_info is not None


def test_formatted_log_record_excludes_headers_query_and_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        response = asyncio.run(
            _get(
                app,
                "/health?token=top-secret-query-value",
                headers={
                    "Authorization": "Bearer top-secret-access-token",
                    "Cookie": "finpath_refresh=top-secret-cookie",
                },
            )
        )

    record = _single_request_record(caplog)
    formatted = JsonFormatter().format(record)
    assert response.status_code == 200
    assert "top-secret" not in formatted
    assert "Authorization" not in formatted
    assert "Cookie" not in formatted
    assert "/health" in formatted


def _domain_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None) in DOMAIN_EVENT_NAMES
    ]


def test_auth_login_failure_reason_vocabulary_is_fixed() -> None:
    assert AUTH_LOGIN_FAILURE_REASONS == frozenset(
        {
            "invalid_credentials",
            "email_verification_required",
            "account_suspended",
            "account_closed",
        }
    )


@pytest.mark.parametrize("event", sorted(DOMAIN_EVENT_NAMES))
def test_log_domain_event_emits_every_approved_event_name(
    event: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    kwargs = (
        {"reason": "invalid_credentials"}
        if event == "auth_login_failed"
        else {}
    )

    with caplog.at_level(logging.INFO):
        log_domain_event(event, **kwargs)

    records = _domain_records(caplog)
    assert len(records) == 1
    assert records[0].event == event
    assert records[0].getMessage() == "Domain event recorded."


def test_log_domain_event_rejects_unapproved_event_name() -> None:
    with pytest.raises(ValueError):
        log_domain_event("not_an_approved_event")


def test_log_domain_event_rejects_unapproved_reason() -> None:
    with pytest.raises(ValueError):
        log_domain_event("auth_login_failed", reason="unknown_email")


def test_log_domain_event_requires_reason_for_login_failure() -> None:
    with pytest.raises(ValueError):
        log_domain_event("auth_login_failed")


def test_log_domain_event_rejects_reason_on_other_events() -> None:
    with pytest.raises(ValueError):
        log_domain_event("report_exported", reason="invalid_credentials")


def test_login_failure_reason_is_identical_for_both_credential_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Unknown email and incorrect password must be indistinguishable in logs.
    with caplog.at_level(logging.WARNING):
        log_domain_event(
            "auth_login_failed",
            level=logging.WARNING,
            reason="invalid_credentials",
        )
        log_domain_event(
            "auth_login_failed",
            level=logging.WARNING,
            reason="invalid_credentials",
        )

    records = _domain_records(caplog)
    assert len(records) == 2
    assert {record.reason for record in records} == {"invalid_credentials"}


def test_login_failure_is_logged_at_warning_and_success_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        log_domain_event("auth_login_succeeded")
        log_domain_event(
            "auth_login_failed",
            level=logging.WARNING,
            reason="account_closed",
        )

    by_event = {record.event: record for record in _domain_records(caplog)}
    assert by_event["auth_login_succeeded"].levelno == logging.INFO
    assert by_event["auth_login_failed"].levelno == logging.WARNING


def test_domain_event_reason_is_rendered_in_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        log_domain_event(
            "auth_login_failed",
            level=logging.WARNING,
            reason="email_verification_required",
        )

    payload = json.loads(JsonFormatter().format(_domain_records(caplog)[0]))
    assert payload["event"] == "auth_login_failed"
    assert payload["reason"] == "email_verification_required"


def test_domain_event_json_only_contains_allowlisted_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        log_domain_event("transaction_created")

    payload = json.loads(JsonFormatter().format(_domain_records(caplog)[0]))
    assert set(payload) <= {
        "timestamp",
        "severity",
        "logger",
        "event",
        "message",
        "request_id",
        "reason",
    }


def test_domain_event_request_id_is_attached_by_context_filter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event_logger = logging.getLogger("app.core.observability")
    context_filter = RequestContextFilter()
    event_logger.addFilter(context_filter)
    token = set_request_id("domain-request-1")
    try:
        with caplog.at_level(logging.INFO):
            log_domain_event("report_exported")
    finally:
        reset_request_id(token)
        event_logger.removeFilter(context_filter)

    record = _domain_records(caplog)[0]
    assert record.request_id == "domain-request-1"


def test_domain_event_inside_request_carries_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    test_app = FastAPI()
    test_app.add_middleware(RequestContextMiddleware)

    @test_app.get("/emit")
    def emit() -> dict[str, bool]:
        log_domain_event("report_exported")
        return {"ok": True}

    # Mirror production: the JSON handler owns the context filter.
    event_logger = logging.getLogger("app.core.observability")
    context_filter = RequestContextFilter()
    event_logger.addFilter(context_filter)
    try:
        with caplog.at_level(logging.INFO):
            response = asyncio.run(
                _get(
                    test_app,
                    "/emit",
                    headers={REQUEST_ID_HEADER: "domain-request-2"},
                )
            )
    finally:
        event_logger.removeFilter(context_filter)

    assert response.status_code == 200
    record = _domain_records(caplog)[0]
    assert record.request_id == "domain-request-2"


def test_domain_event_json_never_contains_sensitive_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        log_domain_event("loan_repayment_recorded")
        log_domain_event("savings_goal_archived")
        log_domain_event("travel_spending_recorded")
        log_domain_event(
            "auth_login_failed",
            level=logging.WARNING,
            reason="invalid_credentials",
        )

    for record in _domain_records(caplog):
        formatted = JsonFormatter().format(record)
        assert "top-secret" not in formatted
        assert "@example.test" not in formatted
        assert "Bearer" not in formatted
        assert "Authorization" not in formatted
        assert "Cookie" not in formatted
