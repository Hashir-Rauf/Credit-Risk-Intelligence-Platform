# Continuous integration (Phase 3)

Workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). Owner: `@Hashir-Rauf`.
It runs on every push to any branch, every pull request, and on demand (Actions tab > CI > Run workflow).

## What runs

| Job | What it checks | Fails the build when |
|---|---|---|
| Lint | `ruff check .`, `black --check .` | any lint or formatting issue |
| Tests | full `pytest` suite, parallel (`-n auto --dist loadfile`, one worker per module so expensive fixtures are built once), with coverage (`pyproject.toml`: `fail_under = 70`) | a test fails or coverage drops under 70% |
| Security | `bandit -r src -ll`, `pip-audit` on `requirements.lock.txt` | a medium+ bandit finding, or a known CVE not listed in `docs/security.md` |
| Secret scan | gitleaks over the **full git history** | a credential-shaped string is found anywhere in history |
| Docker | builds the API and frontend images, **boots the API container without a model** and checks it answers 503, enforces the API key (401) and runs as uid 10001, then Trivy-scans both images | the smoke test fails, or HIGH/CRITICAL findings with a fix available (vulnerabilities and embedded secrets) |
| Frontend | `npm ci`, ESLint, type-check, unit tests, production build, a Playwright browser smoke test against a mock API (proves the API key never reaches the browser), `npm audit` | any step fails, or a high-severity advisory in production dependencies |
| **CI passed** | aggregate of the six jobs above | any of them did not succeed |

`CI passed` is the one status to mark **required** on `main` (see below).

Measured: serial, the suite grew from 7 to 11.6 minutes as phases landed; in parallel it takes about 5 minutes locally. The first run on GitHub took **7.5 minutes** end to end (tests 7.3 min, everything else about 1 minute each),
inside the 10 minute target. Evidence: [`docs/evidence/phase-3/`](evidence/phase-3/README.md).

## Reproducibility of the CI environment
- Python 3.11 on `ubuntu-latest`, installed from `requirements.lock.txt` (compiled by `uv` for py3.11/linux)
  plus the pinned tools in `requirements-ci.txt`. Nothing floats.
- Third-party actions are pinned to **commit SHAs** (the tag is in a comment), so a moved tag cannot change what runs.
- The workflow token is read-only (`permissions: contents: read`); the checkout step does not persist credentials.
- Concurrent runs on the same branch cancel each other, so only the newest commit is tested.

## Run the same checks locally
```bash
pip install -r requirements.lock.txt -r requirements-ci.txt
make lint test security          # same commands as CI
pre-commit install               # hooks (ruff, black, gitleaks, private-key check) run on every commit
```
The pre-commit hook versions match the CI pins, so a commit that passes the hooks passes the lint job.

## One-time GitHub setting (needs repository admin)
Settings > Rules (or Branches) > `main`:
1. Require a pull request before merging, with 1 approval.
2. **Require status checks to pass** and add **`CI passed`** (it appears in the list after the first run).
3. Require branches to be up to date before merging.
4. Optional: "Require approval of the most recent reviewable push" blocks a maintainer from pushing the last
   commit and approving it themselves. It is a good rule, but it needs a second reviewer who is not the pusher.

## Reading a red run
- Open the run from the PR's Checks tab, then the failing job. Tests produce `junit.xml` and `coverage.xml`
  (artifact `test-reports`, kept 14 days).
- Reproduce locally with the commands above. If gitleaks fails on a real secret: rotate the secret first,
  then remove it from history (deleting the commit is not enough).
- To accept a known vulnerability, add it to `docs/security.md` with a reason **and** add `--ignore-vuln <ID>`
  to the pip-audit step (and to `make security`). Review these entries when upstream ships a fix.

## What a bad change looks like (evidence)
A deliberately failing test turns the run red and `CI passed` fails, which blocks the merge button once the check is
required. The same branch goes green after the fix. Links to both runs are in `docs/evidence/phase-3/`.
The regression suite (`tests/test_regression.py`) also proves the quality gate itself works: it trains a model on
shuffled labels and asserts the regression check flags it.
