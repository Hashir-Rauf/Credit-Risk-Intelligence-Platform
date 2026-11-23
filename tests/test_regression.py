"""Regression tests: a change to features, model or training code must not make the model
worse than the stored baseline (spec 3.5).

`tests/baselines/synthetic_lr.json` holds the test-split metrics of the logistic-regression
baseline on the seeded synthetic data (tests/conftest.py settings). To refresh it after an
*intended* change, run `PYTHONPATH=. python tests/test_regression.py --update`
(PowerShell: `$env:PYTHONPATH="."` first) and explain why in the PR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from src.data.paths import split_path
from src.models.evaluate import check_regression, compute_metrics

BASELINE_FILE = Path(__file__).parent / "baselines" / "synthetic_lr.json"
MAX_DROP = (
    0.02  # synthetic data is small (225 test rows), so the tolerance is wider than in promotion
)


def load_baseline() -> dict:
    return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))


def test_candidate_does_not_regress_against_the_stored_baseline(pipeline_env):
    import json as _json

    from src.data.paths import metrics_path

    candidate = _json.loads(metrics_path(pipeline_env.params).read_text())["test"]
    regressed = check_regression(candidate, load_baseline()["test"], MAX_DROP)
    assert regressed == [], f"worse than baseline on {regressed}: {candidate} vs {load_baseline()}"


def test_the_baseline_describes_the_current_setup(pipeline_env):
    baseline = load_baseline()
    assert baseline["algorithm"] == "logistic_regression"
    assert baseline["seed"] == pipeline_env.params["seed"]
    assert baseline["n_test"] == len(
        __import__("pandas").read_parquet(split_path(pipeline_env.params, "test"))
    )


def test_the_check_catches_a_model_trained_on_shuffled_labels(iso_params):
    """Sanity check of the check itself: destroying the signal must be flagged."""
    import numpy as np
    import pandas as pd
    from src.data.paths import metrics_path, model_path
    from src.models import train

    from conftest import run_stages

    run_stages(iso_params, "preprocess")
    path = split_path(iso_params, "train")
    frame = pd.read_parquet(path)
    target = iso_params["data"]["target_column"]
    frame[target] = np.random.default_rng(0).permutation(frame[target].to_numpy())
    frame.to_parquet(path)
    train.run(iso_params)
    degraded = json.loads(metrics_path(iso_params).read_text())["test"]
    assert "roc_auc" in check_regression(degraded, load_baseline()["test"], MAX_DROP)
    assert model_path(iso_params).is_file()


def test_the_check_is_silent_for_the_baseline_itself():
    baseline = load_baseline()["test"]
    assert check_regression(baseline, baseline, 0.0) == []


def test_the_threshold_metrics_are_consistent_with_the_confusion_counts():
    test = load_baseline()["test"]
    assert test["tp"] + test["fp"] + test["tn"] + test["fn"] == test["n"]
    assert compute_metrics([0, 1], [0.1, 0.9], 0.5)["roc_auc"] == 1.0


if __name__ == "__main__":
    if "--update" not in sys.argv:
        raise SystemExit("usage: python tests/test_regression.py --update")
    import copy
    import tempfile

    import pandas as pd

    sys.path.insert(0, str(Path(__file__).parent))
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from src.common.config import load_params

    from conftest import isolate_params, run_stages, use_local_mlflow

    with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as patch:
        base = Path(tmp)
        use_local_mlflow(patch, base)
        params = isolate_params(copy.deepcopy(load_params()), base)
        run_stages(params)
        from src.data.paths import metrics_path

        summary = json.loads(metrics_path(params).read_text())
        record = {
            "note": "Test-split metrics of the LR baseline on seeded synthetic data.",
            "algorithm": summary["algorithm"],
            "seed": summary["seed"],
            "n_test": int(len(pd.read_parquet(split_path(params, "test")))),
            "test": {
                k: (round(v, 6) if isinstance(v, float) else v) for k, v in summary["test"].items()
            },
        }
        BASELINE_FILE.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"wrote {BASELINE_FILE}")
