# Credit Risk Intelligence Platform

End-to-end MLOps system that predicts next-month credit-card default, then serves, monitors and retrains the model. FAST-NUCES MLOps course project (3 students).

> Status: Phase 0 (setup). See [plan.md](plan.md), [phases.md](phases.md), [rules.md](rules.md).

## Dataset
UCI *Default of Credit Card Clients* (30,000 rows, 23 features). Licence: CC BY 4.0. Source: https://archive.ics.uci.edu/dataset/350. Used for coursework only; not a real lending decision tool.

## Prerequisites
Python 3.11+, Git, Docker Desktop (>= 8 GB RAM for kind), Node 20+, `kubectl`, `kind`, AWS CLI v2, DVC.

## Quick start
```bash
git clone <repo-url> && cd <repo>
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
make setup            # install deps + pre-commit hooks
cp .env.example .env  # then fill in values (never commit .env)
```
Later phases add: `dvc pull`, `make repro`, `make up`, deployment and teardown instructions.

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
