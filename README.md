# Credit Risk Intelligence Platform

End-to-end MLOps system that predicts next-month credit-card default, then serves, monitors and retrains the model. FAST-NUCES MLOps course project (3 students).

> Status: Phase 1 (data + DVC) in progress. See [plan.md](plan.md), [phases.md](phases.md), [rules.md](rules.md).

## Dataset
UCI *Default of Credit Card Clients* (30,000 rows, 23 features). Source: https://archive.ics.uci.edu/dataset/350. Used for coursework only; not a real lending decision tool.

**Licence:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). The data is downloaded at run time and versioned with DVC; it is not redistributed in git.

**Citation:** Yeh, I-C. (2009). *Default of Credit Card Clients* [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C55S3H. Original study: Yeh, I-C. and Lien, C-H. (2009). The comparisons of data mining techniques for the predictive accuracy of probability of default of credit card clients. *Expert Systems with Applications*, 36(2), 2473-2480.

## Prerequisites
Python 3.11+, Git, Docker Desktop (>= 8 GB RAM for kind), Node 20+, `kubectl`, `kind`, AWS CLI v2, DVC.

## Quick start
```bash
git clone <repo-url> && cd <repo>
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
make setup            # install deps + pre-commit hooks
cp .env.example .env  # then fill in values (never commit .env)
```
### Get the data (DVC)
Data is versioned with DVC (not stored in git). Remotes: `s3` (default) and `dagshub`.
```bash
dvc pull -r dagshub   # or: dvc pull   (S3; needs AWS profile `credit-risk`, see .env.example)
dvc repro             # ingest -> validate
dvc dag
```
Remote credentials live in the gitignored `.dvc/config.local`:
```bash
dvc remote modify --local s3 profile <your-aws-profile>
dvc remote modify --local dagshub user <dagshub-user>
dvc remote modify --local dagshub password <dagshub-token>
```
Later phases add: `make up`, deployment and teardown instructions.

## Repository layout
See [plan.md](plan.md) section 4.

## Team
| Member | Role |
|---|---|
| S1 | Data & ML Engineering |
| S2 | MLOps & Automation |
| S3 | Deployment & Reliability |

## Contributing
Branch from `main` (`feat/…`, `fix/…`), open a PR, get one review and green CI. Never push to `main`. See [rules.md](rules.md).
