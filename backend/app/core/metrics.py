"""In-process, dependency-free metrics registry.

This module is the C3.1 metrics core: bounded, privacy-safe counters, gauges,
and histograms that render in the Prometheus text exposition format. It has no
database, network, or configuration dependency and performs no application
instrumentation; wiring metrics into request handling is a later phase.

Safety properties:

- Every metric declares its permitted label names and allowed values when it is
  registered. Undeclared labels, missing labels, and values outside the
  declared set are rejected, so no user-provided or high-cardinality value can
  become a label.
- All mutation and rendering is guarded by locks, so the registry is safe under
  concurrent requests and concurrent scrapes.
- Recording errors raise :class:`MetricsError` (a ``ValueError``) before any
  state is mutated, so callers can isolate metric failures from application
  behaviour.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

# Prometheus-compatible metric and label names. Colons are reserved for
# recording rules, so they are deliberately excluded.
_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Declared label values are developer-controlled and bounded. Control
# characters are rejected so a value cannot corrupt the exposition format even
# before escaping is applied.
_LABEL_VALUE_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")

# Default latency buckets in seconds, chosen for HTTP request durations.
DEFAULT_HISTOGRAM_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


class MetricsError(ValueError):
    """Raised for invalid metric definitions or label usage.

    It subclasses ``ValueError`` so future instrumentation can treat a metrics
    fault as a programming error and isolate it from application requests.
    """


def _ensure_name(value: str, *, what: str) -> str:
    if not isinstance(value, str) or not _NAME_PATTERN.fullmatch(value):
        raise MetricsError(f"Invalid {what} name: {value!r}")
    return value


def _ensure_number(value: object, *, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricsError(f"{what} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise MetricsError(f"{what} must be finite, got {value!r}")
    return number


def _normalize_labels(
    labels: Mapping[str, Iterable[object]] | None,
) -> dict[str, frozenset[str]]:
    """Validate and freeze the declared label vocabulary of a metric."""

    if labels is None:
        return {}

    normalized: dict[str, frozenset[str]] = {}
    for name, allowed in labels.items():
        _ensure_name(name, what="label")
        values = frozenset(str(value) for value in allowed)
        if not values:
            raise MetricsError(f"Label {name!r} must declare at least one value.")
        for value in values:
            if not _LABEL_VALUE_PATTERN.fullmatch(value):
                raise MetricsError(
                    f"Label {name!r} has an invalid declared value: {value!r}"
                )
        normalized[name] = values
    return normalized


def _normalize_buckets(buckets: Iterable[object] | None) -> tuple[float, ...]:
    if buckets is None:
        return DEFAULT_HISTOGRAM_BUCKETS

    values = [_ensure_number(bound, what="histogram bucket") for bound in buckets]
    if not values:
        raise MetricsError("A histogram requires at least one bucket boundary.")
    if any(left >= right for left, right in zip(values, values[1:])):
        raise MetricsError(
            "Histogram bucket boundaries must be strictly increasing."
        )
    return tuple(values)


def _escape_label_value(value: str) -> str:
    """Escape a label value for the Prometheus text exposition format."""

    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _escape_help(text: str) -> str:
    """Escape HELP text for the Prometheus text exposition format."""

    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _format_number(value: float) -> str:
    """Render a finite number without a redundant trailing ``.0``."""

    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if math.isnan(value):
        return "NaN"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def _render_label_key(
    pairs: tuple[tuple[str, str], ...],
    extra: str | None = None,
) -> str:
    if not pairs and extra is None:
        return ""
    parts = [f'{name}="{_escape_label_value(value)}"' for name, value in pairs]
    if extra is not None:
        parts.append(extra)
    return "{" + ",".join(parts) + "}"


class _BaseMetric:
    """Shared naming, label validation, and header rendering."""

    _type_name = "untyped"

    def __init__(
        self,
        name: str,
        *,
        help_text: str | None,
        labels: Mapping[str, Iterable[object]] | None,
    ) -> None:
        self._name = _ensure_name(name, what="metric")
        if help_text is not None and not isinstance(help_text, str):
            raise MetricsError("help_text must be a string or None.")
        self._help = help_text
        self._labels = _normalize_labels(labels)
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def help_text(self) -> str | None:
        return self._help

    @property
    def label_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._labels))

    def _series_names(self) -> tuple[str, ...]:
        return (self._name,)

    def _validate_labels(
        self,
        labels: Mapping[str, object] | None,
    ) -> dict[str, str]:
        if labels is None:
            provided: Mapping[str, object] = {}
        elif isinstance(labels, Mapping):
            provided = labels
        else:
            raise MetricsError("labels must be a mapping or None.")

        declared = set(self._labels)
        if set(provided) != declared:
            missing = declared - set(provided)
            undeclared = set(provided) - declared
            raise MetricsError(
                f"Metric {self._name!r} requires labels {sorted(declared)}; "
                f"missing={sorted(missing)}, undeclared={sorted(undeclared)}."
            )

        validated: dict[str, str] = {}
        for name, value in provided.items():
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise MetricsError(
                    f"Label {name!r} must be a string or integer, got {value!r}"
                )
            text = str(value)
            if text not in self._labels[name]:
                raise MetricsError(
                    f"Label {name!r} value {text!r} is not an allowed value."
                )
            validated[name] = text
        return validated

    @staticmethod
    def _label_key(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(labels.items()))

    def _render_header(self) -> list[str]:
        lines: list[str] = []
        if self._help is not None:
            lines.append(f"# HELP {self._name} {_escape_help(self._help)}")
        lines.append(f"# TYPE {self._name} {self._type_name}")
        return lines


class Counter(_BaseMetric):
    """A monotonically increasing metric. Negative increments are rejected."""

    _type_name = "counter"

    def __init__(
        self,
        name: str,
        *,
        help_text: str | None = None,
        labels: Mapping[str, Iterable[object]] | None = None,
    ) -> None:
        super().__init__(name, help_text=help_text, labels=labels)
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def inc(
        self,
        labels: Mapping[str, object] | None = None,
        value: object = 1.0,
    ) -> None:
        validated = self._validate_labels(labels)
        amount = _ensure_number(value, what="counter increment")
        if amount < 0:
            raise MetricsError("Counter increments must be non-negative.")
        key = self._label_key(validated)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def _render(self) -> list[str]:
        with self._lock:
            snapshot = sorted(self._values.items())
        lines = self._render_header()
        for key, value in snapshot:
            lines.append(
                f"{self._name}{_render_label_key(key)} {_format_number(value)}"
            )
        return lines


class Gauge(_BaseMetric):
    """A metric that can be set, incremented, or decremented."""

    _type_name = "gauge"

    def __init__(
        self,
        name: str,
        *,
        help_text: str | None = None,
        labels: Mapping[str, Iterable[object]] | None = None,
    ) -> None:
        super().__init__(name, help_text=help_text, labels=labels)
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def set(
        self,
        value: object,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        validated = self._validate_labels(labels)
        amount = _ensure_number(value, what="gauge value")
        key = self._label_key(validated)
        with self._lock:
            self._values[key] = amount

    def inc(
        self,
        value: object = 1.0,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        self._adjust(value, labels, sign=1.0)

    def dec(
        self,
        value: object = 1.0,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        self._adjust(value, labels, sign=-1.0)

    def _adjust(
        self,
        value: object,
        labels: Mapping[str, object] | None,
        *,
        sign: float,
    ) -> None:
        validated = self._validate_labels(labels)
        amount = _ensure_number(value, what="gauge adjustment")
        key = self._label_key(validated)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + sign * amount

    def _render(self) -> list[str]:
        with self._lock:
            snapshot = sorted(self._values.items())
        lines = self._render_header()
        for key, value in snapshot:
            lines.append(
                f"{self._name}{_render_label_key(key)} {_format_number(value)}"
            )
        return lines


@dataclass
class _HistogramState:
    counts: list[int] = field(default_factory=list)
    total: int = 0
    sum: float = 0.0


class Histogram(_BaseMetric):
    """A cumulative histogram over a fixed, finite set of bucket boundaries."""

    _type_name = "histogram"

    def __init__(
        self,
        name: str,
        *,
        buckets: Iterable[object] | None = None,
        help_text: str | None = None,
        labels: Mapping[str, Iterable[object]] | None = None,
    ) -> None:
        super().__init__(name, help_text=help_text, labels=labels)
        self._buckets = _normalize_buckets(buckets)
        self._states: dict[tuple[tuple[str, str], ...], _HistogramState] = {}

    def _series_names(self) -> tuple[str, ...]:
        return (
            self._name,
            f"{self._name}_bucket",
            f"{self._name}_sum",
            f"{self._name}_count",
        )

    def observe(
        self,
        value: object,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        validated = self._validate_labels(labels)
        amount = _ensure_number(value, what="histogram observation")
        if amount < 0:
            raise MetricsError("Histogram observations must be non-negative.")
        key = self._label_key(validated)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = _HistogramState(counts=[0] * len(self._buckets))
                self._states[key] = state
            for index, bound in enumerate(self._buckets):
                if amount <= bound:
                    state.counts[index] += 1
                    break
            state.total += 1
            state.sum += amount

    def _render(self) -> list[str]:
        with self._lock:
            snapshot = sorted(self._states.items())
        lines = self._render_header()
        for key, state in snapshot:
            cumulative = 0
            for index, bound in enumerate(self._buckets):
                cumulative += state.counts[index]
                le = f'le="{_format_number(bound)}"'
                lines.append(
                    f"{self._name}_bucket"
                    f"{_render_label_key(key, le)} {cumulative}"
                )
            inf_le = 'le="+Inf"'
            lines.append(
                f"{self._name}_bucket"
                f"{_render_label_key(key, inf_le)} {state.total}"
            )
            lines.append(
                f"{self._name}_sum{_render_label_key(key)} "
                f"{_format_number(state.sum)}"
            )
            lines.append(
                f"{self._name}_count{_render_label_key(key)} {state.total}"
            )
        return lines


class MetricsRegistry:
    """A thread-safe registry of bounded metrics with deterministic rendering."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[str, _BaseMetric] = {}
        self._reserved: set[str] = set()

    def _register(self, metric: _BaseMetric) -> _BaseMetric:
        names = metric._series_names()
        with self._lock:
            for name in names:
                if name in self._reserved:
                    raise MetricsError(f"Metric {name!r} is already registered.")
            self._reserved.update(names)
            self._metrics[metric.name] = metric
        return metric

    def counter(
        self,
        name: str,
        *,
        help_text: str | None = None,
        labels: Mapping[str, Iterable[object]] | None = None,
    ) -> Counter:
        return self._register(
            Counter(name, help_text=help_text, labels=labels)
        )

    def gauge(
        self,
        name: str,
        *,
        help_text: str | None = None,
        labels: Mapping[str, Iterable[object]] | None = None,
    ) -> Gauge:
        return self._register(Gauge(name, help_text=help_text, labels=labels))

    def histogram(
        self,
        name: str,
        *,
        buckets: Iterable[object] | None = None,
        help_text: str | None = None,
        labels: Mapping[str, Iterable[object]] | None = None,
    ) -> Histogram:
        return self._register(
            Histogram(
                name,
                buckets=buckets,
                help_text=help_text,
                labels=labels,
            )
        )

    def metric_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._metrics)

    def render(self) -> str:
        """Return the registry as Prometheus text exposition.

        The output is deterministic: metrics appear in registration order and
        each metric's series are sorted by label values. Rendering performs no
        database or network access.
        """

        with self._lock:
            metrics = list(self._metrics.values())
        lines: list[str] = []
        for metric in metrics:
            lines.extend(metric._render())
        return "".join(f"{line}\n" for line in lines)


# Process-wide registry. Later phases register their metrics here; the C3.1
# core intentionally does not wire any instrumentation.
default_registry = MetricsRegistry()
