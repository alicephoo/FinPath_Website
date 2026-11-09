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
from app.core.observability import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    configure_logging,
)
from app.core.security import TokenConfigurationError
from app.db.session import DatabaseUnavailableError, check_database_ready


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

    return application


app = create_app()
