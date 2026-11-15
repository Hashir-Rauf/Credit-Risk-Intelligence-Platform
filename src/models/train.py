"""PLACEHOLDER for @JA-Codex (Phase 2). Not implemented. See docs/HANDOFF-JA-Codex.md.

Spec (phases.md, Phase 2, step 4): baseline logistic regression, random forest and
XGBoost/LightGBM, with class-imbalance handling (class weights or scale_pos_weight; ~22% positives).
Each run logs to MLflow through the shared helper (algorithm, params, DVC data hash, git SHA,
metrics, duration, plots, model). Tracking URI comes from the environment (DagsHub).
"""

from __future__ import annotations


def train_model(algorithm: str, params: dict, seed: int):
    """TODO(JA-Codex): fit one model family inside an sklearn Pipeline and log it to MLflow."""
    raise NotImplementedError("Phase 2 - owner: @JA-Codex")
