# Security notes

Scanners run in CI on every PR: gitleaks (secrets, full history),
bandit (static analysis of `src/`), pip-audit (known CVEs in `requirements.lock.txt`), and Trivy
(container image, once `docker/Dockerfile.api` exists). `rules.md` section 1: High/critical findings
block merge unless recorded here as accepted.

## Accepted risks
| ID | Package / finding | Severity | Why accepted | Revisit |
|---|---|---|---|---|
| PYSEC-2026-2447 (CVE-2025-69872) | `diskcache 5.6.3` (pulled in by `dvc-data`), unsafe pickle deserialization in its cache | CVSS v4 local, needs write access to the cache directory | No fixed version exists upstream. We never read an untrusted cache directory: DVC's cache is our own `.dvc/cache`, and no service of ours loads `diskcache`. Not part of the API image. | When `diskcache` or DVC ships a fix; the CI `--ignore-vuln` entry is then removed |
| bandit low-severity notes (3) | see `bandit -r src -c pyproject.toml` | Low | Informational (not failing the build; CI fails at medium and above). | Reviewed in the Phase 9 audit: no change needed |
| HTTP (no TLS) on the AWS endpoint | design | Medium | An ALB with a certificate needs a domain and costs more than the service. Mitigated by a one-address security group, a 40-character key and simulated data only. | Before any real data: ALB or CloudFront with TLS |

## Secret-scanner exceptions (`.gitleaks.toml`)
| Where | Rule | Why it is not a secret |
|---|---|---|
| `pipelines/kubeflow/compiled_pipeline.yaml`, lines of the form `secretKey: PHASE5_*` | `generic-api-key` | In a compiled Kubeflow pipeline, `secretKey` is the **name of an entry inside a Kubernetes Secret** (`credit-risk-phase5`) that is mapped to an environment variable. The values are created at run time from the environment by `scripts/phase5.ps1`; the manifest holds only names. |

The exception is deliberately narrow: it needs **both** that exact path **and** that exact line pattern, and only for that one rule.
It was tested: a real-looking value in the same folder, the same pattern in another file, and a plain API key are all still flagged.
Do not widen it; if the compiled pipeline starts to contain real values, the scan must fail.

## Phase 9 audit of `rules.md` sections 1 and 2 (2026-11-26)
Scanner output: [evidence/phase-9/](evidence/phase-9/README.md). "Code" means implemented and tested; "not run" means the AWS side has not been applied yet.

| Rule | Status | Proof |
|---|---|---|
| 1. No credentials, keys, tokens, `.env`, kubeconfigs, state in git; `.env.example` placeholders only | pass | `.env` is gitignored (`git check-ignore .env`); `.env.example` holds placeholders; gitleaks on the full history (42 commits): no leaks; pre-commit blocks keys ([evidence/phase-0](evidence/phase-0/precommit_blocks_secret.log)) |
| 1. GitHub to AWS uses OIDC, not static keys | code; not run | `deploy.yml` and `rollback.yml` use `role-to-assume`; no `AWS_ACCESS_KEY*` anywhere in `.github/`; trust policy in `infra/aws/cloudformation/service.yaml`. The provider may be blocked by an SCP (see aws-deployment.md) |
| 1. Runtime secrets from Secrets Manager or env, never baked in | pass (code) | ECS task definition uses `Secrets:`; Kubernetes uses a Secret created at run time; Dockerfiles contain no secrets; the demo log has no key |
| 1. IAM least privilege, no `*:*` | pass (code) | no `Action: "*"` in the templates; `Resource: "*"` appears only for `ecr:GetAuthorizationToken`, task-definition and network *describe* calls, and task listing restricted by an `ecs:cluster` condition, because AWS offers no resource-level permission for them; `iam:PassRole` is limited to two roles and to `ecs-tasks.amazonaws.com` |
| 1. Security groups open only needed ports, to known IPs | pass (code) | one ingress rule (8000) from a parameter that rejects `0.0.0.0/0`; Compose publishes only on `127.0.0.1` |
| 1. Frontend never receives the API key | pass | server-side route handlers; a Playwright test in CI fails if the key reaches the browser |
| 1. gitleaks, bandit, pip-audit, Trivy in CI | pass | `.github/workflows/ci.yml` (the drift monitor image is now built and scanned too) |
| 1. A committed secret is rotated, not just deleted | procedure | stated above in "Rules of thumb"; no incident to date |
| 2. Raw/processed data tracked by DVC only | pass | `git ls-files data` lists only `.gitignore`/`.gitkeep` files |
| 2. Dataset source and licence in the README | pass | README "Dataset" |
| 2. Raw request payloads are not logged | pass | `test_logs_never_contain_submitted_values` and `test_metrics_count_requests_and_predictions_but_never_inputs`. **Documented exception:** the separate pseudonymised prediction log stores the 20 model inputs, which drift detection needs (phases.md step 8.4); it has no direct identifiers, 30-day retention and is off unless `PREDICTION_LOG_PATH` is set |
| 2. Sensitive attributes excluded or justified; fairness per group | pass | `SEX`, `MARRIAGE`, `AGE` are not inputs; [fairness.md](fairness.md) reports per-group metrics, including the failing age-band gaps |
| 2. Not-a-lending-decision disclaimer | pass | in every API response, the UI, README and model card |
| 2. Prediction logs pseudonymised with retention | pass | `src/monitoring/prediction_log.py` (keyed hash, retention sweep) and tests; S3 lifecycle rule in `bootstrap.yaml` |

Other gaps from the Phase 9 checklist and what was done:
| Gap | Action |
|---|---|
| Rate limiting | implemented: per-client sliding window (`RATE_LIMIT_PER_MINUTE`, 429 with `Retry-After`; health and metrics exempt); on in the prod Compose file and the ECS task; off by default |
| Request size limits | already in place: 1 MB body (413), 100 records per request |
| Root containers | API, frontend, monitor, alert-sink run as uid 10001; MLflow as a non-root user; Prometheus, Alertmanager and Grafana use their images' non-root users; prod Compose adds read-only filesystems, dropped capabilities and `no-new-privileges` |
| Secrets in logs | none found; tests assert it for payloads and metrics, and the API key is only in headers |
| Open ports | Compose binds to localhost only (dev override publishes extra ports locally); the Kubernetes API Service is `ClusterIP`; the AWS group admits one address |
| Pod hardening | namespace Pod Security `restricted`; non-root, read-only root filesystem, no privilege escalation, capabilities dropped |

Open items: confirm the hard AWS spend limit; apply and verify the AWS side; add a second reviewer to the `production` Environment.

## Rules of thumb
- Secrets live in `.env` (gitignored), `.dvc/config.local`, GitHub Secrets or AWS Secrets Manager. Never in git.
- If a secret is ever committed: rotate it first, then clean history.
- Pre-commit blocks private keys and secrets locally; CI re-checks on the server (defence in depth).
