# Phase 5 acceptance run (2026-11-26)

One Airflow installation (`airflow standalone`, SequentialExecutor, image `credit-risk-airflow:phase5`) driving KFP 2.18.2 on the `credit-risk` minikube profile, with the shared DagsHub MLflow and DVC remote. Screenshots are in `screenshots/`.

| Scenario (Airflow run id) | Parameters | Outcome | Evidence |
|---|---|---|---|
| `scheduled__2026-11-15T00:00:00+00:00` | source=uci | 7/7 tasks success, one KFP run, gate passed, model registered as staging v4 | `airflow-01-grid.png`, `airflow-02-real-uci-graph.png`, `kfp-01-runs.png`, `kfp-02-real-uci-run-graph.png` |
| `acceptance-corrupt` | source=corrupt | `validate` failed on attempt 1 (exit 2, no retries); dvc_push, trigger_kfp_run, poll_run, check_gate and notify upstream_failed; nothing reached DVC or KFP | `airflow-03-corrupt-graph.png` |
| `acceptance-gate-rejected-v2` | source=uci, strict_gate=true | KFP pipeline succeeded, `package_register` skipped; `check_gate` failed on attempt 1 (exit 3, no retries) with `GateRejected: Quality gate rejected training run 8539c208...`; `notify` upstream_failed; run has `gate_passed=false` and zero registered versions | registry output below |
| `acceptance-retry` | source=synthetic, strict_gate=true, retry_demo=true | `fetch_data` attempt 1 raised the deliberate error and went UP_FOR_RETRY; attempt 2 (about 2 minutes later) succeeded; `trigger_kfp_run` ran once (one attempt); the strict gate then rejected the smoke model as designed | task attempt logs |

`acceptance-gate-rejected` was the first gate-rejected attempt. It ran on the previous image, where a rejection was retried every two minutes, and was failed by hand when Airflow restarted onto the fixed image. Details of the problems found along the way: `found-and-fixed/`.

## Registry state after all runs (`credit-risk-default-model`)

```
version 1 run 7b0c754e aliases [] tags {'source_run_id': '7b0c754e8572'}
version 2 run 89611484 aliases [] tags {'phase5_oci_digest': 'sha256:6012f', 'source_run_id': '896114841435'}
version 3 run edb2105b aliases [] tags {'phase5_oci_digest': 'sha256:d9f4b', 'source_run_id': 'edb2105b8dc6'}
version 4 run 42064a25 aliases [] tags {'phase5_oci_digest': 'sha256:8eaf1', 'source_run_id': '42064a25992b'}
run 8539c208 gate_passed false request dbe4eaaa registered_versions 0
run 42064a25 gate_passed true request f3114262 registered_versions 1
run edb2105b gate_passed true request 90db259e registered_versions 1
run 89611484 gate_passed true request 90db259e registered_versions 1
champion -> v1
staging -> v4
```

Serving still uses `champion` (v1). The pipeline only moves `staging`. v3 came from the first live run, which collided with another installation's run; see `found-and-fixed/01-gate-collision.txt`.
