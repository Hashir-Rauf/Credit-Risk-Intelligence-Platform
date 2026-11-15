"""Exception types that stage CLIs translate into process exit codes."""

from __future__ import annotations

from typing import Any


class PipelineError(Exception):
    """Base class for expected, reportable pipeline failures."""


class ConfigError(PipelineError):
    """params.yaml / config file is missing, malformed or has invalid values."""


class IngestionError(PipelineError):
    """Raw data could not be fetched or read (network, S3, missing/corrupt source file)."""


class DataValidationError(PipelineError):
    """Input data failed schema or quality validation (pipeline must stop)."""

    def __init__(self, message: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report or {}


class GateFailedError(PipelineError):
    """A model failed its minimum-performance gate and must not proceed to deployment."""
