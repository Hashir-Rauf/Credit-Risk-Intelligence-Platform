"""Cycle 0 tests: config, seeding, logging, stage CLI contract, repo hygiene."""

from __future__ import annotations

import importlib
import logging
import os
import random
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from src.common.cli import ExitCode, stage_main
from src.common.config import (
    REQUIRED_KEYS,
    get_param,
    load_params,
    load_yaml,
    resolve_path,
    validate_params,
)
from src.common.errors import ConfigError, DataValidationError, GateFailedError
from src.common.logging_utils import configure_logging
from src.common.seed import derive_seed, make_rng, set_global_seed

# --------------------------------------------------------------------------- config


class TestConfig:
    def test_real_params_file_is_valid(self, params):
        validate_params(params)

    def test_every_required_key_is_present(self, params):
        for key in REQUIRED_KEYS:
            assert get_param(params, key, None) is not None, key

    def test_get_param_dotted_and_default(self, params):
        assert get_param(params, "split.train") == params["split"]["train"]
        assert get_param(params, "no.such.key", default=7) == 7

    def test_get_param_missing_raises(self, params):
        with pytest.raises(ConfigError, match="missing parameter 'no.such'"):
            get_param(params, "no.such")

    def test_missing_keys_are_all_reported(self, params):
        del params["seed"]
        del params["gate"]
        with pytest.raises(ConfigError) as exc:
            validate_params(params)
        assert "'seed'" in str(exc.value)
        assert "'gate.roc_auc_min'" in str(exc.value)

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda p: p.update(seed=True), "seed"),
            (lambda p: p.update(seed=-1), "seed"),
            (lambda p: p.update(seed="42"), "seed"),
            (lambda p: p["data"].update(source="ftp"), "data.source"),
            (lambda p: p["split"].update(train=0.9), "sum to 1.0"),
            (lambda p: p["split"].update(val=0.0, train=0.85), "(0, 1)"),
            (lambda p: p["split"].update(test="0.15"), "(0, 1)"),
            (lambda p: p["features"].update(sensitive_columns=[]), "sensitive_columns"),
            (lambda p: p["features"].update(sensitive_columns="SEX"), "sensitive_columns"),
            (lambda p: p["features"].update(exclude_sensitive_from_model="yes"), "exclude"),
            (lambda p: p["features"].update(sensitive_columns=["ID"]), "id/target"),
            (lambda p: p["gate"].update(roc_auc_min=0.4), "roc_auc_min"),
            (lambda p: p["gate"].update(roc_auc_min=1.0), "roc_auc_min"),
            (lambda p: p["mlflow"].update(champion_alias="challenger"), "must differ"),
        ],
    )
    def test_invalid_values_rejected(self, params, mutate, message):
        mutate(params)
        with pytest.raises(ConfigError, match=re.escape(message)):
            validate_params(params)

    def test_load_params_from_custom_path(self, params, write_params):
        params["seed"] = 7
        assert load_params(write_params(params))["seed"] == 7

    def test_load_params_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_params(tmp_path / "nope.yaml")

    def test_load_yaml_rejects_non_mapping(self, tmp_path):
        bad = tmp_path / "list.yaml"
        bad.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="mapping"):
            load_yaml(bad)

    def test_load_yaml_rejects_broken_yaml(self, tmp_path):
        bad = tmp_path / "broken.yaml"
        bad.write_text("a: [unclosed\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="invalid YAML"):
            load_yaml(bad)

    def test_resolve_path_relative_and_absolute(self, root, tmp_path):
        assert resolve_path("params.yaml") == root / "params.yaml"
        assert resolve_path(tmp_path) == tmp_path

    def test_sensitive_attributes_excluded_by_default(self, params):
        assert params["features"]["exclude_sensitive_from_model"] is True
        assert {"SEX", "MARRIAGE", "AGE"} <= set(params["features"]["sensitive_columns"])

    def test_registry_uses_aliases_not_production(self, params):
        aliases = {params["mlflow"]["challenger_alias"], params["mlflow"]["champion_alias"]}
        assert aliases == {"challenger", "champion"}

    def test_other_config_files_are_mappings(self, root):
        for name in ("model_params.yaml", "pipeline_config.yaml"):
            assert isinstance(load_yaml(root / "configs" / name), dict), name

    def test_pipeline_config_exit_codes_match_enum(self, root):
        codes = load_yaml(root / "configs" / "pipeline_config.yaml")["exit_codes"]
        assert codes == {
            "ok": ExitCode.OK,
            "error": ExitCode.ERROR,
            "validation_failed": ExitCode.VALIDATION_FAILED,
            "gate_failed": ExitCode.GATE_FAILED,
        }


# --------------------------------------------------------------------------- seeding


class TestSeed:
    def test_derive_seed_is_stable_and_in_range(self):
        assert derive_seed(42, "split") == derive_seed(42, "split")
        assert 0 <= derive_seed(42, "split") <= 0x7FFFFFFF

    def test_derive_seed_differs_by_name_and_base(self):
        assert derive_seed(42, "split") != derive_seed(42, "optuna")
        assert derive_seed(42, "split") != derive_seed(43, "split")

    def test_derive_seed_known_value(self):
        # Guards against accidental changes that would silently alter every split/trial.
        assert derive_seed(42, "split") == 1714860770

    def test_set_global_seed_makes_python_and_numpy_reproducible(self):
        set_global_seed(123)
        first = (random.random(), float(np.random.rand()))
        set_global_seed(123)
        second = (random.random(), float(np.random.rand()))
        assert first == second

    def test_set_global_seed_exports_pythonhashseed(self):
        set_global_seed(99)
        assert os.environ["PYTHONHASHSEED"] == "99"

    def test_make_rng_reproducible_and_independent(self):
        a1 = make_rng(42, "split").random(5)
        a2 = make_rng(42, "split").random(5)
        b = make_rng(42, "other").random(5)
        assert np.array_equal(a1, a2)
        assert not np.array_equal(a1, b)


# --------------------------------------------------------------------------- logging


class TestLogging:
    def test_configure_logging_is_idempotent(self):
        configure_logging("INFO")
        configure_logging("INFO")
        marked = [h for h in logging.getLogger().handlers if getattr(h, "_credit_risk_handler", 0)]
        assert len(marked) == 1

    def test_level_from_argument_and_invalid_falls_back(self):
        configure_logging("DEBUG")
        assert logging.getLogger().level == logging.DEBUG
        configure_logging("not-a-level")
        assert logging.getLogger().level == logging.INFO


# --------------------------------------------------------------------------- stage CLI


class TestStageMain:
    def test_success_runs_stage_with_params_and_smoke_flag(self, params, write_params):
        seen: dict[str, Any] = {}

        def run(p: dict[str, Any], smoke: bool):
            seen["seed"] = p["seed"]
            seen["smoke"] = smoke
            seen["rng"] = random.random()
            return {"out": "x"}

        params["seed"] = 5
        path = write_params(params)
        assert stage_main(run, description="t", argv=["--params", str(path), "--smoke"]) == 0
        assert seen["seed"] == 5
        assert seen["smoke"] is True

        random.seed(5)
        assert seen["rng"] == random.random()  # run_fn saw the seeded global RNG

    def test_smoke_defaults_to_false(self):
        flags: list[bool] = []
        assert stage_main(lambda p, s: flags.append(s), description="t", argv=[]) == 0
        assert flags == [False]

    @pytest.mark.parametrize(
        ("exc", "code"),
        [
            (DataValidationError("bad rows", {"n": 3}), ExitCode.VALIDATION_FAILED),
            (GateFailedError("auc 0.6 < 0.75"), ExitCode.GATE_FAILED),
            (ConfigError("oops"), ExitCode.ERROR),
            (RuntimeError("boom"), ExitCode.ERROR),
        ],
    )
    def test_exceptions_map_to_exit_codes(self, exc, code):
        def run(p, s):
            raise exc

        assert stage_main(run, description="t", argv=[]) == code

    def test_missing_params_file_is_error(self, tmp_path):
        rc = stage_main(
            lambda p, s: None, description="t", argv=["--params", str(tmp_path / "missing.yaml")]
        )
        assert rc == ExitCode.ERROR

    def test_invalid_params_is_error_and_stage_not_run(self, params, write_params):
        params["gate"]["roc_auc_min"] = 0.1
        called: list[int] = []
        rc = stage_main(
            lambda p, s: called.append(1),
            description="t",
            argv=["--params", str(write_params(params))],
        )
        assert rc == ExitCode.ERROR
        assert called == []

    def test_bad_cli_usage_is_error_not_validation_failed(self):
        # argparse natively exits 2, which would be misread as VALIDATION_FAILED.
        rc = stage_main(lambda p, s: None, description="t", argv=["--no-such-flag"])
        assert rc == ExitCode.ERROR
        assert rc != ExitCode.VALIDATION_FAILED

    def test_help_exits_zero(self, capsys):
        assert stage_main(lambda p, s: None, description="t", argv=["--help"]) == 0
        assert "--smoke" in capsys.readouterr().out

    def test_exit_code_propagates_through_a_real_process(self, root, tmp_path):
        """What Airflow/KFP actually see: the process return code."""
        script = tmp_path / "stage_under_test.py"
        script.write_text(
            textwrap.dedent("""
                from src.common.cli import stage_main
                from src.common.errors import GateFailedError

                def run(params, smoke):
                    raise GateFailedError("simulated low AUC")

                raise SystemExit(stage_main(run, description="subprocess test"))
                """),
            encoding="utf-8",
        )
        env = {**os.environ, "PYTHONPATH": str(root)}
        proc = subprocess.run(
            [sys.executable, str(script)], cwd=root, env=env, capture_output=True, text=True
        )
        assert proc.returncode == ExitCode.GATE_FAILED
        assert "quality gate failed" in proc.stderr


# --------------------------------------------------------------------------- repo hygiene


class TestRepoHygiene:
    @pytest.mark.parametrize(
        "module", ["src", "src.common", "src.data", "src.features", "src.models"]
    )
    def test_packages_importable(self, module):
        assert importlib.import_module(module)

    def test_requirements_are_pinned(self, root):
        files = sorted(root.glob("requirements*.txt"))
        assert files, "no requirements files found"
        for f in files:
            for raw in f.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line or line.startswith("-r "):
                    continue
                assert re.match(
                    r"^[A-Za-z0-9_.\-\[\]]+==\S+$", line
                ), f"{f.name}: unpinned '{line}'"

    def test_data_dirs_are_gitignored(self, root):
        ignored = (root / ".gitignore").read_text(encoding="utf-8")
        assert "data/raw/*" in ignored
        assert "data/processed/*" in ignored

    def test_params_paths_are_relative(self, params):
        for key in ("data.raw_dir", "data.processed_dir"):
            assert not Path(get_param(params, key)).is_absolute(), key

    def test_params_file_has_no_secrets(self, root):
        text = (root / "params.yaml").read_text(encoding="utf-8").lower()
        for needle in ("password", "secret", "token", "aws_access", "api_key"):
            assert needle not in text, needle

    def test_params_yaml_round_trips(self, root):
        data = yaml.safe_load((root / "params.yaml").read_text(encoding="utf-8"))
        assert data["data"]["id_column"] == "ID"
