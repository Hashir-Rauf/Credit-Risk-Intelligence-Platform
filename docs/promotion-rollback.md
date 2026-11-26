# Monitoring, drift, retraining, promotion and rollback (Phase 8)

Owner: `@Hashir-Rauf` (metrics, dashboards, alerts, retrain DAG, promotion). Modelling inputs (reference data, simulated drift batches, the
promotion rules in `params.yaml`) come from `@JA-Codex`: [contracts-s1.md](contracts-s1.md) section 8c.
Evidence: [`docs/evidence/phase-8/`](evidence/phase-8/README.md).

> **Everything drifted or labelled here is SIMULATED.** The drift batches are resampled held-out test rows with shifted features
> (`python -m src.models.drift`), and the "late labels" are the real outcomes of those same rows replayed after the fact.
> Nothing in this document describes real customers.

## The loop
```
API ──► prediction log (pseudonymised, 30-day retention) ──► drift monitor ──► /metrics ─► Prometheus ─► Grafana
                                          │  (PSI per feature, data quality,        │
                                          │   live AUC/recall from replayed labels) └► alert rules ─► Alertmanager ─► notification
                                          └─► /status ◄── drift_watch_dag (every 10 min)
                                                              │ drift or performance drop, and no retrain in the last 6 h
                                                              ▼
 retrain_dag (also weekly): fetch ► validate ► DVC push ► Kubeflow run ► decide ► branch
                                                                            │ recommended          │ rejected
                                                                            ▼                      ▼
                                                  challenger alias + approval request     reasons recorded, champion keeps serving
                                                                            │
              Deploy workflow, input model_version=N, GitHub Environment "production" (a reviewer approves)
                   check ► fetch model ► build ► scan ► deploy ► smoke test ► only then: champion alias := N
```

## What is measured
| Signal | Where it comes from | Dashboard / alert |
|---|---|---|
| Request rate, error rate (5xx), p50/p95/p99 latency, up, model loaded | API `/metrics` (route templates only, never payloads) | Grafana row 1-2; alerts `ApiDown`, `HighErrorRate` (> 5 % for 5 min), `HighLatencyP95` (> 0.5 s for 2 min) |
| **Model version per prediction**, flagged vs not flagged, score distribution | `api_predictions_total{model_version}`, `api_risk_score` | Grafana row 3 |
| Input drift | monitor: PSI per input feature against the training reference; share of drifted features (PSI ≥ 0.2) | `monitor_drift_share`, `monitor_feature_psi`; alert `DataDriftDetected` (> 30 % of features for 1 min) |
| Data-quality failures | monitor: logged cells outside the feature schema; API `api_validation_failures_total` | alert `DataQualityFailures` |
| Live performance (SIMULATED labels) | monitor: logged predictions joined with replayed labels | `monitor_live_auc`, `monitor_live_recall`; alert `LivePerformanceDrop` (AUC < 0.70) |
| Process CPU / memory | `process_*` metrics from each API pod or container | Grafana row 5 |
| Infrastructure on AWS | CloudWatch Logs and alarms ([aws-deployment.md](aws-deployment.md)) | alarms `5xx`, `p95-latency` |

The **prediction log** ([`src/monitoring/prediction_log.py`](../src/monitoring/prediction_log.py)) stores request id, a keyed hash of the customer id (only if the caller sends `X-Customer-Id`), time, model version, score and the 20 model inputs. It stores no name, e-mail, raw customer id or sensitive attribute (`SEX`, `MARRIAGE`, `AGE` are not API inputs). Rows older than `PREDICTION_LOG_RETENTION_DAYS` (30) are deleted. This store is separate from the application log, which never contains payloads.

