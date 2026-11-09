"""Structured logging, request correlation, and request timing.

The application emits one machine-readable JSON record for every completed
request. Log records never include request or response bodies, cookies,
authorization headers, query strings, or database connection details.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MAX_LENGTH = 128
REQUEST_ID_PATTERN = re.compile(
    rf"^[A-Za-z0-9][A-Za-z0-9._:-]{{0,{REQUEST_ID_MAX_LENGTH - 1}}}$"
)

_request_id: ContextVar[str | None] = ContextVar(
    "finpath_request_id",
    default=None,
)

logger = logging.getLogger(__name__)

_EXTRA_LOG_FIELDS = (
    "event",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "slow",
    "error_type",
    "purpose",
)


def get_request_id() -> str | None:
    """Return the request ID for the current request context, if any."""

    return _request_id.get()


def set_request_id(value: str) -> Token[str | None]:
    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def resolve_request_id(value: str | None) -> str:
    """Return a safe request ID, replacing absent or invalid input.

    Input is bounded and restricted to a conservative character set so a
    client cannot inject log structure through the correlation header.
    """

    if value is not None and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid4().hex


def slow_request_threshold_ms() -> float:
    return max(0.0, float(settings.slow_request_threshold_ms))


class RequestContextFilter(logging.Filter):
    """Attach the current request ID to every record that does not set one."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as a single JSON object with an allowlisted schema."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "severity": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None),
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        for field in _EXTRA_LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            # Tracebacks contain code paths, not request bodies or headers.
            # Handled database errors deliberately omit exception details.
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Install the JSON handler once for the application logger tree."""

    app_logger = logging.getLogger("app")
    has_json_handler = any(
        getattr(handler, "_finpath_json_handler", False)
        for handler in app_logger.handlers
    )
    if not has_json_handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RequestContextFilter())
        handler._finpath_json_handler = True  # type: ignore[attr-defined]
        app_logger.addHandler(handler)

    level_name = settings.log_level.strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    app_logger.setLevel(level if isinstance(level, int) else logging.INFO)

    # Uvicorn's default access log includes the full query string. The
    # structured request record below is the single source of HTTP access
    # events and intentionally omits query strings and sensitive headers.
    logging.getLogger("uvicorn.access").disabled = True


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "unmatched"


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _error_payload(request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": "internal_error",
            "message": "An unexpected server error occurred.",
            "field_errors": [],
            "request_id": request_id,
        }
    }


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Correlate, time, and log every HTTP request."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = set_request_id(request_id)
        request.state.request_id = request_id
        started_at = time.perf_counter()

        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                duration_ms = _duration_ms(started_at)
                logger.error(
                    "Unhandled application exception.",
                    extra={
                        "event": "http_request_failed",
                        "request_id": request_id,
                        "method": request.method,
                        "route": _route_label(request),
                        "status_code": 500,
                        "duration_ms": duration_ms,
                        "slow": duration_ms >= slow_request_threshold_ms(),
                        "error_type": type(exc).__name__,
                    },
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                response = JSONResponse(
                    status_code=500,
                    content=_error_payload(request_id),
                )
            else:
                duration_ms = _duration_ms(started_at)
                status_code = response.status_code
                slow = duration_ms >= slow_request_threshold_ms()
                server_error_type = getattr(
                    request.state,
                    "server_error_type",
                    None,
                )
                if status_code >= 500:
                    level = logging.ERROR
                elif slow:
                    level = logging.WARNING
                else:
                    level = logging.INFO

                extra: dict[str, Any] = {
                    "event": "http_request_completed",
                    "request_id": request_id,
                    "method": request.method,
                    "route": _route_label(request),
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "slow": slow,
                }
                if server_error_type:
                    extra["error_type"] = server_error_type
                logger.log(level, "HTTP request completed.", extra=extra)

            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            reset_request_id(token)
