"""Focused unit tests for the C3.1 in-process metrics core.

These tests exercise the registry directly. They do not require PostgreSQL,
Neon, network access, Docker, or any external service.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.core.config import Settings
from app.core.metrics import (
    DEFAULT_HISTOGRAM_BUCKETS,
    Counter,
    Gauge,
    Histogram,
    MetricsError,
    MetricsRegistry,
    _escape_help,
    _escape_label_value,
)


def _series_value(output: str, series: str) -> float:
    """Return the numeric value of an exact ``series`` line."""

    for line in output.splitlines():
        if not line.startswith(series):
            continue
        remainder = line[len(series) :]
        if remainder and remainder[0] not in " {":
            continue
        return float(remainder.rsplit(" ", 1)[1])
    raise AssertionError(f"series {series!r} not found in:\n{output}")


# --- Counter ---------------------------------------------------------------


def test_counter_increments_and_renders() -> None:
    registry = MetricsRegistry()
    counter = registry.counter(
        "finpath_requests_total",
        help_text="Total requests.",
        labels={"route": {"/health", "/ready"}},
    )

    counter.inc(labels={"route": "/health"})
    counter.inc(labels={"route": "/health"})
    counter.inc(labels={"route": "/ready"}, value=3)

    output = registry.render()
    assert "# HELP finpath_requests_total Total requests." in output
    assert "# TYPE finpath_requests_total counter" in output
    assert 'finpath_requests_total{route="/health"} 2' in output
    assert 'finpath_requests_total{route="/ready"} 3' in output


def test_counter_accumulates_default_increment() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("finpath_events_total")

    for _ in range(5):
        counter.inc()

    assert "finpath_events_total 5" in registry.render()


def test_counter_rejects_negative_increment() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("finpath_events_total")

    with pytest.raises(MetricsError):
        counter.inc(value=-1)

    samples = [
        line
        for line in registry.render().splitlines()
        if not line.startswith("#")
    ]
    assert samples == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True, "1"])
def test_counter_rejects_invalid_increment(bad: object) -> None:
    registry = MetricsRegistry()
    counter = registry.counter("finpath_events_total")

    with pytest.raises(MetricsError):
        counter.inc(value=bad)


# --- Gauge -----------------------------------------------------------------


def test_gauge_set_increment_and_decrement() -> None:
    registry = MetricsRegistry()
    gauge = registry.gauge("finpath_db_ready", help_text="Readiness.")

    gauge.set(1)
    assert "finpath_db_ready 1" in registry.render()

    gauge.inc(2)
    assert "finpath_db_ready 3" in registry.render()

    gauge.dec(5)
    assert "finpath_db_ready -2" in registry.render()


def test_gauge_can_be_set_negative() -> None:
    registry = MetricsRegistry()
    gauge = registry.gauge("finpath_temperature")
    gauge.set(-7.5)

    assert "finpath_temperature -7.5" in registry.render()


def test_gauge_rejects_invalid_values() -> None:
    registry = MetricsRegistry()
    gauge = registry.gauge("finpath_db_ready")

    with pytest.raises(MetricsError):
        gauge.set(float("nan"))
    with pytest.raises(MetricsError):
        gauge.inc(value=float("inf"))
    with pytest.raises(MetricsError):
        gauge.dec(value=True)


# --- Histogram -------------------------------------------------------------


def test_histogram_cumulative_buckets_and_inf() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram(
        "finpath_duration_seconds",
        buckets=(0.1, 0.5, 1.0),
        labels={"route": {"/health"}},
    )

    for value in (0.05, 0.3, 2.0, 0.5):
        histogram.observe(value, labels={"route": "/health"})

    output = registry.render()
    assert (
        'finpath_duration_seconds_bucket{route="/health",le="0.1"} 1'
        in output
    )
    assert (
        'finpath_duration_seconds_bucket{route="/health",le="0.5"} 3'
        in output
    )
    assert (
        'finpath_duration_seconds_bucket{route="/health",le="1"} 3' in output
    )
    assert (
        'finpath_duration_seconds_bucket{route="/health",le="+Inf"} 4'
        in output
    )
    assert (
        'finpath_duration_seconds_count{route="/health"} 4' in output
    )
    assert _series_value(
        output,
        'finpath_duration_seconds_sum{route="/health"}',
    ) == pytest.approx(2.85)


def test_histogram_sum_and_count_are_exact() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram(
        "finpath_duration_seconds",
        buckets=(1.0, 2.0, 5.0),
    )

    for value in (1.0, 2.0, 5.0):
        histogram.observe(value)

    output = registry.render()
    assert "finpath_duration_seconds_sum 8" in output
    assert "finpath_duration_seconds_count 3" in output
    assert 'finpath_duration_seconds_bucket{le="+Inf"} 3' in output


def test_histogram_rejects_negative_and_non_finite_observation() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram("finpath_duration_seconds", buckets=(1.0,))

    with pytest.raises(MetricsError):
        histogram.observe(-0.1)
    with pytest.raises(MetricsError):
        histogram.observe(float("nan"))
    with pytest.raises(MetricsError):
        histogram.observe(float("inf"))


@pytest.mark.parametrize(
    "buckets",
    [
        (),
        (0.5, 0.1),
        (0.1, 0.1),
        (0.1, float("inf")),
        (float("nan"),),
    ],
)
def test_histogram_rejects_invalid_buckets(buckets: tuple[float, ...]) -> None:
    registry = MetricsRegistry()

    with pytest.raises(MetricsError):
        registry.histogram("finpath_duration_seconds", buckets=buckets)


def test_histogram_default_buckets_are_used_when_omitted() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram("finpath_duration_seconds")
    histogram.observe(0.02)

    output = registry.render()
    assert len(DEFAULT_HISTOGRAM_BUCKETS) == 11
    assert 'finpath_duration_seconds_bucket{le="0.005"} 0' in output
    assert 'finpath_duration_seconds_bucket{le="+Inf"} 1' in output


# --- Labels ----------------------------------------------------------------


def test_multiple_label_combinations_produce_separate_series() -> None:
    registry = MetricsRegistry()
    counter = registry.counter(
        "finpath_requests_total",
        labels={"route": {"/health"}, "method": {"GET", "POST"}},
    )

    counter.inc(labels={"route": "/health", "method": "GET"})
    counter.inc(labels={"route": "/health", "method": "POST"}, value=2)

    output = registry.render()
    assert (
        'finpath_requests_total{method="GET",route="/health"} 1' in output
    )
    assert (
        'finpath_requests_total{method="POST",route="/health"} 2' in output
    )


def test_missing_label_is_rejected() -> None:
    registry = MetricsRegistry()
    counter = registry.counter(
        "finpath_requests_total",
        labels={"route": {"/health"}},
    )

    with pytest.raises(MetricsError):
        counter.inc()


def test_undeclared_label_is_rejected() -> None:
    registry = MetricsRegistry()
    counter = registry.counter(
        "finpath_requests_total",
        labels={"route": {"/health"}},
    )

    with pytest.raises(MetricsError):
        counter.inc(labels={"route": "/health", "user_id": "42"})


def test_invalid_label_value_is_rejected() -> None:
    registry = MetricsRegistry()
    counter = registry.counter(
        "finpath_requests_total",
        labels={"route": {"/health"}},
    )

    with pytest.raises(MetricsError):
        counter.inc(labels={"route": "/api/v1/transactions/123"})


def test_non_string_or_integer_label_value_is_rejected() -> None:
    registry = MetricsRegistry()
    counter = registry.counter(
        "finpath_requests_total",
        labels={"route": {"/health"}},
    )

    with pytest.raises(MetricsError):
        counter.inc(labels={"route": ("/health",)})


def test_label_value_rejects_raw_url_paths_by_membership() -> None:
    registry = MetricsRegistry()
    counter = registry.counter(
        "finpath_requests_total",
        labels={"route": {"/health"}},
    )

    with pytest.raises(MetricsError):
        counter.inc(labels={"route": "/health?token=secret"})


def test_invalid_metric_and_label_names_are_rejected() -> None:
    registry = MetricsRegistry()

    with pytest.raises(MetricsError):
        registry.counter("bad-name")
    with pytest.raises(MetricsError):
        registry.counter("finpath_ok_total", labels={"bad-label": {"x"}})
    with pytest.raises(MetricsError):
        registry.counter("finpath_ok_total", labels={"route": set()})


def test_invalid_declared_label_values_are_rejected() -> None:
    registry = MetricsRegistry()

    with pytest.raises(MetricsError):
        registry.counter("finpath_a_total", labels={"route": {""}})
    with pytest.raises(MetricsError):
        registry.counter("finpath_b_total", labels={"route": {"bad\nvalue"}})


# --- Registry --------------------------------------------------------------


def test_duplicate_registration_is_rejected() -> None:
    registry = MetricsRegistry()
    registry.counter("finpath_requests_total")

    with pytest.raises(MetricsError):
        registry.counter("finpath_requests_total")


def test_histogram_series_name_collision_is_rejected() -> None:
    registry = MetricsRegistry()
    registry.histogram("finpath_duration_seconds")

    with pytest.raises(MetricsError):
        registry.counter("finpath_duration_seconds_count")
    with pytest.raises(MetricsError):
        registry.gauge("finpath_duration_seconds")


def test_empty_registry_renders_empty_string() -> None:
    assert MetricsRegistry().render() == ""


def test_rendering_is_deterministic() -> None:
    first = MetricsRegistry()
    second = MetricsRegistry()

    for registry in (first, second):
        counter = registry.counter(
            "finpath_requests_total",
            labels={"route": {"/health", "/ready"}},
        )
        counter.inc(labels={"route": "/ready"})
        counter.inc(labels={"route": "/health"}, value=2)

    assert first.render() == second.render()
    assert first.render() == first.render()


def test_type_declarations_are_correct() -> None:
    registry = MetricsRegistry()
    registry.counter("finpath_a_total")
    registry.gauge("finpath_b")
    registry.histogram("finpath_c_seconds", buckets=(1.0,))

    output = registry.render()
    assert "# TYPE finpath_a_total counter" in output
    assert "# TYPE finpath_b gauge" in output
    assert "# TYPE finpath_c_seconds histogram" in output


def test_help_line_is_optional() -> None:
    registry = MetricsRegistry()
    registry.counter("finpath_a_total")

    output = registry.render()
    assert "# HELP" not in output
    assert "# TYPE finpath_a_total counter" in output


def test_registered_metric_properties_are_exposed() -> None:
    registry = MetricsRegistry()
    counter = registry.counter(
        "finpath_requests_total",
        help_text="Total requests.",
        labels={"route": {"/health"}},
    )

    assert isinstance(counter, Counter)
    assert counter.name == "finpath_requests_total"
    assert counter.help_text == "Total requests."
    assert counter.label_names == ("route",)
    assert registry.metric_names() == ("finpath_requests_total",)


def test_gauge_and_histogram_types_are_exported() -> None:
    registry = MetricsRegistry()
    assert isinstance(registry.gauge("finpath_g"), Gauge)
    assert isinstance(
        registry.histogram("finpath_h", buckets=(1.0,)),
        Histogram,
    )


# --- Rendering safety ------------------------------------------------------


def test_label_values_are_escaped() -> None:
    registry = MetricsRegistry()
    raw_value = 'a"b\\c'
    counter = registry.counter("finpath_x_total", labels={"kind": {raw_value}})
    counter.inc(labels={"kind": raw_value})

    output = registry.render()
    assert r'kind="a\"b\\c"' in output


def test_escape_helpers_handle_newlines_and_backslashes() -> None:
    assert _escape_label_value("line\nbreak") == "line\\nbreak"
    assert _escape_label_value('quote"slash\\') == 'quote\\"slash\\\\'
    assert _escape_help("help\nline\\") == "help\\nline\\\\"


# --- Concurrency -----------------------------------------------------------


def test_counter_is_thread_safe_under_concurrent_increments() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("finpath_events_total", labels={"event": {"x"}})

    def worker() -> None:
        for _ in range(500):
            counter.inc(labels={"event": "x"})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert 'finpath_events_total{event="x"} 4000' in registry.render()


def test_histogram_is_thread_safe_under_concurrent_observations() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram("finpath_duration_seconds", buckets=(0.5, 1.0))

    def worker() -> None:
        for _ in range(500):
            histogram.observe(0.2)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    output = registry.render()
    assert 'finpath_duration_seconds_bucket{le="0.5"} 3000' in output
    assert 'finpath_duration_seconds_bucket{le="+Inf"} 3000' in output
    assert "finpath_duration_seconds_count 3000" in output


def test_rendering_is_safe_while_updates_occur() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("finpath_events_total", labels={"event": {"x"}})
    stop = threading.Event()
    errors: list[BaseException] = []

    def updater() -> None:
        while not stop.is_set():
            counter.inc(labels={"event": "x"})

    def renderer() -> None:
        while not stop.is_set():
            try:
                output = registry.render()
                assert isinstance(output, str)
            except BaseException as exc:  # noqa: BLE001 - recorded for assert
                errors.append(exc)
                return

    threads = [threading.Thread(target=updater) for _ in range(4)]
    threads += [threading.Thread(target=renderer) for _ in range(2)]
    for thread in threads:
        thread.start()
    time.sleep(0.2)
    stop.set()
    for thread in threads:
        thread.join()

    assert errors == []


def test_public_state_does_not_leak_mutable_containers() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("finpath_events_total", labels={"event": {"x"}})
    counter.inc(labels={"event": "x"})

    for name, value in vars(counter).items():
        if not name.startswith("_"):
            assert not isinstance(value, (dict, list, set))

    first = registry.render()
    assert isinstance(first, str)
    assert registry.render() == registry.render()


# --- Configuration ---------------------------------------------------------


def test_metrics_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINPATH_METRICS_ENABLED", raising=False)

    configured = Settings(_env_file=None)

    assert configured.metrics_enabled is False


def test_metrics_can_be_enabled_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_METRICS_ENABLED", "true")

    configured = Settings(_env_file=None)

    assert configured.metrics_enabled is True


def test_metrics_token_defaults_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FINPATH_METRICS_TOKEN", raising=False)

    configured = Settings(_env_file=None)

    assert configured.metrics_token is None


def test_metrics_token_is_masked_in_settings_representation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "test-metrics-token-not-a-real-secret"
    monkeypatch.setenv("FINPATH_METRICS_TOKEN", token)

    configured = Settings(_env_file=None)

    assert configured.metrics_token is not None
    assert token not in repr(configured)
    assert token not in str(configured)
    assert "**********" in repr(configured.metrics_token)


def test_metrics_token_is_parsed_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "test-metrics-token-not-a-real-secret"
    monkeypatch.setenv("FINPATH_METRICS_TOKEN", token)

    configured = Settings(_env_file=None)

    assert configured.metrics_token is not None
    assert configured.metrics_token.get_secret_value() == token
