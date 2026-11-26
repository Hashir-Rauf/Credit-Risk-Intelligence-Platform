"""Promote a registered model version to `champion` (or roll back to an earlier champion).

    python -m pipelines.retrain.promote_alias --check --version 4     # refuse versions nobody recommended
    python -m pipelines.retrain.promote_alias --apply --version 4     # move the alias, remember the previous champion

Used by the Deploy workflow, which runs inside the GitHub Environment `production` (a required reviewer there is the
approval). `--check` runs before the deployment; `--apply` only after the deployment and its smoke test succeeded, so
the alias never points at a model that is not serving.

Allowed targets: a version the retrain DAG recommended (`promotion_decision=recommended`) or one that was champion
before (`was_champion=true`, i.e. a rollback). Anything else is refused.

Needs MLFLOW_TRACKING_URI (and credentials) in the environment.
"""

from __future__ import annotations

import argparse
import sys
import time

MODEL_NAME = "credit-risk-default-model"
CHAMPION = "champion"
CHALLENGER = "challenger"


def alias_version(client, name, alias):
    """The model version behind `alias`, or None when the alias does not exist."""
    try:
        return client.get_model_version_by_alias(name, alias)
    except Exception:  # MlflowException RESOURCE_DOES_NOT_EXIST: no such alias
        return None


def current_champion(client, name):
    return alias_version(client, name, CHAMPION)


def check(client, name, version):
    """Raise SystemExit unless `version` may become champion. Returns a short reason."""
    tags = client.get_model_version(name, str(version)).tags
    if tags.get("promotion_decision") == "recommended":
        return "recommended by the retrain DAG"
    if tags.get("was_champion") == "true":
        return "earlier champion (rollback)"
    raise SystemExit(
        f"version {version} was neither recommended by the retrain DAG nor an earlier champion; refusing to promote it"
    )


def apply(client, name, version, now=None):
    """Move `champion` to `version`. Returns {previous, version}."""
    version = str(version)
    previous = current_champion(client, name)
    previous_version = None if previous is None else str(previous.version)
    if previous_version == version:
        return {"previous": previous_version, "version": version, "changed": False}
    if previous_version is not None:
        client.set_model_version_tag(name, previous_version, "was_champion", "true")
        client.set_model_version_tag(name, version, "previous_champion", previous_version)
    client.set_registered_model_alias(name, CHAMPION, version)
    client.set_model_version_tag(name, version, "promoted_at", str(int(now or time.time())))
    challenger = alias_version(client, name, CHALLENGER)
    if challenger is not None and str(challenger.version) == version:
        client.delete_registered_model_alias(name, CHALLENGER)
    return {"previous": previous_version, "version": version, "changed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--model-name", default=MODEL_NAME)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from mlflow.tracking import MlflowClient

    client = MlflowClient()
    if args.check:
        print(f"version {args.version}: {check(client, args.model_name, args.version)}")
    else:
        result = apply(client, args.model_name, args.version)
        print(f"champion: v{result['previous']} -> v{result['version']}")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
