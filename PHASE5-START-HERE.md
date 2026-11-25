# Phase 5 for the Credit Risk Intelligence Platform

Run the following commands from the repository root in Windows PowerShell. Docker Desktop must be running. The Build action reuses your tested credit-risk-ml:phase5 image. You can run it while the Kubeflow API is unavailable.

    .\scripts\phase5.ps1 -Action Build

For the existing Minikube installation that logged MySQL error 1068 on transfer_identities, apply the targeted repair once. It selects a new database for the 2.18.2 API and pins the KFP driver and launcher. Existing database records stay in their original schema. If your API already recovered on the fresh database, the same repair aligns the remaining development-tagged components.

    .\scripts\phase5-repair-existing.ps1

For a fresh cluster use the release installation in docs/kubeflow-setup.md instead. The setup script can reuse saved DVC credentials for the known project. If it requests missing settings, follow section 3 of that guide.

    .\scripts\phase5.ps1 -Action Start

Open Airflow at http://localhost:8085. Unpause ml_orchestrator_dag, select Trigger DAG and choose synthetic for the first run. To capture the assessed real-data run, trigger with source=uci. See docs/orchestration.md for the complete acceptance sequence and docs/evidence/phase-5/local-verification.md for the checks already run during development.

## Package contents

The overlay uses repository-relative paths. Extract it inside the existing Credit Risk project root. It adds the pipeline code, Docker build files, test workflow, tests, scripts and guides. The alternative full-source ZIP is for a separate checkout directory. It includes the supplied Phase 4 source snapshot with Phase 5 integrated and the non-secret DVC remote configuration restored from the project's own runbook evidence. Git history, credentials, virtual environments, raw data, trained model binaries and local MLflow databases are excluded. Restore versioned artifacts with the project's DVC workflow when using the full-source ZIP.
