# Security notes

Living file (Phase 9 expands it). Scanners run in CI on every PR: gitleaks (secrets, full history),
bandit (static analysis of `src/`), pip-audit (known CVEs in `requirements.lock.txt`), and Trivy
(container image, once `docker/Dockerfile.api` exists). `rules.md` section 1: High/critical findings
block merge unless recorded here as accepted.

## Accepted risks
| ID | Package / finding | Severity | Why accepted | Revisit |
|---|---|---|---|---|
| PYSEC-2026-2447 (CVE-2025-69872) | `diskcache 5.6.3` (pulled in by `dvc-data`), unsafe pickle deserialization in its cache | CVSS v4 local, needs write access to the cache directory | No fixed version exists upstream. We never read an untrusted cache directory: DVC's cache is our own `.dvc/cache`, and no service of ours loads `diskcache`. Not part of the API image. | When `diskcache` or DVC ships a fix; the CI `--ignore-vuln` entry is then removed |
| bandit low-severity notes (3) | see `bandit -r src -c pyproject.toml` | Low | Informational (not failing the build; CI fails at medium and above). | Phase 9 audit |

## Secret-scanner exceptions (`.gitleaks.toml`)
| Where | Rule | Why it is not a secret |
|---|---|---|
| `pipelines/kubeflow/compiled_pipeline.yaml`, lines of the form `secretKey: PHASE5_*` | `generic-api-key` | In a compiled Kubeflow pipeline, `secretKey` is the **name of an entry inside a Kubernetes Secret** (`credit-risk-phase5`) that is mapped to an environment variable. The values are created at run time from the environment by `scripts/phase5.ps1`; the manifest holds only names. |

The exception is deliberately narrow: it needs **both** that exact path **and** that exact line pattern, and only for that one rule.
It was tested: a real-looking value in the same folder, the same pattern in another file, and a plain API key are all still flagged.
Do not widen it; if the compiled pipeline starts to contain real values, the scan must fail.

## Rules of thumb
- Secrets live in `.env` (gitignored), `.dvc/config.local`, GitHub Secrets or AWS Secrets Manager. Never in git.
- If a secret is ever committed: rotate it first, then clean history.
- Pre-commit blocks private keys and secrets locally; CI re-checks on the server (defence in depth).
