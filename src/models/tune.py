"""PLACEHOLDER for @JA-Codex (Phase 2). Not implemented. See docs/HANDOFF-JA-Codex.md.

Spec (phases.md, Phase 2, step 5): Optuna, at least 20 trials per model family, cross-validated
on train only, objective = validation AUC; log each trial as a nested MLflow run.
"""

from __future__ import annotations


def tune(algorithm: str, n_trials: int, seed: int) -> dict:
    """TODO(JA-Codex): return the best hyperparameters for `algorithm`."""
    raise NotImplementedError("Phase 2 - owner: @JA-Codex")
