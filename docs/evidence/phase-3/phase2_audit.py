import json
import sys
import warnings

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
P = yaml.safe_load(open("params.yaml", encoding="utf-8"))
T = P["data"]["target_column"]
ID = P["data"]["id_column"]
raw = pd.read_csv(P["data"]["raw_path"])
tr, va, te = (pd.read_parquet(f"data/processed/{n}.parquet") for n in ("train", "val", "test"))
print("== 1. split integrity")
print("sizes:", len(tr), len(va), len(te), "sum", len(tr) + len(va) + len(te), "raw", len(raw))
ids = [set(d[ID]) for d in (tr, va, te)]
print(
    "id overlap train/val, train/test, val/test:",
    len(ids[0] & ids[1]),
    len(ids[0] & ids[2]),
    len(ids[1] & ids[2]),
)
print("union == raw ids:", (ids[0] | ids[1] | ids[2]) == set(raw[ID]))
print(
    "default rate raw/train/val/test: %.4f %.4f %.4f %.4f"
    % (raw[T].mean(), tr[T].mean(), va[T].mean(), te[T].mean())
)
cols = [c for c in raw.columns if c != ID]
key = lambda d: d[cols].astype(str).agg("|".join, axis=1)
ktr, kte = set(key(tr)), set(key(te))
print(
    "test rows whose exact feature+label twin is in train (duplicate leakage):",
    int(key(te).isin(ktr).sum()),
    "of",
    len(te),
)
print("== 2. recompute test metrics from the saved model")
model = joblib.load("models/model.joblib")
rep = json.load(open("reports/metrics.json", encoding="utf-8"))["test"]
from src.features.preprocessing import model_input_columns

X = te[model_input_columns(P)]
p = model.predict_proba(X)[:, 1]
y = te[T].to_numpy()
fpr, tpr, _ = roc_curve(y, p)
mine = {
    "roc_auc": roc_auc_score(y, p),
    "pr_auc": average_precision_score(y, p),
    "ks": float(np.max(tpr - fpr)),
    "brier": brier_score_loss(y, p),
    "precision": precision_score(y, p >= 0.5),
    "recall": recall_score(y, p >= 0.5),
    "accuracy": float(((p >= 0.5) == y).mean()),
}
for k, v in mine.items():
    print(f"{k:10s} mine={v:.4f} reported={rep[k]:.4f} diff={abs(v-rep[k]):.2e}")
print(
    "model input columns (%d):" % len(model_input_columns(P)),
    "sensitive/ID/target present?",
    [c for c in ("SEX", "MARRIAGE", "AGE", ID, T) if c in model_input_columns(P)],
)
print("== 3. is the model better than trivial?")
base = y.mean()
print("majority-class accuracy: %.4f vs model %.4f" % (1 - base, mine["accuracy"]))
print("constant-prediction Brier: %.4f vs model %.4f" % (base * (1 - base), mine["brier"]))
print("== 4. calibration (class_weight inflates probabilities)")
print("mean predicted prob %.4f vs observed default rate %.4f" % (p.mean(), y.mean()))
bins = pd.qcut(p, 5, duplicates="drop")
print(
    pd.DataFrame({"p": p, "y": y})
    .groupby(bins, observed=True)
    .agg(mean_pred=("p", "mean"), observed=("y", "mean"), n=("y", "size"))
    .round(3)
    .to_string()
)
print("== 5. gate margin / noise")
rng = np.random.default_rng(0)
n = len(y)
aucs = []
for _ in range(1000):
    i = rng.integers(0, n, n)
    if y[i].min() != y[i].max():
        aucs.append(roc_auc_score(y[i], p[i]))
lo, hi = np.percentile(aucs, [2.5, 97.5])
print(
    "test ROC-AUC 95%% bootstrap CI: [%.4f, %.4f]  (gate %.2f)" % (lo, hi, P["gate"]["roc_auc_min"])
)
print(
    "share of bootstrap resamples below the gate: %.1f%%"
    % (100 * np.mean(np.array(aucs) < P["gate"]["roc_auc_min"]))
)
print("== 6. what drives the model")
est = model.named_steps["model"]
names = model.named_steps["prep"].get_feature_names_out()
imp = getattr(est, "feature_importances_", None)
if imp is not None:
    top = sorted(zip(names, imp), key=lambda t: -t[1])[:8]
    print([(n, round(float(v), 3)) for n, v in top])
