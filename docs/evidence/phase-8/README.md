# Phase 8 evidence: monitoring, drift, alerts, retraining

Local runs on 2026-11-26 (Compose stack: API, drift monitor, Prometheus, Alertmanager, a local webhook receiver, Grafana; Airflow and Kubeflow on Minikube for the retrain).
Method and limits: [docs/promotion-rollback.md](../../promotion-rollback.md).

> **Drift, delayed labels and the labelled performance numbers are SIMULATED**: resampled held-out test rows with shifted features and their real outcomes replayed as if they had arrived late.

## Dashboard: normal, degraded, alert
| State | Screenshot | What it shows |
|---|---|---|
| Normal traffic (1,000 rows of the `no_drift` batch) | [01-normal-grafana.png](01-normal-grafana.png), [alerts](01-normal-prometheus-alerts.png) | Drift detected 0, per-feature PSI all below 0.02, live AUC about 0.78 (simulated labels), model version panel shows the serving version |
| Drifted traffic (`payment_delays`: PAY_* +2, payments halved) | [02-degraded-grafana.png](02-degraded-grafana.png) | Drift detected 1, drift share and max PSI jump, score distribution moves |
| Alert firing | [03-alert-firing-prometheus-alerts.png](03-alert-firing-prometheus-alerts.png), [Alertmanager](03-alert-firing-alertmanager.png) | `DataDriftDetected` firing (`LivePerformanceDrop` pending, then firing) |
| Resolved | [04-resolved-grafana.png](04-resolved-grafana.png), [alerts](04-resolved-prometheus-alerts.png), [Alertmanager](04-resolved-alertmanager.png) | back to normal after the window slid past the drifted rows and normal traffic returned |

Numbers during the alert ([drift-status-during-alert.txt](drift-status-during-alert.txt)): 1,000 predictions in the window, **drift share 0.5** (10 of 20 inputs: `PAY_1`-`PAY_6`, `PAY_AMT1`-`PAY_AMT3`, `PAY_AMT5`), **max PSI 5.6**, **live AUC 0.55** against 0.78 on normal traffic.
The Evidently HTML report is produced by the monitor (`GET /report`, `/app/state/monitoring/drift_report.html` in the container).

## Alerts triggered on purpose (each fired and resolved)
Notification record: [alert-notifications.log](alert-notifications.log) (every Alertmanager webhook delivery, with UTC times).

| Alert | How it was triggered | Fired → resolved (UTC) | Screenshots |
|---|---|---|---|
| `DataDriftDetected` | drifted traffic, see above (1 min `for`) | 15:31:18 → 15:31:48, then 15:33:48 → 15:35:48 (two cycles; the 3-minute window slid) | 02, 03, 04 |
| `HighErrorRate` (critical, > 5 % for 5 min) | API restarted with a missing model path (`/predict` answers 503) and about 148,000 requests sent over 7 minutes | 15:51:19 → 15:57:49 | [pending](05-error-rate-pending-grafana.png), [firing](05-error-rate-firing-grafana.png), [alerts](05-error-rate-firing-prometheus-alerts.png), [Alertmanager](05-error-rate-firing-alertmanager.png), [resolved](05-error-rate-resolved-grafana.png) |
| `HighLatencyP95` (> 0.5 s for 2 min) | 24 concurrent threads posting 100-record batches | 16:01:49 → 16:07:19 | [firing](06-latency-firing-grafana.png), [alerts](06-latency-firing-prometheus-alerts.png), [resolved](06-latency-resolved-grafana.png) |
| `LivePerformanceDrop` | live AUC under 0.70 on the drifted, labelled traffic | fired 15:49:18 (still firing at the end because the monitor window was lengthened to 1 h for the retrain demo) | 03 |

The error-rate run happened with the rate limit off (it is off by default); with the production setting (600 requests/minute) most of those requests would have been refused with 429, which are not 5xx.

## Retraining
A real drift-triggered chain was run on the live stack:

1. The monitor reported `state: drift` (drift share 0.35 over a 1-hour window, live AUC 0.59; simulated).
2. `drift_watch_dag` read the monitor's `/status` and started `retrain_dag` (it did so on its own schedule and on a manual trigger, which exposed the missing cooldown, see below).
3. `retrain_dag` run `retrain-drift-acceptance` (`reason=drift`): fetch, validate, DVC push, Kubeflow run, poll and **`decide`** all succeeded; the branch took `record_rejection`, `request_approval` was skipped. Task states and log lines: [retrain-run.txt](retrain-run.txt); the decision: [decision.json](decision.json).
4. **Decision: REJECTED.** Challenger v6 against champion v1: ROC-AUC gain +0.0000 (both 0.7883, identical PR-AUC and KS), required +0.005. This is expected: the retrain used the same data and seed, so it reproduced the champion exactly. The champion alias stayed on v1 (`staging` moved to v6; versions 5 and 6 are the two registered runs of this exercise).

What this shows and what it does not:
* It shows the trigger chain, the same-metrics comparison, the rule that rejects a model that does not win, and that nothing is promoted or approved automatically.
* It does **not** show a better challenger being recommended and promoted: no model that beats the champion was produced. That path is covered by unit tests only (`decide`, the alias rules in `pipelines/tests/test_retrain.py`) and the Deploy workflow's promotion mode has **not been run**.
* The DAG compares the runs' recorded test metrics (same seed and test size, which it checks). It does **not** re-evaluate fairness gaps, because the test split lives inside the Kubeflow pods; the decision says so and tells the approver to check `reports/fairness.json`.

Problems found by running it, all fixed:
| Problem | Fix |
|---|---|
| Importing one DAG file from another registered the first DAG twice (`AirflowDagDuplicatedIdException`) | shared helpers moved to `orchestration_common.py` |
| The first `decide` failed: the Airflow container has no test split | compare recorded metrics of both runs instead (above) |
| The watcher triggered a new retrain every ten minutes while drift persisted (three runs queued) | 6-hour cooldown in `drift_watch_dag` (`in_cooldown`, tested); duplicates were cancelled by hand |
| Clearing a finished task changes the DagRun start time, which is part of the request id, so the rerun could not find its own training run | not changed: re-run the DAG instead of clearing tasks (documented in promotion-rollback.md) |


## Reproduce
See "Run the monitoring stack and the drift scenario" in [promotion-rollback.md](../../promotion-rollback.md). The screenshots were taken with Playwright against the live stack.
