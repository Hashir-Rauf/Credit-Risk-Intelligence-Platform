# Phase 6 evidence: AWS deployment and CD

**Nothing in this folder is from a live AWS run, because no AWS resource has been created yet.** Creating them costs money and needs an
explicit go-ahead; an organisation policy may also block the GitHub OIDC provider (see
[docs/aws-deployment.md](../../aws-deployment.md), "Risk to confirm first").

What exists and was checked statically:

| Item | Where | Check performed |
|---|---|---|
| Foundation stack (ECR, bucket, log group, two generated secrets, SNS topic, optional budget) | `infra/aws/cloudformation/bootstrap.yaml` | `cfn-lint` clean |
| Service stack (ECS cluster/service/task definition, security group, task and execution roles, GitHub OIDC role, alarms) | `infra/aws/cloudformation/service.yaml` | `cfn-lint` clean; no `*:*`; wildcard resources only where AWS has no resource-level permission (listed in the doc) |
| Setup, endpoint lookup, teardown with leftover listing | `infra/aws/setup.sh`, `endpoint.sh`, `teardown.sh` | `bash -n` |
| Deploy (CI → approval → build → Trivy → ECR by SHA → ECS → smoke test → rollback) | `.github/workflows/deploy.yml`, `scripts/aws_ecs_deploy.sh`, `scripts/aws_smoke.sh` | YAML parse, `bash -n` |
| Manual rollback | `.github/workflows/rollback.yml`, `scripts/aws_rollback.sh` | YAML parse, `bash -n` |

Exit criteria still to be demonstrated (checklist in the doc): merge deploys with no manual AWS clicks and no stored keys; public `curl` returns a prediction and a request without the key is rejected; a failed CI quality gate means no deployment; rollback; teardown leaves no billable resources.
