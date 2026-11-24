"""Flask scoring API.

    GET  /health      readiness: 200 when a model is loaded, 503 otherwise (open, for probes)
    GET  /model-info  which model is serving and how it was evaluated
    POST /predict     one record (`{...fields}`) or a batch (`{"records": [...]}`) -> risk scores
    POST /explain     per-record top factors (SHAP-style contributions), small batches only
    GET  /metrics     Prometheus text format (open; keep it on the internal network)

Errors are always JSON: 400 malformed body, 401 bad or missing key, 404/405, 413 body too large,
415 not JSON, 422 invalid fields, 500 unexpected (no stack trace, only a request id), 503 no model.
Raw field values are never logged and never become metric labels.
"""

from __future__ import annotations

import hmac
import logging
import re
import threading
import time
import uuid
from typing import Any

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

from src.models import inference
from src.monitoring.metrics import ApiMetrics
from src.serving.model_loader import Bundle, BundleHolder, warm_up
from src.serving.schemas import RequestError, ignored_fields, parse_records, validate_record
from src.serving.settings import DISCLAIMER, Settings

OPEN_ENDPOINTS = frozenset({"health", "metrics"})
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_TOP_K = 10
MAX_VALIDATION_DETAILS = 20

log = logging.getLogger("credit_risk_api")


def _configure_logging(level: str) -> None:
    if log.handlers:
        log.setLevel(level)
        return
    handler = logging.StreamHandler()
    try:
        from pythonjsonlogger.json import JsonFormatter

        handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    except ImportError:  # keep working without the optional formatter
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False


def _warm_up_quietly(loaded: Bundle) -> None:
    started = time.perf_counter()
    try:
        warm_up(loaded)
        log.info("warm-up done", extra={"duration_s": round(time.perf_counter() - started, 2)})
    except Exception:  # a failed warm-up must never stop the service
        log.warning("warm-up failed (first requests will be slower)", exc_info=True)


def _error(status: int, code: str, message: str, details: Any = None) -> Response:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message, "request_id": getattr(g, "request_id", None)}
    }
    if details is not None:
        body["error"]["details"] = details
    response = jsonify(body)
    response.status_code = status
    return response


