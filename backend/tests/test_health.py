import asyncio
import logging

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from app.db import session as database_session
from app.main import app
from tests.pg_test_env import require_test_database_urls


async def _get(
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.get(path, headers=headers)


def test_application_can_be_imported() -> None:
    assert isinstance(app, FastAPI)


def test_health_returns_expected_response() -> None:
    response = asyncio.run(_get("/health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_does_not_depend_on_database_connectivity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnreachableEngine:
        def connect(self) -> None:
            raise AssertionError("liveness must not connect to the database")

    monkeypatch.setattr(database_session, "engine", UnreachableEngine())

    response = asyncio.run(_get("/health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_ready_when_database_responds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_url, _, _ = require_test_database_urls()
    test_engine = create_engine(
        runtime_url,
        hide_parameters=True,
        pool_pre_ping=True,
    )
    monkeypatch.setattr(database_session, "engine", test_engine)

    try:
        response = asyncio.run(_get("/ready"))
    finally:
        test_engine.dispose()

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_reports_unavailable_without_leaking_database_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class UnreachableEngine:
        def connect(self) -> None:
            raise OperationalError(
                "SELECT 1",
                {},
                RuntimeError("password=top-secret database=finpath_test"),
            )

    monkeypatch.setattr(database_session, "engine", UnreachableEngine())

    with caplog.at_level(logging.WARNING):
        response = asyncio.run(_get("/ready"))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert response.headers["X-Request-ID"]
    events = [getattr(record, "event", None) for record in caplog.records]
    assert "database_readiness_failed" in events
    assert any(
        getattr(record, "status_code", None) == 503 for record in caplog.records
    )
    for leaked in (
        "top-secret",
        "password",
        "finpath_test",
        "postgresql",
        "Traceback",
        "SELECT 1",
    ):
        assert leaked not in response.text
        assert leaked not in caplog.text
