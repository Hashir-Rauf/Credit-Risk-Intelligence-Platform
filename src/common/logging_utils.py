"""Consistent, idempotent logging setup. Never log raw request payloads (rules.md §2)."""

from __future__ import annotations

import logging
import os

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_MARK = "_credit_risk_handler"


def configure_logging(level: str | None = None) -> None:
    """Configure the root logger once. Level: argument, else $LOG_LEVEL, else INFO."""
    resolved = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    if not isinstance(logging.getLevelName(resolved), int):
        resolved = "INFO"
    root = logging.getLogger()
    root.setLevel(resolved)
    if any(getattr(h, _HANDLER_MARK, False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
