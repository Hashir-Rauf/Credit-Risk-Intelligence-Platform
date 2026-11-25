# Phase 5 local verification

The ML stage commands ran as independent Python processes on Linux with a local DVC remote and an isolated SQLite MLflow registry. Data, model and report directories were cleared between stages to exercise artifact handoffs. Kubeflow SDK 2.18.2 compiled the checked-in IR. Airflow 2.10.5 imported the DAG with all seven tasks.

| Check | Observed result |
|---|---|
| Full archived UCI CSV | 30,000 rows, 25 columns, DVC MD5 a7e80c905840715d8456fe06d935f981 |
| Real-data model evaluation | Test ROC-AUC 0.7883450182006565 on 4,500 observations, above the 0.77 gate |
| Real-data local registration | credit-risk-default-model v1 under staging, linked to local run 6a8c3f2041074a45961bf5a2f2af801e |
| Synthetic evaluation | Test ROC-AUC 0.8202549484337966, passing at 0.77 and failing at 1.0 |
| Registration retry and rejection | Repeating registration on the passing smoke run reused its version. Direct registration of the failing evaluation raised an error. |
| DVC handoff | Published, fetched and SHA-256 verified the validated bytes. A modified CSV was rejected. |
| Model package | OCI index, manifest, config and layer digests verified. The compiled static installer copied the model into a mounted output directory and the model bytes matched. |
| Corrupt Airflow DAG test | fetch_data succeeded, validate failed, and all five subsequent tasks were upstream_failed. The fixture produced six validation findings. |
| Additional tests | Three KFP graph/submission tests, two Airflow DAG/retry tests and three ML snapshot/bundle tests passed in their isolated environments. Ruff and Black passed on new Python files. |
| KFP install renderer | Both release archives verified by SHA-256; kubectl kustomize rendered the cluster-scoped resources and platform-agnostic standalone resources. |

These are local development results. The live Airflow-triggered run on Junaid's Windows/Minikube environment, real DagsHub transfer, retry evidence, dashboard screenshots and final deployment readiness belong in the course submission after running the acceptance steps.
