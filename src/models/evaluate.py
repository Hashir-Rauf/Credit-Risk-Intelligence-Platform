"""PLACEHOLDER for @JA-Codex (Phase 2). Not implemented. See docs/HANDOFF-JA-Codex.md.

Spec (phases.md, Phase 2, step 6): ROC-AUC, PR-AUC, KS, recall/precision at the chosen threshold,
confusion matrix and calibration plot. The quality gate reads thresholds from configs/ (initial
gate: ROC-AUC >= 0.75). The gate result must be machine-readable (the Kubeflow evaluate component
and the CI model test both depend on it).
"""

from __future__ import annotations


def evaluate(model, X, y, threshold: float) -> dict:
    """TODO(JA-Codex): return a metrics dict including roc_auc, pr_auc, ks, recall, precision."""
    raise NotImplementedError("Phase 2 - owner: @JA-Codex")


def passes_gate(metrics: dict, gate: dict) -> bool:
    """TODO(JA-Codex): True only if every gated metric meets its configured threshold."""
    raise NotImplementedError("Phase 2 - owner: @JA-Codex")
