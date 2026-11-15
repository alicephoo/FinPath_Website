"""Focused tests for C3.2 HTTP metrics and the protected /metrics endpoint.

No PostgreSQL, network, Docker, or external services are required.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from fastapi import FastAPI, Response

from app.core.config import Settings, settings
from app.core.metrics import MetricsRegistry
from app.core.observability import (
    METRICS_PATH,
    HttpMetrics,
    RequestContextMiddleware,
)
from app.main import PROMETHEUS_CONTENT_TYPE, create_app

# Test-only token. It is not a real credential and is never configured in any
# deployed environment.
_TOKEN = "test-metrics-token-not-a-real-secret"


def _app(*, enabled: bool = False, token: str | None = None) -> FastAPI:
    return create_app(
        Settings(
            _env_file=None,
            environment="development",
            metrics_enabled=enabled,
            metrics_token=token,
        )
    )


async def _request(
    app: FastAPI,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | list[tuple[str, str]] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, headers=headers)


def _scrape(app: FastAPI) -> str:
    response = asyncio.run(
        _request(
            app,
            METRICS_PATH,
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    )
    assert response.status_code == 200
    return response.text


def _bare_app() -> tuple[FastAPI, MetricsRegistry]:
    """A minimal instrumented app with routes that need no database."""

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    registry = MetricsRegistry()
    routes = frozenset(
        {"/items/{item_id}", "/health", "/boom", "/teapot", "unmatched", "other"}
    )
    methods = frozenset({"GET", "other"})
    statuses = frozenset({"200", "404", "500", "other"})

    app.state.metrics = registry
    app.state.http_metrics = HttpMetrics(
        requests=registry.counter(
            "finpath_http_requests_total",
            labels={"route": routes, "method": methods, "status": statuses},
        ),
        duration=registry.histogram(
            "finpath_http_request_duration_seconds",
            labels={"route": routes, "method": methods},
        ),
        slow=registry.counter(
            "finpath_http_slow_requests_total",
            labels={"route": routes, "method": methods},
        ),
        routes=routes,
        methods=methods,
        statuses=statuses,
    )

    @app.get("/items/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("top-secret boom detail")

    @app.get("/teapot")
    def teapot() -> Response:
        return Response(status_code=418)

    return app, registry


# --- Endpoint security -----------------------------------------------------


def test_metrics_disabled_by_default_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FINPATH_METRICS_ENABLED", raising=False)
    monkeypatch.delenv("FINPATH_METRICS_TOKEN", raising=False)
    app = create_app(Settings(_env_file=None, environment="development"))

    response = asyncio.run(_request(app, METRICS_PATH))

    assert response.status_code == 404
    assert response.content == b""


def test_enabled_without_server_token_returns_401() -> None:
    app = _app(enabled=True, token=None)

    response = asyncio.run(
        _request(
            app,
            METRICS_PATH,
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    )

    assert response.status_code == 401
    assert response.content == b""


def test_enabled_with_missing_authorization_returns_401() -> None:
    app = _app(enabled=True, token=_TOKEN)

    response = asyncio.run(_request(app, METRICS_PATH))

    assert response.status_code == 401
    assert response.content == b""


def test_enabled_with_incorrect_token_returns_401() -> None:
    app = _app(enabled=True, token=_TOKEN)

    response = asyncio.run(
        _request(
            app,
            METRICS_PATH,
            headers={"Authorization": "Bearer wrong-token"},
        )
    )

    assert response.status_code == 401
    assert response.content == b""


@pytest.mark.parametrize(
    "authorization",
    ["Basic abcdef", "Bearer", "Token abcdef", "Bearer   "],
)
def test_enabled_with_malformed_authorization_returns_401(
    authorization: str,
) -> None:
    app = _app(enabled=True, token=_TOKEN)

    response = asyncio.run(
        _request(
            app,
            METRICS_PATH,
            headers={"Authorization": authorization},
        )
    )

    assert response.status_code == 401
    assert response.content == b""


def test_enabled_with_duplicate_authorization_returns_401() -> None:
    app = _app(enabled=True, token=_TOKEN)

    response = asyncio.run(
        _request(
            app,
            METRICS_PATH,
            headers=[
                ("Authorization", f"Bearer {_TOKEN}"),
                ("Authorization", f"Bearer {_TOKEN}"),
            ],
        )
    )

    assert response.status_code == 401
    assert response.content == b""


def test_enabled_with_correct_token_returns_200_and_metrics() -> None:
    app = _app(enabled=True, token=_TOKEN)

    response = asyncio.run(
        _request(
            app,
            METRICS_PATH,
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    )

    assert response.status_code == 200
    assert "# TYPE finpath_http_requests_total counter" in response.text


def test_authorized_content_type_is_prometheus_exposition() -> None:
    app = _app(enabled=True, token=_TOKEN)

    response = asyncio.run(
        _request(
            app,
            METRICS_PATH,
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == PROMETHEUS_CONTENT_TYPE


def test_unauthorized_responses_contain_no_metric_content() -> None:
    app = _app(enabled=True, token=_TOKEN)

    response = asyncio.run(
        _request(
            app,
            METRICS_PATH,
            headers={"Authorization": "Bearer wrong-token"},
        )
    )

    assert response.status_code == 401
    assert "finpath_http_requests_total" not in response.text
    assert response.content == b""


def test_token_never_appears_in_responses_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app(enabled=True, token=_TOKEN)

    with caplog.at_level(logging.INFO):
        unauthorized = asyncio.run(_request(app, METRICS_PATH))
        authorized = asyncio.run(
            _request(
                app,
                METRICS_PATH,
                headers={"Authorization": f"Bearer {_TOKEN}"},
            )
        )

    assert _TOKEN not in unauthorized.text
    assert _TOKEN not in authorized.text
    assert _TOKEN not in caplog.text


# --- HTTP instrumentation --------------------------------------------------


def test_request_counter_increments_once_per_request() -> None:
    app = _app(enabled=True, token=_TOKEN)

    asyncio.run(_request(app, "/health"))
    asyncio.run(_request(app, "/health"))

    output = _scrape(app)
    assert (
        'finpath_http_requests_total'
        '{method="GET",route="/health",status="200"} 2'
    ) in output


def test_histogram_count_matches_observed_requests() -> None:
    app = _app(enabled=True, token=_TOKEN)

    for _ in range(3):
        asyncio.run(_request(app, "/health"))

    output = _scrape(app)
    assert (
        'finpath_http_request_duration_seconds_count'
        '{method="GET",route="/health"} 3'
    ) in output


def test_histogram_output_includes_cumulative_buckets_and_inf() -> None:
    app = _app(enabled=True, token=_TOKEN)

    asyncio.run(_request(app, "/health"))

    output = _scrape(app)
    # A finite default bucket is always rendered, and every observation is
    # counted in the cumulative ``+Inf`` bucket.
    assert (
        'finpath_http_request_duration_seconds_bucket'
        '{method="GET",route="/health",le="0.005"}'
    ) in output
    assert (
        'finpath_http_request_duration_seconds_bucket'
        '{method="GET",route="/health",le="+Inf"} 1'
    ) in output


def test_slow_counter_increments_only_at_or_above_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(enabled=True, token=_TOKEN)

    monkeypatch.setattr(settings, "slow_request_threshold_ms", 10**9)
    asyncio.run(_request(app, "/health"))
    fast_output = _scrape(app)
    assert "finpath_http_slow_requests_total{" not in fast_output

    monkeypatch.setattr(settings, "slow_request_threshold_ms", 0)
    asyncio.run(_request(app, "/health"))
    slow_output = _scrape(app)
    assert (
        'finpath_http_slow_requests_total'
        '{method="GET",route="/health"} 1'
    ) in slow_output


def test_route_templates_are_used_instead_of_concrete_paths() -> None:
    app, registry = _bare_app()

    asyncio.run(_request(app, "/items/123"))

    output = registry.render()
    assert (
        'finpath_http_requests_total'
        '{method="GET",route="/items/{item_id}",status="200"} 1'
    ) in output


def test_raw_path_parameter_values_do_not_appear_in_metrics() -> None:
    app, registry = _bare_app()

    asyncio.run(_request(app, "/items/super-secret-id"))

    output = registry.render()
    assert "super-secret-id" not in output


def test_unmatched_requests_use_the_unmatched_label() -> None:
    app, registry = _bare_app()

    response = asyncio.run(_request(app, "/does-not-exist"))

    assert response.status_code == 404
    output = registry.render()
    assert (
        'finpath_http_requests_total'
        '{method="GET",route="unmatched",status="404"} 1'
    ) in output


def test_unlisted_method_and_status_map_to_other() -> None:
    app, registry = _bare_app()

    method_response = asyncio.run(_request(app, "/health", method="POST"))
    status_response = asyncio.run(_request(app, "/teapot"))

    assert method_response.status_code == 405
    assert status_response.status_code == 418
    output = registry.render()
    assert 'method="other"' in output
    assert (
        'finpath_http_requests_total'
        '{method="GET",route="/teapot",status="other"} 1'
    ) in output


def test_handled_exception_counts_once_with_final_status() -> None:
    app, registry = _bare_app()

    response = asyncio.run(_request(app, "/boom"))

    assert response.status_code == 500
    output = registry.render()
    assert (
        'finpath_http_requests_total'
        '{method="GET",route="/boom",status="500"} 1'
    ) in output
    assert (
        'finpath_http_requests_total'
        '{method="GET",route="/boom",status="200"}'
    ) not in output


def test_metrics_scrapes_never_count_themselves() -> None:
    enabled = _app(enabled=True, token=_TOKEN)

    asyncio.run(_request(enabled, "/health"))
    for _ in range(2):
        assert _scrape(enabled)

    output = enabled.state.metrics.render()
    assert 'route="/metrics"' not in output
    assert (
        'finpath_http_requests_total'
        '{method="GET",route="/health",status="200"} 1'
    ) in output


def test_disabled_metrics_endpoint_is_not_instrumented() -> None:
    disabled = _app(enabled=False)

    response = asyncio.run(_request(disabled, METRICS_PATH))

    assert response.status_code == 404
    output = disabled.state.metrics.render()
    assert 'route="/metrics"' not in output
    assert 'route="unmatched"' not in output


def test_metrics_recording_failure_does_not_break_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, registry = _bare_app()
    holder = app.state.http_metrics

    class RaisingCounter:
        def inc(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("metrics backend down")

    app.state.http_metrics = HttpMetrics(
        requests=RaisingCounter(),  # type: ignore[arg-type]
        duration=holder.duration,
        slow=holder.slow,
        routes=holder.routes,
        methods=holder.methods,
        statuses=holder.statuses,
    )

    with caplog.at_level(logging.WARNING):
        response = asyncio.run(_request(app, "/health"))

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    events = [getattr(record, "event", None) for record in caplog.records]
    assert "metrics_recording_failed" in events
    assert "metrics backend down" not in caplog.text


def test_bare_app_without_metrics_holder_handles_requests() -> None:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    response = asyncio.run(_request(app, "/health"))

    assert response.status_code == 200
    assert response.json() == {"ok": True}