def create_app(
    settings: Settings | None = None,
    bundle: Bundle | None = None,
    metrics: ApiMetrics | None = None,
) -> Flask:
    """App factory. Tests pass `settings` and a ready `bundle`; production uses the environment."""
    settings = settings or Settings.from_env()
    _configure_logging(settings.log_level)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_body_bytes
    app.json.sort_keys = False
    holder = BundleHolder(settings, preload=bundle)
    api_metrics = metrics or ApiMetrics()
    if settings.warmup and holder.get() is not None:
        threading.Thread(target=_warm_up_quietly, args=(holder.get(),), daemon=True).start()
    started = time.time()
    if settings.allow_anonymous:
        log.warning("API_ALLOW_ANONYMOUS=1: authentication is DISABLED (development only)")

    def current_version() -> str:
        return settings.model_version

    # ------------------------------------------------------------------ request lifecycle

    @app.before_request
    def begin() -> Response | None:
        g.started = time.perf_counter()
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex
        g.n_records = 0
        api_metrics.in_flight.inc()
        endpoint = request.endpoint
        if endpoint in OPEN_ENDPOINTS or settings.allow_anonymous or endpoint is None:
            return None
        supplied = request.headers.get("X-API-Key", "")
        if not settings.api_key or not hmac.compare_digest(
            supplied.encode("utf-8"), settings.api_key.encode("utf-8")
        ):
            return _error(401, "unauthorized", "missing or invalid API key")
        return None

    @app.after_request
    def finish(response: Response) -> Response:
        route = request.url_rule.rule if request.url_rule else "unmatched"
        elapsed = time.perf_counter() - getattr(g, "started", time.perf_counter())
        api_metrics.in_flight.dec()
        api_metrics.requests.labels(request.method, route, str(response.status_code)).inc()
        api_metrics.latency.labels(route).observe(elapsed)
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        log.info(
            "request",
            extra={
                "request_id": getattr(g, "request_id", None),
                "method": request.method,
                "route": route,
                "status": response.status_code,
                "duration_ms": round(elapsed * 1000, 2),
                "n_records": getattr(g, "n_records", 0),
                "model_version": current_version(),
            },
        )
        return response

    # ------------------------------------------------------------------ errors

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException) -> Response:
        codes = {
            400: "bad_request",
            404: "not_found",
            405: "method_not_allowed",
            413: "payload_too_large",
            415: "unsupported_media_type",
        }
        messages = {
            400: "the request body is not valid JSON",
            413: f"request body is larger than {settings.max_body_bytes} bytes",
            415: 'send the body as JSON with "Content-Type: application/json"',
        }
        status = exc.code or 500
        message = messages.get(status, exc.description or "request failed")
        return _error(status, codes.get(status, "http_error"), message)

    @app.errorhandler(Exception)
    def unexpected(exc: Exception) -> Response:
        log.exception("unhandled error", extra={"request_id": getattr(g, "request_id", None)})
        return _error(500, "internal_error", "internal server error")

    # ------------------------------------------------------------------ helpers

    def require_bundle() -> Bundle | Response:
        bundle_now = holder.get()
        api_metrics.model_loaded.set(1 if bundle_now else 0)
        if bundle_now is None:
            return _error(503, "model_unavailable", "no model is loaded yet; try again shortly")
        return bundle_now

    def read_records(loaded: Bundle, limit: int | None = None) -> list[Any] | Response:
        body = request.get_json()  # 400 malformed, 415 wrong content type, 413 too large
        try:
            records = parse_records(body, settings.max_batch)
        except RequestError as exc:
            return _error(400, "bad_request", str(exc))
        if limit is not None and len(records) > limit:
            return _error(400, "bad_request", f"at most {limit} records per request here")
        problems = [
            {"index": index, "errors": errors}
            for index, record in enumerate(records)
            if (errors := validate_record(record, loaded.inputs))
        ]
        if problems:
            for problem in problems:
                for error in problem["errors"]:
                    api_metrics.validation_failures.labels(error["code"]).inc()
            return _error(
                422,
                "validation_failed",
                f"{len(problems)} of {len(records)} record(s) are invalid",
                problems[:MAX_VALIDATION_DETAILS],
            )
        g.n_records = len(records)
        return records

    # ------------------------------------------------------------------ endpoints

    @app.get("/health")
    def health() -> Response:
        loaded = holder.get()
        api_metrics.model_loaded.set(1 if loaded else 0)
        body = {
            "status": "ok" if loaded else "unavailable",
            "model_loaded": loaded is not None,
            "model_version": current_version() if loaded else None,
            "uptime_s": round(time.time() - started, 1),
        }
        if loaded is None and holder.error:
            body["reason"] = holder.error
        response = jsonify(body)
        response.status_code = 200 if loaded else 503
        return response

    @app.get("/model-info")
    def model_info() -> Response:
        loaded = require_bundle()
        if isinstance(loaded, Response):
            return loaded
        return jsonify({**loaded.info, "request_id": g.request_id})

    @app.post("/predict")
    def predict() -> Response:
        loaded = require_bundle()
        if isinstance(loaded, Response):
            return loaded
        records = read_records(loaded)
        if isinstance(records, Response):
            return records
        columns = [spec["name"] for spec in loaded.inputs]
        try:
            scores = inference.predict_proba(loaded.model, records, columns)
        except ValueError as exc:
            api_metrics.validation_failures.labels("model_input").inc()
            return _error(422, "validation_failed", str(exc))
        version = current_version()
        predictions = []
        for score in scores:
            flagged = int(score >= loaded.threshold)
            predictions.append({"risk_score": round(float(score), 6), "prediction": flagged})
            api_metrics.scores.labels(version).observe(float(score))
            api_metrics.predictions.labels(version, "flagged" if flagged else "not_flagged").inc()
        extra = sorted(
            {name for record in records for name in ignored_fields(record, loaded.inputs)}
        )
        return jsonify(
            {
                "request_id": g.request_id,
                "model_version": version,
                "threshold": loaded.threshold,
                "score_type": "uncalibrated_risk_score",
                "predictions": predictions,
                "ignored_fields": extra,
                "disclaimer": DISCLAIMER,
            }
        )

    @app.post("/explain")
    def explain() -> Response:
        loaded = require_bundle()
        if isinstance(loaded, Response):
            return loaded
        if loaded.background is None:
            return _error(503, "explanations_unavailable", "no explainer background is loaded")
        raw_k = request.args.get("top_k", "5")
        if not raw_k.isdigit() or not 1 <= int(raw_k) <= MAX_TOP_K:
            return _error(400, "bad_request", f"top_k must be a whole number from 1 to {MAX_TOP_K}")
        records = read_records(loaded, limit=settings.explain_max_records)
        if isinstance(records, Response):
            return records
        try:
            from src.models import explain as explainer

            explained = explainer.explain_records(
                loaded.model, loaded.background, records, top_k=int(raw_k)
            )
        except ImportError:
            return _error(503, "explanations_unavailable", "the explainer library is not installed")
        except ValueError as exc:
            return _error(422, "validation_failed", str(exc))
        results = []
        for item in explained:
            score = float(item["probability"])
            results.append(
                {
                    "risk_score": round(score, 6),
                    "prediction": int(score >= loaded.threshold),
                    "log_odds": item["log_odds"],
                    "base_value": item["base_value"],
                    "factors": item["factors"],
                }
            )
        return jsonify(
            {
                "request_id": g.request_id,
                "model_version": current_version(),
                "threshold": loaded.threshold,
                "score_type": "uncalibrated_risk_score",
                "explanations": results,
                "note": "Factors describe the model's behaviour, not causes in the real world.",
                "disclaimer": DISCLAIMER,
            }
        )

    @app.get("/metrics", endpoint="metrics")
    def metrics_endpoint() -> Response:
        payload, content_type = api_metrics.render()
        return Response(payload, mimetype=content_type.split(";")[0], content_type=content_type)

    app.extensions["bundle_holder"] = holder
    app.extensions["api_metrics"] = api_metrics
    return app
