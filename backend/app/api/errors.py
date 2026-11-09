from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.observability import get_request_id
from app.db.session import DatabaseUnavailableError


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        field_errors: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors or []
        self.headers = headers


def _envelope(
    code: str,
    message: str,
    field_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or [],
            "request_id": get_request_id() or str(uuid4()),
        }
    }


def internal_error_response() -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=_envelope(
            "internal_error",
            "An unexpected server error occurred.",
        ),
    )


def mark_server_error(request: Request, exc: Exception) -> None:
    """Record a handled 5xx cause for the request log without exposing it."""

    request.state.server_error_type = type(exc).__name__


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, exc.field_errors),
        headers=exc.headers,
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    field_errors: list[dict[str, str]] = []
    for error in exc.errors():
        location = [
            str(part)
            for part in error.get("loc", ())
            if part not in ("body", "query", "path")
        ]
        field_errors.append(
            {
                "field": ".".join(location) or "body",
                "code": str(error.get("type", "validation_error")),
                "message": str(error.get("msg", "Invalid value.")),
            }
        )
    return JSONResponse(
        status_code=422,
        content=_envelope(
            "validation_error",
            "The request contains invalid values.",
            field_errors,
        ),
    )


async def database_unavailable_handler(
    request: Request,
    exc: DatabaseUnavailableError,
) -> JSONResponse:
    mark_server_error(request, exc)
    return JSONResponse(
        status_code=503,
        content=_envelope(
            "database_unavailable",
            "The database is temporarily unavailable.",
        ),
    )


async def database_error_handler(
    request: Request,
    exc: SQLAlchemyError,
) -> JSONResponse:
    # Never expose SQL, parameters, or driver details to API clients.
    mark_server_error(request, exc)
    return JSONResponse(
        status_code=500,
        content=_envelope(
            "internal_error",
            "An unexpected server error occurred.",
        ),
    )
