"""API tests (Phase 4): behaviour, validation, auth, error handling and log/metric hygiene.

A real (small) scikit-learn pipeline is trained on the seeded synthetic data and written to a temp
directory; the API loads it through the same loader production uses.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import joblib
import pytest
from sklearn.linear_model import LogisticRegression
from src.common.config import load_params
from src.data.synthetic import make_synthetic_credit_data
from src.features.preprocessing import build_feature_schema, build_pipeline, model_input_columns
from src.models import explain
from src.monitoring.metrics import ApiMetrics
from src.serving import app as app_module
from src.serving.app import create_app
from src.serving.model_loader import BundleHolder, ModelUnavailable, load_bundle, warm_up
from src.serving.schemas import RequestError, parse_records, validate_record
from src.serving.settings import Settings, SettingsError

API_KEY = "test-key-0123456789abcdef"  # gitleaks:allow (fake key, used only by these tests)
HEADERS = {"X-API-Key": API_KEY}


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Train a small model once and write everything the API expects into a temp 'image'."""
    root = tmp_path_factory.mktemp("serving")
    params = load_params()
    frame = make_synthetic_credit_data(1200, seed=3)
    columns = model_input_columns(params)
    target = params["data"]["target_column"]
    pipeline = build_pipeline(params, LogisticRegression(max_iter=500, random_state=0))
    pipeline.fit(frame[columns], frame[target])

    (root / "models").mkdir()
    (root / "reports").mkdir()
    joblib.dump(pipeline, root / "models" / "model.joblib")
    explain.make_background(pipeline, frame, params).to_parquet(
        root / "models" / "explainer_background.parquet"
    )
    schema = build_feature_schema(params)
    (root / "reports" / "feature_schema.json").write_text(json.dumps(schema), encoding="utf-8")
    (root / "reports" / "metrics.json").write_text(
        json.dumps({"test": {"roc_auc": 0.79, "pr_auc": 0.58, "n": 4500, "secret_extra": 1}}),
        encoding="utf-8",
    )
    (root / "reports" / "evaluation.json").write_text(
        json.dumps({"gate": {"metric": "roc_auc", "passed": True, "threshold": 0.77}}),
        encoding="utf-8",
    )
    (root / "dvc.lock").write_text(
        "stages:\n  ingest:\n    outs:\n    - path: data/raw/credit_default.csv\n"
        "      md5: abc123\n",
        encoding="utf-8",
    )
    record = json.loads(frame[columns].iloc[[0]].to_json(orient="records"))[0]
    return {
        "root": root,
        "pipeline": pipeline,
        "frame": frame,
        "columns": columns,
        "record": record,
    }


def make_env(root: Path, **overrides: str) -> dict[str, str]:
    env = {
        "API_KEY": API_KEY,
        "MODEL_PATH": str(root / "models" / "model.joblib"),
        "EXPLAINER_BACKGROUND_PATH": str(root / "models" / "explainer_background.parquet"),
        "FEATURE_SCHEMA_PATH": str(root / "reports" / "feature_schema.json"),
        "METRICS_REPORT_PATH": str(root / "reports" / "metrics.json"),
        "EVALUATION_REPORT_PATH": str(root / "reports" / "evaluation.json"),
        "DVC_LOCK_PATH": str(root / "dvc.lock"),
        "MODEL_VERSION": "7",
        "GIT_SHA": "abc1234",
        "MAX_BATCH": "5",
        "MAX_BODY_BYTES": "4096",
        "EXPLAIN_MAX_RECORDS": "2",
        "WARMUP_EXPLAIN": "0",
    }
    env.update(overrides)
    return env


@pytest.fixture()
def client(artifacts):
    app = create_app(Settings.from_env(make_env(artifacts["root"])))
    return app.test_client()


@pytest.fixture()
def record(artifacts) -> dict[str, Any]:
    return dict(artifacts["record"])


# --------------------------------------------------------------------------- health and info


