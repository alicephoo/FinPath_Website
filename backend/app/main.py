import hmac
import re
from typing import Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.api.auth import router as auth_router
from app.api.dashboard import router as dashboard_router
from app.api.errors import (
    ApiError,
    api_error_handler,
    database_error_handler,
    database_unavailable_handler,
    internal_error_response,
    mark_server_error,
    validation_error_handler,
)
from app.api.financial_settings import router as financial_settings_router
from app.api.loans import router as loans_router
from app.api.savings import router as savings_router
from app.api.transactions import router as transactions_router
from app.api.travel import router as travel_router
from app.api.reports import router as reports_router
from app.core.config import Settings, settings
from app.core.metrics import MetricsRegistry
from app.core.observability import (
    METRICS_PATH,
    REQUEST_ID_HEADER,
    HttpMetrics,
    RequestContextMiddleware,
    configure_logging,
)
from app.core.security import TokenConfigurationError
from app.db.session import DatabaseUnavailableError, check_database_ready

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Bounded label vocabularies. Any value outside these sets is recorded as
# ``other`` so request input can never create a new series.
_HTTP_METHOD_LABELS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "other"}
)
_HTTP_STATUS_LABELS = frozenset(
    {
        "200",
        "201",
        "202",
        "204",
        "400",
        "401",
        "403",
        "404",
        "405",
        "409",
        "412",
        "415",
        "422",
        "428",
        "500",
        "503",
        "other",
    }
)

# Mirrors the C3.1 label-value rule in ``app.core.metrics``. A route template
# that does not satisfy it is dropped from the vocabulary so it collapses to
# the bounded ``other`` label instead of creating a new series.
_ROUTE_LABEL_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "unavailable"]


configure_logging()


async def token_configuration_handler(
    request: Request,
    exc: TokenConfigurationError,
) -> JSONResponse:
    mark_server_error(request, exc)
    return internal_error_response()


def _collect_route_labels(application: FastAPI) -> frozenset[str]:
    """Return the bounded route-label vocabulary for a completed application."""

    labels = {"unmatched", "other"}
    for route in application.routes:
        path = getattr(route, "path", None)
        if (
            isinstance(path, str)
            and path
            and _ROUTE_LABEL_PATTERN.fullmatch(path)
        ):
            labels.add(path)
    return frozenset(labels)


def _bearer_token(request: Request) -> str | None:
    """Return exactly one well-formed Bearer credential, or ``None``."""

    values = request.headers.getlist("authorization")
    if len(values) != 1:
        return None
    scheme, separator, credentials = values[0].partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    token = credentials.strip()
    if not token:
        return None
    return token


def _metrics_token_matches(token: str, config: Settings) -> bool:
    """Constant-time token check that never authorizes an unset token."""

    configured = config.metrics_token
    expected = (
        configured.get_secret_value() if configured is not None else ""
    )
    matched = hmac.compare_digest(
        token.encode("utf-8"),
        expected.encode("utf-8"),
    )
    return bool(expected) and matched


def _metrics_response(request: Request, config: Settings) -> Response:
    if not config.metrics_enabled:
        return Response(status_code=404)

    token = _bearer_token(request)
    if token is None or not _metrics_token_matches(token, config):
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    registry = getattr(request.app.state, "metrics", None)
    if not isinstance(registry, MetricsRegistry):
        return Response(status_code=404)

    return Response(
        content=registry.render(),
        media_type=PROMETHEUS_CONTENT_TYPE,
    )


def create_app(app_settings: Settings | None = None) -> FastAPI:
    """Build an application instance from the supplied settings.

    Public API documentation is disabled in production so the deployed
    service does not expose schema and interactive tooling. Local development
    keeps the standard ``/docs``, ``/redoc``, and ``/openapi.json`` routes.
    """

    config = app_settings or settings
    is_production = config.environment.strip().lower() in {
        "production",
        "prod",
    }

    application = FastAPI(
        title=config.app_name,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    # CORS is added last so it remains the outermost middleware and also
    # decorates responses produced by the request-context middleware.
    application.add_middleware(RequestContextMiddleware)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.frontend_origin_list),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "If-None-Match",
            "X-CSRF-Token",
            REQUEST_ID_HEADER,
        ],
        expose_headers=["ETag", "Retry-After", REQUEST_ID_HEADER],
    )

    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(
        RequestValidationError,
        validation_error_handler,
    )
    application.add_exception_handler(
        DatabaseUnavailableError,
        database_unavailable_handler,
    )
    application.add_exception_handler(SQLAlchemyError, database_error_handler)
    application.add_exception_handler(
        TokenConfigurationError,
        token_configuration_handler,
    )

    application.include_router(auth_router)
    application.include_router(transactions_router)
    application.include_router(loans_router)
    application.include_router(savings_router)
    application.include_router(travel_router)
    application.include_router(dashboard_router)
    application.include_router(reports_router)
    application.include_router(financial_settings_router)

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get(
        "/ready",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
    )
    def readiness(
        response: Response,
        database_ready: bool = Depends(check_database_ready),
    ) -> ReadinessResponse:
        if not database_ready:
            response.status_code = 503
            return ReadinessResponse(status="unavailable")
        return ReadinessResponse(status="ready")

    @application.get(METRICS_PATH, include_in_schema=False)
    def metrics(request: Request) -> Response:
        return _metrics_response(request, config)

    # Metrics are registered only after every route exists, so the route-label
    # vocabulary is complete. A per-application registry keeps multiple
    # application instances (for example in tests) isolated and avoids
    # duplicate registration.
    registry = MetricsRegistry()
    route_labels = _collect_route_labels(application)
    application.state.metrics = registry
    application.state.http_metrics = HttpMetrics(
        requests=registry.counter(
            "finpath_http_requests_total",
            help_text="Total HTTP requests handled.",
            labels={
                "route": route_labels,
                "method": _HTTP_METHOD_LABELS,
                "status": _HTTP_STATUS_LABELS,
            },
        ),
        duration=registry.histogram(
            "finpath_http_request_duration_seconds",
            help_text="HTTP request duration in seconds.",
            labels={
                "route": route_labels,
                "method": _HTTP_METHOD_LABELS,
            },
        ),
        slow=registry.counter(
            "finpath_http_slow_requests_total",
            help_text="HTTP requests at or above the slow-request threshold.",
            labels={
                "route": route_labels,
                "method": _HTTP_METHOD_LABELS,
            },
        ),
        routes=route_labels,
        methods=_HTTP_METHOD_LABELS,
        statuses=_HTTP_STATUS_LABELS,
    )

    return application


app = create_app()