## Run the monitoring stack and the drift scenario
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build api monitor prometheus alertmanager alert-sink grafana
# Grafana http://localhost:3001 (admin / $GRAFANA_ADMIN_PASSWORD) -> Credit risk -> "Credit risk service"
python scripts/simulate_drift.py --scenario no_drift                 # normal traffic
python scripts/simulate_drift.py --scenario payment_delays           # drifted traffic (PAY_* +2, payments halved)
docker compose exec -T monitor python -m src.monitoring.drift labels - < reports/monitoring/labels.jsonl   # replayed labels (SIMULATED)
docker compose logs alert-sink                                       # every alert notification, firing and resolved
```
`MONITOR_WINDOW_HOURS` (default 24) is the span of predictions the monitor judges; the local demo used 3 minutes so that an alert could resolve quickly.
`alert-sink` is a local webhook receiver; for real e-mail or Slack, add a receiver to [`monitoring/alertmanager.yml`](../monitoring/alertmanager.yml) (comments show how; keep webhook URLs and passwords in mounted files, not in git).

## Investigation checklist (when `DataDriftDetected` fires)
1. **Is it real?** Open the Evidently report (`GET /report` on the monitor, or `reports/monitoring/drift_report.html`) and the per-feature PSI panel. Which features moved, and by how much? A single feature at PSI 0.2 is noise; many at 1+ is a regime change.
2. **Is it the data pipeline?** Check data quality (`monitor_data_quality_failed_cells`, `api_validation_failures_total`) and the request log for a new client or an upstream change (units, encoding: e.g. `PAY_*` shifted by a constant is a coding change, not behaviour).
3. **Is it hurting?** Look at live AUC/recall (when labels exist) and the score distribution. Drift without performance loss is a watch item; both together justify retraining.
4. **Is the reference still valid?** If the population changed for a legitimate reason, the reference (training data) must be refreshed by a retrain, not the threshold relaxed.
5. **Decide:** retrain (the DAG may already have started: `drift_watch_dag` triggers it), roll back, or accept and record why.

## Retraining
[`retrain_dag`](../pipelines/airflow/dags/retrain_dag.py) runs weekly and whenever [`drift_watch_dag`](../pipelines/airflow/dags/retrain_dag.py) sees drift or a live-AUC drop (checked every 10 minutes through the monitor's `/status`). The watcher will not trigger another retrain while one is active or started within the last 6 hours (`RETRAIN_COOLDOWN_HOURS`): drift persists after a retrain request, and without this guard it would retrain over and over.

The data tasks and the Kubeflow run are exactly the Phase 5 ones (validation failure stops the DAG; a model below the quality gate is never registered). The new step is **`decide`** ([`pipelines/retrain/decision.py`](../pipelines/retrain/decision.py)):

1. the model this run registered (`staging`) and the current `champion` are compared on the **test metrics MLflow recorded for each run** (same seed and test size; the step refuses to judge if they differ);
2. the predefined rules in `params.yaml` → `promotion` decide, using `src.models.promote.decide`: ROC-AUC gain ≥ **+0.005**, PR-AUC and KS drop by at most 0.01, and no fairness gap (approval rate, TPR, FPR, AUC across SEX / age band / MARRIAGE) grows by more than 0.02;
3. the outcome is written to the MLflow model version (`promotion_decision=recommended|rejected`) and to the run's `decision.json`;
4. **recommended** → alias `challenger` is set and the DAG asks for approval; **rejected** → reasons are logged and nothing else changes.

The DAG never moves `champion`. A test asserts that no task in it calls the alias API.

## Promotion (manual, approved)
Run the **Deploy** workflow (Actions → Deploy → Run workflow) with `model_version = N`. It runs in the `production` Environment, so a required reviewer must approve; the reviewer is the human gate that `rules.md` section 6 requires.
1. `promote_alias --check`: refuses any version that was neither `recommended` by the retrain DAG nor a former champion.
2. `fetch_model`: reads `model.joblib`, `feature_schema.json`, `metrics.json`, `evaluation.json` out of the version's `phase5/model-image.oci.tar` (exactly the evaluated files).
3. The gate check on that `evaluation.json`, build (model baked in), Trivy, push (git SHA tag), ECS deployment, smoke test.
4. **Only after the smoke test passes**: `promote_alias --apply` moves `champion` to N, tags the old champion `was_champion=true` and the new one `previous_champion=<old>`.

A deployment that fails is rolled back by the workflow and never touches the alias, so `champion` always names a model that is serving.

## Rollback
| What went wrong | Do this |
|---|---|
| The new deployment is unhealthy | Automatic: the workflow points the service back (see [aws-deployment.md](aws-deployment.md)) |
| The new model is worse in production | Run **Deploy** with `model_version = <previous champion>`. The check accepts it because it is tagged `was_champion`; the alias returns to it after the smoke test. The previous version number is in the `previous_champion` tag of the one you are leaving |
| A bad image, same model | Run **Rollback** (`previous`, a revision number or a git SHA) |
| Local Kubernetes | `kubectl rollout undo deploy/api` ([kubernetes.md](kubernetes.md)) |

## Controlled drift scenario (what was run)
[`scripts/simulate_drift.py`](../scripts/simulate_drift.py) → dashboard shifts → `DataDriftDetected` fires → the watcher starts `retrain_dag` → the pipeline trains and registers a model → `decide` compares it with the champion → rejected or recommended. See the evidence README for the run, the numbers and the screenshots.

## Known limits
* The drift monitor judges a sliding window of the prediction log; a window that mixes normal and drifted traffic reports a share between the two (the first half of an incident is under-reported).
* The DAG does not re-evaluate the **fairness** gaps (the test split is inside the Kubeflow pods); it records that and asks the approver to check `reports/fairness.json`. `python -m src.models.promote` evaluates them where the split exists.
* Do not *clear* tasks of a finished retrain run: the request id includes the DagRun start time, which a clear resets. Trigger a new run.
* A retrain on the same data cannot beat the champion by the required 0.5 pt (the live run reproduced the champion exactly), so the live demo shows the **rejection** path. The recommendation path is covered by the unit tests of `decide` and of the alias rules, not by a live better model.
* Live performance uses replayed labels. Real labels arrive weeks later; the alert rule is what a real one would look like, but its evidence here is simulated.
* The prediction log is SQLite shared through a volume (Compose) or exported to S3 (ECS); it is not a database for high volume.