def test_health_is_open_and_reports_the_model(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok" and body["model_loaded"] is True
    assert body["model_version"] == "7"


def test_health_is_503_when_no_model_is_loaded(tmp_path, artifacts):
    env = make_env(artifacts["root"], MODEL_PATH=str(tmp_path / "missing.joblib"))
    response = create_app(Settings.from_env(env)).test_client().get("/health")
    assert response.status_code == 503
    body = response.get_json()
    assert body["model_loaded"] is False and "not found" in body["reason"]


def test_model_info_describes_the_serving_model(client):
    response = client.get("/model-info", headers=HEADERS)
    assert response.status_code == 200
    info = response.get_json()
    assert info["model_version"] == "7" and info["git_sha"] == "abc1234"
    assert info["algorithm"] == "LogisticRegression"
    assert info["raw_data_md5"] == "abc123"
    assert len(info["model_sha256"]) == 64
    assert info["threshold"] == 0.5
    assert info["score"]["calibrated"] is False and info["score"]["name"] == "risk_score"
    assert info["test_metrics"] == {"roc_auc": 0.79, "pr_auc": 0.58, "n": 4500}  # allow-list only
    assert info["quality_gate"]["passed"] is True
    assert len(info["inputs"]) == 20
    assert set(info["excluded_sensitive"]) == {"SEX", "MARRIAGE", "AGE"}
    assert info["explanations_available"] is True
    assert "not a real lending decision" in info["disclaimer"].lower()


def test_model_info_requires_the_key(client):
    assert client.get("/model-info").status_code == 401


# --------------------------------------------------------------------------- predict


def test_predict_single_record(client, record, artifacts):
    response = client.post("/predict", json=record, headers=HEADERS)
    assert response.status_code == 200
    body = response.get_json()
    assert body["score_type"] == "uncalibrated_risk_score"
    assert body["model_version"] == "7" and body["threshold"] == 0.5
    (prediction,) = body["predictions"]
    assert 0.0 <= prediction["risk_score"] <= 1.0
    assert prediction["prediction"] in (0, 1)
    assert response.headers["X-Request-ID"] == body["request_id"]
    expected = artifacts["pipeline"].predict_proba(
        artifacts["frame"][artifacts["columns"]].iloc[:1]
    )
    assert prediction["risk_score"] == pytest.approx(float(expected[0, 1]), abs=1e-6)


def test_predict_batch_keeps_order(client, artifacts):
    frame = artifacts["frame"][artifacts["columns"]].iloc[:3]
    records = json.loads(frame.to_json(orient="records"))
    response = client.post("/predict", json={"records": records}, headers=HEADERS)
    assert response.status_code == 200
    scores = [p["risk_score"] for p in response.get_json()["predictions"]]
    expected = artifacts["pipeline"].predict_proba(frame)[:, 1]
    assert scores == pytest.approx(list(expected), abs=1e-6)


def test_integral_floats_are_accepted_for_integer_fields(client, record):
    record["PAY_1"] = float(record["PAY_1"])
    assert client.post("/predict", json=record, headers=HEADERS).status_code == 200


def test_sensitive_fields_are_ignored_and_reported_by_name_only(client, record):
    base = client.post("/predict", json=record, headers=HEADERS).get_json()
    other = client.post(
        "/predict", json={**record, "SEX": 2, "AGE": 70, "MARRIAGE": 3}, headers=HEADERS
    ).get_json()
    assert other["ignored_fields"] == ["AGE", "MARRIAGE", "SEX"]
    assert other["predictions"] == base["predictions"]  # they cannot influence the score


@pytest.mark.parametrize(
    ("change", "field", "code"),
    [
        ({"PAY_1": None}, "PAY_1", "missing"),
        ({"LIMIT_BAL": "50000"}, "LIMIT_BAL", "type"),
        ({"LIMIT_BAL": True}, "LIMIT_BAL", "type"),
        ({"PAY_2": 1.5}, "PAY_2", "type"),
        ({"PAY_3": 99}, "PAY_3", "range"),
        ({"LIMIT_BAL": -5}, "LIMIT_BAL", "range"),
        ({"PAY_AMT1": -1}, "PAY_AMT1", "range"),
        ({"EDUCATION": 9}, "EDUCATION", "range"),
        ({"LIMIT_BAL": float("nan")}, "LIMIT_BAL", "type"),
        ({"LIMIT_BAL": float("inf")}, "LIMIT_BAL", "type"),
    ],
)
def test_invalid_fields_are_422_with_the_field_named(client, record, change, field, code):
    response = client.post("/predict", json={**record, **change}, headers=HEADERS)
    assert response.status_code == 422
    error = response.get_json()["error"]
    assert error["code"] == "validation_failed"
    details = error["details"][0]
    assert details["index"] == 0
    assert {"field": field, "code": code} == {
        "field": details["errors"][0]["field"],
        "code": details["errors"][0]["code"],
    }


def test_a_missing_field_is_reported(client, record):
    del record["BILL_AMT3"]
    response = client.post("/predict", json=record, headers=HEADERS)
    assert response.status_code == 422
    assert response.get_json()["error"]["details"][0]["errors"][0]["field"] == "BILL_AMT3"


def test_a_bad_record_in_a_batch_names_its_index(client, record):
    bad = {**record, "PAY_1": 55}
    response = client.post("/predict", json={"records": [record, bad]}, headers=HEADERS)
    assert response.status_code == 422
    assert response.get_json()["error"]["details"][0]["index"] == 1


# --------------------------------------------------------------------------- malformed requests


def test_empty_body_is_400(client):
    assert client.post("/predict", json={}, headers=HEADERS).status_code == 400


def test_malformed_json_is_400(client):
    response = client.post(
        "/predict", data="{not json", content_type="application/json", headers=HEADERS
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "bad_request"
    assert "not valid JSON" in response.get_json()["error"]["message"]


def test_non_json_content_type_is_415(client):
    response = client.post("/predict", data="x=1", content_type="text/plain", headers=HEADERS)
    assert response.status_code == 415


@pytest.mark.parametrize("body", [[], [1, 2], "text", 5])
def test_non_object_bodies_are_400(client, body):
    assert client.post("/predict", json=body, headers=HEADERS).status_code == 400


def test_oversize_body_is_413(client, record):
    response = client.post("/predict", json={**record, "junk": "x" * 6000}, headers=HEADERS)
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "payload_too_large"


def test_oversize_batch_is_400(client, record):
    response = client.post("/predict", json={"records": [record] * 6}, headers=HEADERS)
    assert response.status_code == 400 and "maximum is 5" in response.get_json()["error"]["message"]


@pytest.mark.parametrize("records", [[], "x", {"a": 1}])
def test_bad_records_container_is_400(client, records):
    assert client.post("/predict", json={"records": records}, headers=HEADERS).status_code == 400


def test_unknown_route_and_wrong_method_are_json_errors(client):
    missing = client.get("/nope", headers=HEADERS)
    assert missing.status_code == 404 and missing.get_json()["error"]["code"] == "not_found"
    wrong = client.get("/predict", headers=HEADERS)
    assert wrong.status_code == 405 and wrong.get_json()["error"]["code"] == "method_not_allowed"


# --------------------------------------------------------------------------- auth


def test_missing_and_wrong_keys_are_401(client, record):
    assert client.post("/predict", json=record).status_code == 401
    assert client.post("/predict", json=record, headers={"X-API-Key": "nope"}).status_code == 401
    assert (
        client.post("/predict", json=record, headers={"X-API-Key": API_KEY + "x"}).status_code
        == 401
    )


def test_health_and_metrics_do_not_need_a_key(client):
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_anonymous_mode_is_explicit_and_works_without_a_key(artifacts, record):
    env = make_env(artifacts["root"], API_ALLOW_ANONYMOUS="1")
    del env["API_KEY"]
    anonymous = create_app(Settings.from_env(env)).test_client()
    assert anonymous.post("/predict", json=record).status_code == 200


def test_the_service_refuses_to_start_without_a_key():
    with pytest.raises(SettingsError, match="API_KEY"):
        Settings.from_env({})


def test_a_short_key_is_rejected():
    with pytest.raises(SettingsError, match="16"):
        Settings.from_env({"API_KEY": "short"})


@pytest.mark.parametrize(
    ("name", "value"), [("MAX_BATCH", "abc"), ("MAX_BATCH", "0"), ("MAX_BODY_BYTES", "10")]
)
def test_numeric_settings_are_validated(name, value):
    with pytest.raises(SettingsError, match=name):
        Settings.from_env({"API_KEY": API_KEY, name: value})


# --------------------------------------------------------------------------- explain


def test_explain_returns_top_factors(client, record):
    response = client.post("/explain?top_k=3", json=record, headers=HEADERS)
    assert response.status_code == 200
    (item,) = response.get_json()["explanations"]
    assert len(item["factors"]) == 3
    assert {"feature", "label", "contribution", "direction"} <= set(item["factors"][0])
    assert 0.0 <= item["risk_score"] <= 1.0


@pytest.mark.parametrize("top_k", ["0", "11", "abc", "-1"])
def test_explain_validates_top_k(client, record, top_k):
    assert client.post(f"/explain?top_k={top_k}", json=record, headers=HEADERS).status_code == 400


def test_explain_limits_the_batch_size(client, record):
    response = client.post("/explain", json={"records": [record] * 3}, headers=HEADERS)
    assert response.status_code == 400


def test_explain_is_503_without_a_background(artifacts, record, tmp_path):
    env = make_env(artifacts["root"], EXPLAINER_BACKGROUND_PATH=str(tmp_path / "none.parquet"))
    app = create_app(Settings.from_env(env))
    response = app.test_client().post("/explain", json=record, headers=HEADERS)
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "explanations_unavailable"


# --------------------------------------------------------------------------- errors, metrics, logs


def test_unexpected_errors_are_500_without_details(client, record, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("SECRET internal detail /srv/path")

    monkeypatch.setattr(app_module.inference, "predict_proba", boom)
    response = client.post("/predict", json=record, headers=HEADERS)
    assert response.status_code == 500
    text = response.get_data(as_text=True)
    assert "SECRET" not in text and "Traceback" not in text and "/srv" not in text
    body = response.get_json()["error"]
    assert body["code"] == "internal_error" and body["request_id"]


def test_request_id_is_echoed_or_replaced(client):
    kept = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert kept.headers["X-Request-ID"] == "trace-123"
    replaced = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert replaced.headers["X-Request-ID"] != "bad id with spaces"
    assert len(replaced.headers["X-Request-ID"]) == 32


def test_responses_carry_security_headers(client):
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


def test_metrics_count_requests_and_predictions_but_never_inputs(artifacts, record):
    metrics = ApiMetrics()
    app = create_app(Settings.from_env(make_env(artifacts["root"])), metrics=metrics)
    test_client = app.test_client()
    unique = {**record, "LIMIT_BAL": 123457}
    assert test_client.post("/predict", json=unique, headers=HEADERS).status_code == 200
    assert (
        test_client.post("/predict", json={**unique, "PAY_1": 55}, headers=HEADERS).status_code
        == 422
    )
    text = test_client.get("/metrics").get_data(as_text=True)
    assert 'api_requests_total{method="POST",route="/predict",status="200"} 1.0' in text
    assert 'api_requests_total{method="POST",route="/predict",status="422"} 1.0' in text
    assert 'api_predictions_total{model_version="7",outcome=' in text
    assert 'api_validation_failures_total{code="range"} 1.0' in text
    assert "api_model_loaded 1.0" in text
    assert "123457" not in text


def test_logs_never_contain_submitted_values(artifacts, record):
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, log_record: logging.LogRecord) -> None:
            captured.append(log_record)

    handler = Capture()
    app_module.log.addHandler(handler)
    try:
        app = create_app(Settings.from_env(make_env(artifacts["root"])))
        unique = {**record, "LIMIT_BAL": 987654}
        app.test_client().post("/predict", json=unique, headers=HEADERS)
        app.test_client().post("/predict", json={**unique, "PAY_1": 55}, headers=HEADERS)
    finally:
        app_module.log.removeHandler(handler)
    assert captured, "expected request log lines"
    flattened = " ".join(str(vars(item)) for item in captured)
    assert "987654" not in flattened
    assert any(item.__dict__.get("n_records") == 1 for item in captured)


# --------------------------------------------------------------------------- loader and schemas


def test_loader_reports_a_missing_model(artifacts, tmp_path):
    env = make_env(artifacts["root"], MODEL_PATH=str(tmp_path / "nope.joblib"))
    with pytest.raises(ModelUnavailable, match="not found"):
        load_bundle(Settings.from_env(env))


def test_loader_rejects_a_corrupt_model_file(artifacts, tmp_path):
    bad = tmp_path / "bad.joblib"
    bad.write_bytes(b"this is not a pickle")
    with pytest.raises(ModelUnavailable, match="could not read"):
        load_bundle(Settings.from_env(make_env(artifacts["root"], MODEL_PATH=str(bad))))


def test_loader_requires_a_valid_schema(artifacts, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    with pytest.raises(ModelUnavailable, match="schema"):
        load_bundle(Settings.from_env(make_env(artifacts["root"], FEATURE_SCHEMA_PATH=str(schema))))


def test_holder_picks_up_a_model_that_appears_later(artifacts, tmp_path):
    target = tmp_path / "late.joblib"
    settings = Settings.from_env(make_env(artifacts["root"], MODEL_PATH=str(target)))
    holder = BundleHolder(settings)
    assert holder.get() is None and "not found" in (holder.error or "")
    joblib.dump(artifacts["pipeline"], target)
    holder._last_attempt = 0.0  # skip the cool-down
    assert holder.get() is not None and holder.error is None


def test_model_uri_loads_through_mlflow(artifacts, monkeypatch):
    pytest.importorskip("mlflow", reason="serving image does not ship mlflow")
    import mlflow.sklearn

    calls = []
    monkeypatch.setattr(
        mlflow.sklearn, "load_model", lambda uri: calls.append(uri) or artifacts["pipeline"]
    )
    uri = "models:/credit-risk-default-model@champion"
    bundle = load_bundle(Settings.from_env(make_env(artifacts["root"], MODEL_URI=uri)))
    assert calls == [uri] and bundle.info["model_source"] == uri


def test_a_failing_mlflow_load_is_reported_without_details(artifacts, monkeypatch):
    pytest.importorskip("mlflow", reason="serving image does not ship mlflow")
    import mlflow.sklearn

    def fail(_uri):
        raise OSError("token=abc-secret")

    monkeypatch.setattr(mlflow.sklearn, "load_model", fail)
    env = make_env(artifacts["root"], MODEL_URI="models:/x@champion")
    with pytest.raises(ModelUnavailable) as exc:
        load_bundle(Settings.from_env(env))
    assert "abc-secret" not in str(exc.value)


def test_validate_record_unit(artifacts):
    inputs = build_feature_schema(load_params())["inputs"]
    assert validate_record(artifacts["record"], inputs) == []
    assert validate_record("nope", inputs)[0]["code"] == "type"
    assert {e["field"] for e in validate_record({}, inputs)} == {s["name"] for s in inputs}


def test_parse_records_unit():
    assert parse_records({"a": 1}, 3) == [{"a": 1}]
    assert parse_records({"records": [{"a": 1}, {"b": 2}]}, 3) == [{"a": 1}, {"b": 2}]
    for bad in ({}, {"records": []}, {"records": [1] * 4}, [1]):
        with pytest.raises(RequestError):
            parse_records(bad, 3)


def test_the_serving_model_runs_single_threaded(artifacts):
    rf_like = artifacts["pipeline"]
    rf_like[-1].n_jobs = 4  # LogisticRegression has n_jobs too; pretend training used 4
    bundle = load_bundle(Settings.from_env(make_env(artifacts["root"])))
    assert bundle.model[-1].n_jobs == 1


def test_warm_up_scores_and_explains_a_valid_record(artifacts):
    bundle = load_bundle(Settings.from_env(make_env(artifacts["root"])))
    warm_up(bundle)  # must not raise: the synthetic record built from the schema is valid


def test_warm_up_runs_in_the_background_without_blocking_start(artifacts):
    env = make_env(artifacts["root"], WARMUP_EXPLAIN="1")
    app = create_app(Settings.from_env(env))
    assert app.test_client().get("/health").status_code == 200


# --------------------------------------------------------------------------- BentoML parity


@pytest.fixture()
def bento(artifacts, monkeypatch):
    pytest.importorskip("bentoml", reason="the slim serving image does not ship bentoml")
    for key, value in make_env(artifacts["root"]).items():
        monkeypatch.setenv(key, value)
    from src.serving.bento_service import CreditRisk

    return CreditRisk.inner()


def test_bento_scores_exactly_like_the_flask_api(bento, client, artifacts):
    frame = artifacts["frame"][artifacts["columns"]].iloc[:4]
    records = json.loads(frame.to_json(orient="records"))
    via_flask = client.post("/predict", json={"records": records}, headers=HEADERS).get_json()
    via_bento = bento.predict(records)
    assert via_bento["predictions"] == via_flask["predictions"]
    assert via_bento["score_type"] == via_flask["score_type"] == "uncalibrated_risk_score"


def test_bento_rejects_invalid_records_like_the_api(bento, record):
    import bentoml

    with pytest.raises(bentoml.exceptions.InvalidArgument, match="PAY_1"):
        bento.predict([{**record, "PAY_1": 55}])
    with pytest.raises(bentoml.exceptions.InvalidArgument):
        bento.predict([])


def test_bento_model_info_matches(bento, client):
    flask_info = client.get("/model-info", headers=HEADERS).get_json()
    info = bento.model_info()
    for key in ("algorithm", "threshold", "model_sha256", "raw_data_md5"):
        assert info[key] == flask_info[key]
