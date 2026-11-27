# Phase 6 evidence: AWS deployment and CD

## Live run, 2026-11-27 (account 922844966900, ap-southeast-2, Free plan, credits only)
The foundation was created with `infra/aws/setup.sh` (both stacks succeeded, including the GitHub OIDC provider, so the policy risk in
[docs/aws-deployment.md](../../aws-deployment.md) did not materialise). The Deploy workflow then ran from `main` through the `production`
Environment (reviewer approval): OIDC role, DVC pull, quality gate, image build with the model baked in, Trivy, push to ECR by git SHA, ECS
deployment, wait for stability and smoke test all succeeded (5 min 23 s).

| Check | Result |
|---|---|
| ECS service | ACTIVE, desired 1, running 1, rollout COMPLETED, task definition `credit-risk-api:2`, 0.25 vCPU / 0.5 GB |
| `GET /health` over the public address | `{"status":"ok","model_loaded":true,"model_version":"1",...}` |
| `POST /predict` without the API key | HTTP 401 |
| `POST /predict` with the key (sample record) | HTTP 200, `risk_score` 0.612, `prediction` 1, model version 1 |
| Security group | one /32 only (the operator's address); no 0.0.0.0/0 |
| CloudWatch alarms | `credit-risk-api-5xx` and `credit-risk-api-p95-latency` exist, state OK |
| Alert e-mail | SNS subscription is **PendingConfirmation** (the recipient has not clicked the confirmation link yet) |

Problems the live run found, all fixed through PRs: the OIDC subject now carries numeric owner and repository IDs (the role trusted the plain
form: `AccessDenied` until fixed, found by reading the subject in CloudTrail); the DVC S3 remote was missing the two model objects (pushed);
the deploy role has no `ecr:ListImages` (the step now uses `DescribeImages`); the operator scripts were not marked executable in git.

**Frontend (2026-11-27):** deployed as a second container of the task (revision `credit-risk-api:4`, 0.5 vCPU / 1 GB) with an own ECR repository. Smoke test passed including the frontend check; from outside: `GET /` 200 (title "Credit Risk Intelligence Platform", scoring form rendered), `GET /api/health` `{"frontend":"ok","api":"ok","api_status":200}`, `POST /api/predict` through the frontend returned a score without any key from the client. The first attempt (revision 3) rolled back automatically because the smoke script needed `python3`, which the local machine lacks; it now uses `jq`. The service stack change set was previewed first and contained no task definition or service change.

Still to demonstrate: rollback run, a failed gate or failed smoke test not deploying, alarm firing, Cost Explorer, teardown.

Static checks done before the live run:

| Item | Where | Check performed |
|---|---|---|
| Foundation stack (ECR, bucket, log group, two generated secrets, SNS topic, optional budget) | `infra/aws/cloudformation/bootstrap.yaml` | `cfn-lint` clean |
| Service stack (ECS cluster/service/task definition, security group, task and execution roles, GitHub OIDC role, alarms) | `infra/aws/cloudformation/service.yaml` | `cfn-lint` clean; no `*:*`; wildcard resources only where AWS has no resource-level permission (listed in the doc) |
| Setup, endpoint lookup, teardown with leftover listing | `infra/aws/setup.sh`, `endpoint.sh`, `teardown.sh` | `bash -n` |
| Deploy (CI → approval → build → Trivy → ECR by SHA → ECS → smoke test → rollback) | `.github/workflows/deploy.yml`, `scripts/aws_ecs_deploy.sh`, `scripts/aws_smoke.sh` | YAML parse, `bash -n` |
| Manual rollback | `.github/workflows/rollback.yml`, `scripts/aws_rollback.sh` | YAML parse, `bash -n` |

Exit criteria still to be demonstrated (checklist in the doc): merge deploys with no manual AWS clicks and no stored keys; public `curl` returns a prediction and a request without the key is rejected; a failed CI quality gate means no deployment; rollback; teardown leaves no billable resources.

## Screenshots of the deployed interface (2026-11-27, from the AWS task)
[aws-01-score-empty](screenshots/aws-01-score-empty.png), [aws-02-score-higher-risk](screenshots/aws-02-score-higher-risk.png),
[aws-03-score-lower-risk](screenshots/aws-03-score-lower-risk.png), [aws-04-model-and-data](screenshots/aws-04-model-and-data.png)
(shows the deployed git SHA and the test metrics), [aws-05-monitoring](screenshots/aws-05-monitoring.png),
[aws-06-admin](screenshots/aws-06-admin.png), [aws-07-phone-width](screenshots/aws-07-phone-width.png).
