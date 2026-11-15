"""Deterministic seeding. All randomness derives from params.yaml `seed` (rules.md §4)."""

from __future__ import annotations

import hashlib
import os
import random

import numpy as np


def derive_seed(base_seed: int, name: str) -> int:
    """Stable sub-seed for a named purpose (e.g. "split", "optuna"); independent of hash()."""
    digest = hashlib.sha256(f"{base_seed}:{name}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def set_global_seed(seed: int) -> None:
    """Seed Python and NumPy global RNGs.

    PYTHONHASHSEED is also exported, but it only affects child processes: the running
    interpreter's hash randomisation is fixed at startup.
    """
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)


def make_rng(base_seed: int, name: str) -> np.random.Generator:
    """Independent, reproducible NumPy Generator for a named purpose."""
    return np.random.default_rng(derive_seed(base_seed, name))
