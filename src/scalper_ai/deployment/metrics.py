"""In-memory operational metrics with a Prometheus-style text surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MetricKind = Literal["counter", "gauge"]


@dataclass(frozen=True)
class MetricSample:
    """One rendered metric point."""

    name: str
    kind: MetricKind
    value: float
    labels: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Metric name must be non-empty.")
        for key, value in self.labels:
            if not key.strip():
                raise ValueError("Metric label keys must be non-empty.")
            if not value.strip():
                raise ValueError("Metric label values must be non-empty.")


class MetricsRegistry:
    """Tiny registry for counters and gauges exposed through text snapshots."""

    def __init__(self, *, service_name: str) -> None:
        normalized_service_name = service_name.strip()
        if not normalized_service_name:
            raise ValueError("service_name must be non-empty.")
        self._service_name = normalized_service_name
        self._values: dict[tuple[str, MetricKind, tuple[tuple[str, str], ...]], float] = {}

    def increment(self, name: str, value: float = 1.0, **labels: str) -> float:
        """Increase a counter and return its new value."""

        if value < 0:
            raise ValueError("Counter increments must be non-negative.")
        key = self._metric_key(name, "counter", labels)
        next_value = self._values.get(key, 0.0) + value
        self._values[key] = next_value
        return next_value

    def set_gauge(self, name: str, value: float, **labels: str) -> float:
        """Set a gauge and return the stored value."""

        key = self._metric_key(name, "gauge", labels)
        self._values[key] = value
        return value

    def snapshot(self) -> tuple[MetricSample, ...]:
        """Return a stable tuple of metric samples."""

        samples = [
            MetricSample(name=name, kind=kind, value=value, labels=labels)
            for (name, kind, labels), value in self._values.items()
        ]
        return tuple(sorted(samples, key=lambda sample: (sample.name, sample.kind, sample.labels)))

    def render_prometheus(self) -> str:
        """Render the current registry in a Prometheus-like exposition format."""

        samples = self.snapshot()
        if not samples:
            return ""

        lines: list[str] = []
        seen_types: set[str] = set()
        for sample in samples:
            if sample.name not in seen_types:
                lines.append(f"# TYPE {sample.name} {sample.kind}")
                seen_types.add(sample.name)
            lines.append(f"{sample.name}{_format_labels(sample.labels)} {_format_metric_value(sample.value)}")
        return "\n".join(lines)

    def _metric_key(
        self,
        name: str,
        kind: MetricKind,
        labels: dict[str, str],
    ) -> tuple[str, MetricKind, tuple[tuple[str, str], ...]]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Metric name must be non-empty.")
        normalized_labels = dict(labels)
        normalized_labels["service"] = self._service_name
        return normalized_name, kind, tuple(sorted((key, str(value).strip()) for key, value in normalized_labels.items()))


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    fragments = [f'{key}="{value}"' for key, value in labels]
    return "{" + ",".join(fragments) + "}"


def _format_metric_value(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.10g}"
