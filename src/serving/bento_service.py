"""BentoML service: the alternate packaging and serving path for the same model.

Flask (`src/serving/app.py`) is the primary API. This service exists so the model can also be
built into a **Bento** (a versioned, self-describing artifact) and shipped through BentoML tooling
(`bentoml build`, `bentoml containerize`, BentoCloud or any OCI registry).

It deliberately reuses the Flask API's loader and validation, so both paths score identically:
same artifacts, same `feature_schema.json` rules, same single-threaded estimator.

Authentication is NOT handled here: put a gateway in front of a Bento (the Flask API already
checks `X-API-Key`). That is why the loader is given `API_ALLOW_ANONYMOUS=1` below.

    bentoml build -f bentofile.yaml
    bentoml serve src.serving.bento_service:CreditRisk
"""

from __future__ import annotations

import os
from typing import Any

import bentoml

from src.models import inference
from src.serving.model_loader import load_bundle
from src.serving.schemas import RequestError, ignored_fields, parse_records, validate_record
from src.serving.settings import DISCLAIMER, Settings


@bentoml.service(name="credit_risk", traffic={"timeout": 30})
class CreditRisk:
    def __init__(self) -> None:
        env = {**os.environ, "API_ALLOW_ANONYMOUS": "1"}  # auth is the gateway's job here
        self.settings = Settings.from_env(env)
        self.bundle = load_bundle(self.settings)

    @bentoml.api
    def predict(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Score a batch of raw customer records; same rules and result shape as POST /predict."""
        try:
            parsed = parse_records({"records": records}, self.settings.max_batch)
        except RequestError as exc:
            raise bentoml.exceptions.InvalidArgument(str(exc)) from exc
        problems = [
            {"index": index, "errors": errors}
            for index, record in enumerate(parsed)
            if (errors := validate_record(record, self.bundle.inputs))
        ]
        if problems:
            raise bentoml.exceptions.InvalidArgument(f"invalid records: {problems[:20]}")
        columns = [spec["name"] for spec in self.bundle.inputs]
        scores = inference.predict_proba(self.bundle.model, parsed, columns)
        return {
            "model_version": self.settings.model_version,
            "threshold": self.bundle.threshold,
            "score_type": "uncalibrated_risk_score",
            "predictions": [
                {"risk_score": round(float(s), 6), "prediction": int(s >= self.bundle.threshold)}
                for s in scores
            ],
            "ignored_fields": sorted(
                {name for record in parsed for name in ignored_fields(record, self.bundle.inputs)}
            ),
            "disclaimer": DISCLAIMER,
        }

    @bentoml.api
    def model_info(self) -> dict[str, Any]:
        """Which model this Bento serves (same content as GET /model-info)."""
        return self.bundle.info
