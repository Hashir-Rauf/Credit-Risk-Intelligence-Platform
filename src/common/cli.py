"""Uniform entry point for every S1 stage.

One implementation, three callers: `dvc repro`, Kubeflow components and Airflow tasks all
invoke a stage as `python -m src.<pkg>.<module> [--params FILE] [--smoke]` and branch on the
process exit code. A stage module looks like:

    def run(params: dict, smoke: bool) -> dict:   # pure-ish: reads/writes paths from params
        ...

    if __name__ == "__main__":
        raise SystemExit(stage_main(run, description="..."))
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from enum import IntEnum
from typing import Any

from src.common.config import load_params
from src.common.errors import ConfigError, DataValidationError, GateFailedError
from src.common.logging_utils import configure_logging, get_logger
from src.common.seed import set_global_seed

RunFn = Callable[[dict[str, Any], bool], Mapping[str, Any] | None]


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    VALIDATION_FAILED = 2
    GATE_FAILED = 3


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--params", default=None, help="path to params.yaml (default: <repo>/params.yaml)"
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="fast mode for CI/tests: small subset, few trials; never used for real runs",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG|INFO|WARNING|ERROR")
    return parser


def stage_main(run_fn: RunFn, *, description: str, argv: Sequence[str] | None = None) -> int:
    """Parse args, load params, seed RNGs, run the stage, map outcome to an exit code."""
    parser = build_parser(description)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits with 2 on bad usage; 2 means VALIDATION_FAILED here, so remap.
        return int(ExitCode.OK) if exc.code in (0, None) else int(ExitCode.ERROR)

    configure_logging(args.log_level)
    log = get_logger(getattr(run_fn, "__module__", "stage"))

    try:
        params = load_params(args.params)
        set_global_seed(params["seed"])
        outputs = run_fn(params, args.smoke)
    except DataValidationError as exc:
        log.error("data validation failed: %s", exc)
        return int(ExitCode.VALIDATION_FAILED)
    except GateFailedError as exc:
        log.error("quality gate failed: %s", exc)
        return int(ExitCode.GATE_FAILED)
    except ConfigError as exc:
        log.error("configuration error: %s", exc)
        return int(ExitCode.ERROR)
    except Exception:
        log.exception("stage crashed")
        return int(ExitCode.ERROR)

    if outputs:
        log.info("stage finished; outputs: %s", dict(outputs))
    return int(ExitCode.OK)
