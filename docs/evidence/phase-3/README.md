# Phase 3 evidence (CI, red/green gate, Phase 0 and Phase 2 loose ends)

Captured 2026-11-23. Repository: `Hashir-Rauf/Credit-Risk-Intelligence-Platform`. Workflow: `.github/workflows/ci.yml`.

## CI runs (GitHub Actions)
| Run | Branch / commit | Result | Notes |
|---|---|---|---|
| [45229774869](https://github.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform/actions/runs/45229774869) | `feat/phase-3-ci` @ `ba59780` | **green**, 7.5 min | First run of the pipeline: tests 7.3 min, lint 0.2, bandit + pip-audit 1.0, gitleaks 0.2, docker skipped (no Dockerfile yet) |
| [45231592266](https://github.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform/actions/runs/45231592266) | `evidence/ci-red-demo` @ `0209f8d` | **RED** (intended) | One deliberately failing test (`assert 1 + 1 == 3`) |
| [45233201820](https://github.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform/actions/runs/45233201820) | `evidence/ci-red-demo` @ `fb45391` | **green** | Same branch after the fix commit removed the failing test |

### Red run, job by job (45231592266)
| Job | Result |
|---|---|
| Lint (ruff, black) | success (25 s) |
| **Tests (pytest + coverage)** | **failed** (8 m 19 s), `Process completed with exit code 1` |
| Security (bandit, pip-audit) | success (58 s) |
| Secret scan (gitleaks, full history) | success (11 s) |
| Docker build + Trivy scan | success, steps skipped (6 s) |
| **CI passed** (the required check) | **failed** (3 s), so the merge button stays blocked once this check is required |

### Green run after the fix, job by job (45233201820)
All six jobs succeeded: lint 11 s, tests 9 m 30 s, security 52 s, gitleaks 6 s, docker 5 s (skipped steps), CI passed 2 s.
Test time varies between 7.3 and 9.5 minutes between runs; it is the only job near the 10-minute target.

The demo branch `evidence/ci-red-demo` is kept only so these runs stay linked to their commits; it must never be merged.

## Phase 0 exit criterion: pre-commit blocks a secret
[`../phase-0/precommit_blocks_secret.log`](../phase-0/precommit_blocks_secret.log): a commit containing a fake GitHub-token
string and a fake private-key block was refused by two independent hooks (`detect-private-key` and `gitleaks`), the commit
exit code was 1 and `HEAD` did not move. The values were randomly generated and are redacted in the log. The hook versions
were also aligned with the CI pins (black 26.10.1, ruff 0.17.5, gitleaks 8.30.1) in the same change.

## Phase 2 review (JA-Codex's work)
Report: [`docs/reviews/phase-2-review.md`](../../reviews/phase-2-review.md).
| File | What it is |
|---|---|
| [`phase2_audit.py`](phase2_audit.py) | Independent audit script (split integrity, metric recomputation, calibration, gate margin, feature importance) |
| [`phase2_audit_output.txt`](phase2_audit_output.txt) | Its output on the committed model: all reported metrics reproduce exactly |
| [`phase2_retrain_reproduction.log`](phase2_retrain_reproduction.log) | Full retrain from raw data (`preprocess, feast, tune, train, evaluate`) on Windows / Python 3.13: same winner, same AUCs; only validation log-loss differs, by 1e-16 |

## Scanner results kept on file
- bandit (`-ll`, medium and above): 0 findings; 3 low-severity notes.
- pip-audit (`requirements.lock.txt`): 1 known vulnerability, `diskcache 5.6.3` (PYSEC-2026-2447), accepted and documented in
  [`docs/security.md`](../../security.md) because no fixed version exists and it is not reachable from our code.
- gitleaks over the full history: no findings.
