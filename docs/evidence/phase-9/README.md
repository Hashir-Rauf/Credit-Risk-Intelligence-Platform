# Phase 9 evidence: security scans (2026-11-26)

Run locally against the working tree of branch `feat/phase-6-9-delivery`; CI runs the same tools on every push.

| Tool | Scope | Result | File |
|---|---|---|---|
| gitleaks | full git history (42 commits), repo `.gitleaks.toml` | no leaks found | [gitleaks-history.txt](gitleaks-history.txt) |
| bandit (medium and above fail) | `src/` | 0 medium, 0 high; 3 low (informational) | [bandit.txt](bandit.txt) |
| pip-audit | `requirements.lock.txt` | no known vulnerabilities; 2 ignored entries, the one documented accepted risk (diskcache, `PYSEC-2026-2447`) | [pip-audit.txt](pip-audit.txt) |
| Trivy, HIGH and CRITICAL with a fix | API image, drift monitor image, frontend image | 0 findings each | [trivy-summary.txt](trivy-summary.txt) |

The audit of `rules.md` sections 1 and 2, the accepted risks and the secret-scanner exception are in [docs/security.md](../../security.md); risks in [docs/risk-assessment.md](../../risk-assessment.md).
Not covered by a scan here: the CloudFormation templates were linted (`cfn-lint`) but not run against AWS.
