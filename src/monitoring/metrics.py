"""Prometheus metrics for the scoring API.

One registry per app instance (so tests can create many apps without duplicate-registration
errors). The API runs a single gunicorn worker with threads, so in-process counters are exact; scale
out with replicas (Phase 7), not with extra workers.

Labels are deliberately low-cardinality: route templates, status classes, model version. No raw
inputs and no customer identifiers ever reach a metric.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
SCORE_BUCKETS = tuple(i / 10 for i in range(1, 10)) + (1.0,)


class ApiMetrics:
    def __init__(self, registry: CollectorRegistry | None = None):
        self.registry = registry or CollectorRegistry()
        self.requests = Counter(
            "api_requests_total",
            "HTTP requests",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "api_request_duration_seconds",
            "HTTP request latency",
            ["route"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.in_flight = Gauge(
            "api_requests_in_flight", "Requests being served", registry=self.registry
        )
        self.predictions = Counter(
            "api_predictions_total",
            "Scored records by outcome and model version",
            ["model_version", "outcome"],
            registry=self.registry,
        )
        self.scores = Histogram(
            "api_risk_score",
            "Distribution of returned risk scores (drift signal)",
            ["model_version"],
            buckets=SCORE_BUCKETS,
            registry=self.registry,
        )
        self.validation_failures = Counter(
            "api_validation_failures_total",
            "Requests rejected by input validation",
            ["code"],
            registry=self.registry,
        )
        self.model_loaded = Gauge(
            "api_model_loaded", "1 when a model is loaded", registry=self.registry
        )

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
