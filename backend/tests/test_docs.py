from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app


def _development_app() -> FastAPI:
    return create_app(Settings(_env_file=None, environment="development"))


def _production_app() -> FastAPI:
    return create_app(
        Settings(
            _env_file=None,
            environment="production",
            frontend_origins="https://finpath.example.test",
            require_email_verification=False,
            allow_unverified_registration=True,
        )
    )


async def _get(application: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        return await client.get(path)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_development_exposes_api_documentation(path: str) -> None:
    response = asyncio.run(_get(_development_app(), path))

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_production_disables_api_documentation(path: str) -> None:
    production_app = _production_app()

    response = asyncio.run(_get(production_app, path))

    assert response.status_code == 404
    assert production_app.docs_url is None
    assert production_app.redoc_url is None
    assert production_app.openapi_url is None


def test_production_app_still_serves_health_and_readiness_contracts() -> None:
    production_app = _production_app()

    health = asyncio.run(_get(production_app, "/health"))

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert production_app.url_path_for("health") == "/health"
    assert production_app.url_path_for("readiness") == "/ready"
