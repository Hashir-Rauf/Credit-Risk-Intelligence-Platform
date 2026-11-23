"""Feast feature store: definitions, offline (training) and online (serving) retrieval,
feature-quality checks, and the `feast` pipeline stage.

    python -m src.features.feast_features [--params params.yaml] [--smoke]

What is stored: the raw model inputs (`RAW_INPUT_COLUMNS`), split into three feature views
(payment history, billing, account). Engineered features are NOT stored: they are computed
inside the saved model pipeline, so training and serving cannot drift apart. SEX, MARRIAGE and
AGE are never stored (contract: docs/contracts-s1.md section 8).

UCI has no timestamps, so every customer gets a SIMULATED snapshot time, a pure function of
(seed, customer id) inside `feast.simulated_start` + `simulated_days`. Everything derived from
it is labelled simulated (rules.md section 9). Training rows are looked up `label_lag_days`
after the snapshot, which is what makes the retrieval point-in-time correct.

Offline store: parquet files (`data/feast/source/`). Online store: SQLite. Both paths come from
params.yaml; `--smoke` runs live under `data/smoke/feast/`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.common.cli import stage_main
from src.common.errors import ConfigError, DataValidationError, FeatureNotFoundError, IngestionError
from src.common.io import write_json, write_parquet
from src.common.logging_utils import get_logger
from src.common.seed import derive_seed, make_rng
from src.data.paths import (
    feast_dir,
    feast_online_store_path,
    feast_registry_path,
    feast_report_path,
    feast_source_path,
    raw_csv_path,
)
from src.data.schema import BILL_AMT_COLUMNS, PAY_AMT_COLUMNS, PAY_COLUMNS
from src.features.preprocessing import RAW_INPUT_COLUMNS, _require_validated

log = get_logger(__name__)

TIMESTAMP_FIELD = "event_timestamp"
ENTITY_NAME = "customer"

# Feature view -> columns it serves. Together they must cover RAW_INPUT_COLUMNS exactly.
FEATURE_GROUPS: dict[str, list[str]] = {
    "payment_history": list(PAY_COLUMNS),
    "billing": [*BILL_AMT_COLUMNS, *PAY_AMT_COLUMNS],
    "account": ["LIMIT_BAL", "EDUCATION"],
}
SENSITIVE_COLUMNS = ("SEX", "MARRIAGE", "AGE")


def _feast():
    """Import Feast lazily (it is heavy) with its telemetry switched off."""
    os.environ.setdefault("FEAST_USAGE", "False")
    import feast

    return feast


# --------------------------------------------------------------------------- timestamps


def _splitmix64(values: np.ndarray) -> np.ndarray:
    z = values.astype(np.uint64) + np.uint64(0x9E3779B97F4A7C15)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return z ^ (z >> np.uint64(31))


def simulated_window(params: dict[str, Any]) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(str(params["feast"]["simulated_start"])).replace(tzinfo=UTC)
    return start, start + timedelta(days=params["feast"]["simulated_days"])


def simulated_timestamps(ids: Any, params: dict[str, Any]) -> pd.Series:
    """SIMULATED snapshot time per customer: a pure function of (seed, id), so any subset of
    customers gets the same times without needing the full table."""
    ids = np.asarray(ids, dtype=np.int64)
    if (ids < 0).any():
        raise ConfigError("customer ids must be non-negative integers")
    start, _ = simulated_window(params)
    span = np.uint64(params["feast"]["simulated_days"] * 86400)
    with np.errstate(over="ignore"):
        mixed = ids.astype(np.uint64) ^ np.uint64(derive_seed(params["seed"], "feast:timestamps"))
        offsets = (_splitmix64(mixed) % span).astype(np.int64)
    stamps = pd.Timestamp(start) + pd.to_timedelta(offsets, unit="s")
    return pd.Series(pd.DatetimeIndex(stamps).as_unit("ns"), name=TIMESTAMP_FIELD)


# --------------------------------------------------------------------------- source data


def build_source_frames(raw: pd.DataFrame, params: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """One frame per feature view: customer_id, event_timestamp and the view's columns."""
    id_column = params["data"]["id_column"]
    key = params["feast"]["entity_join_key"]
    missing = [c for c in (id_column, *RAW_INPUT_COLUMNS) if c not in raw.columns]
    if missing:
        raise IngestionError(f"raw data is missing columns {missing}")
    if raw[id_column].duplicated().any():
        raise DataValidationError("customer ids must be unique to build a feature store")
    base = pd.DataFrame(
        {
            key: raw[id_column].astype("int64").to_numpy(),
            TIMESTAMP_FIELD: simulated_timestamps(raw[id_column], params),
        }
    )
    frames = {}
    for view, columns in FEATURE_GROUPS.items():
        frame = base.copy()
        for column in columns:
            frame[column] = raw[column].astype("int64").to_numpy()
        frames[view] = frame.sort_values(key).reset_index(drop=True)
    return frames


# --------------------------------------------------------------------------- store


def build_definitions(params: dict[str, Any], smoke: bool = False) -> list[Any]:
    """Entity + feature views, reading from the parquet sources for this run."""
    feast = _feast()
    from feast.types import Int64

    key = params["feast"]["entity_join_key"]
    entity = feast.Entity(name=ENTITY_NAME, join_keys=[key], value_type=feast.ValueType.INT64)
    ttl = timedelta(days=params["feast"]["ttl_days"])
    views = []
    for view, columns in FEATURE_GROUPS.items():
        source = feast.FileSource(
            name=f"{view}_source",
            path=str(feast_source_path(params, view, smoke)),
            timestamp_field=TIMESTAMP_FIELD,
        )
        views.append(
            feast.FeatureView(
                name=view,
                entities=[entity],
                ttl=ttl,
                schema=[feast.Field(name=c, dtype=Int64) for c in columns],
                source=source,
                tags={"timestamps": "simulated", "sensitive_attributes": "excluded"},
            )
        )
    return [entity, *views]


def repo_config(params: dict[str, Any], smoke: bool = False) -> Any:
    _feast()
    from feast.infra.offline_stores.dask import DaskOfflineStoreConfig
    from feast.infra.online_stores.sqlite import SqliteOnlineStoreConfig
    from feast.repo_config import RegistryConfig, RepoConfig

    return RepoConfig(
        project=params["feast"]["project"],
        provider="local",
        registry=RegistryConfig(path=str(feast_registry_path(params, smoke))),
        online_store=SqliteOnlineStoreConfig(path=str(feast_online_store_path(params, smoke))),
        offline_store=DaskOfflineStoreConfig(),
        entity_key_serialization_version=3,
    )


def get_store(params: dict[str, Any], smoke: bool = False) -> Any:
    feast = _feast()
    feast_dir(params, smoke).mkdir(parents=True, exist_ok=True)
    return feast.FeatureStore(config=repo_config(params, smoke))


def close_store(store: Any) -> None:
    """Release the SQLite online-store connection. Windows cannot delete or replace a database
    file while a connection to it is open, so every store we open ourselves is closed after use."""
    try:
        online = store._get_provider().online_store  # Feast has no public close
        connection = getattr(online, "_conn", None)
        if connection is not None:
            connection.close()
            online._conn = None
    except Exception as exc:  # best effort: never fail a stage over cleanup
        log.debug("could not close the online store connection: %s", exc)


def feature_refs() -> list[str]:
    return [f"{view}:{column}" for view, columns in FEATURE_GROUPS.items() for column in columns]


# --------------------------------------------------------------------------- retrieval


def training_entity_frame(ids: Any, params: dict[str, Any]) -> pd.DataFrame:
    """Entity rows for point-in-time retrieval: lookup time = simulated snapshot + label lag."""
    key = params["feast"]["entity_join_key"]
    ids = pd.Series(np.asarray(ids, dtype=np.int64))
    lookup = simulated_timestamps(ids, params) + timedelta(days=params["feast"]["label_lag_days"])
    return pd.DataFrame({key: ids.to_numpy(), TIMESTAMP_FIELD: lookup})


def _finish(frame: pd.DataFrame, key: str, ids: np.ndarray) -> pd.DataFrame:
    """Order like `ids`, require every customer to have every feature, restore int64."""
    frame = frame.drop_duplicates(subset=key).set_index(key)
    missing = [int(i) for i in ids if i not in frame.index]
    incomplete = (
        [] if missing else [int(i) for i in ids if frame.loc[i, RAW_INPUT_COLUMNS].isna().any()]
    )
    problem = missing or incomplete
    if problem:
        raise FeatureNotFoundError(
            f"no complete features for {len(problem)} customer(s), e.g. {problem[:5]}", problem
        )
    out = frame.loc[ids, RAW_INPUT_COLUMNS].astype("int64")
    out.insert(0, key, ids)
    return out.reset_index(drop=True)


def get_training_features(
    params: dict[str, Any], ids: Any, smoke: bool = False, store: Any = None
) -> pd.DataFrame:
    """Offline (point-in-time) retrieval of RAW_INPUT_COLUMNS for `ids`, one row per id, in
    the order given. Raises FeatureNotFoundError if any customer has no features at its
    lookup time (that would mean a time leak or a missing row, never a silent NaN)."""
    store = store or get_store(params, smoke)
    key = params["feast"]["entity_join_key"]
    ids = np.asarray(ids, dtype=np.int64)
    frame = store.get_historical_features(
        entity_df=training_entity_frame(ids, params), features=feature_refs()
    ).to_df()
    return _finish(frame, key, ids)


def get_online_features(
    params: dict[str, Any], ids: Any, smoke: bool = False, store: Any = None
) -> pd.DataFrame:
    """Online (serving) retrieval of RAW_INPUT_COLUMNS for `ids`; raises FeatureNotFoundError
    for unknown customers so the API can answer 404."""
    owned = store is None
    store = store or get_store(params, smoke)
    key = params["feast"]["entity_join_key"]
    ids = np.asarray(ids, dtype=np.int64)
    if ids.size == 0:
        raise ValueError("no customer ids requested")
    try:
        response = store.get_online_features(
            features=feature_refs(), entity_rows=[{key: int(i)} for i in ids]
        ).to_df()
    finally:
        if owned:
            close_store(store)
    return _finish(response, key, ids)


def attach_feast_features(
    frames: dict[str, pd.DataFrame], params: dict[str, Any], smoke: bool = False
) -> dict[str, pd.DataFrame]:
    """Replace the raw feature columns of each split by what the offline store returns.
    A no-op when `features.use_feast_for_training` is false. Row order and all other
    columns (ID, target, sensitive attributes for fairness) are untouched."""
    if not params["features"]["use_feast_for_training"]:
        return frames
    id_column = params["data"]["id_column"]
    key = params["feast"]["entity_join_key"]
    all_ids = pd.concat([f[id_column] for f in frames.values()]).drop_duplicates()
    retrieved = get_training_features(params, all_ids.to_numpy(), smoke).rename(
        columns={key: id_column}
    )
    out = {}
    for name, frame in frames.items():
        merged = frame.drop(columns=RAW_INPUT_COLUMNS).merge(
            retrieved, on=id_column, how="left", validate="one_to_one"
        )
        out[name] = merged[list(frame.columns)]
    return out


# --------------------------------------------------------------------------- quality checks


def _check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _sample_ids(ids: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    n = min(params["feast"]["parity_sample"], len(ids))
    rng = make_rng(params["seed"], "feast:sample")
    return np.sort(rng.choice(np.sort(ids), size=n, replace=False))


def quality_checks(
    frames: dict[str, pd.DataFrame], params: dict[str, Any], smoke: bool, store: Any
) -> list[dict[str, Any]]:
    """Feature-quality validation (plan.md Phase 2): nulls, uniqueness, timestamps, sensitive
    columns, materialisation (freshness), and offline/online/source parity."""
    key = params["feast"]["entity_join_key"]
    start, end = simulated_window(params)
    checks: list[dict[str, Any]] = []

    stored = {c for cols in FEATURE_GROUPS.values() for c in cols}
    leaked = sorted(stored & set(SENSITIVE_COLUMNS))
    checks.append(
        _check("no_sensitive_features", not leaked, f"sensitive columns in store: {leaked}")
    )
    checks.append(
        _check(
            "views_cover_model_inputs",
            sorted(stored) == sorted(RAW_INPUT_COLUMNS),
            "feature views must serve exactly the model's raw inputs",
        )
    )

    sizes = {v: len(f) for v, f in frames.items()}
    checks.append(_check("same_rows_in_every_view", len(set(sizes.values())) == 1, str(sizes)))
    checks.append(
        _check(
            "unique_customers",
            all(not f[key].duplicated().any() for f in frames.values()),
            "one snapshot per customer",
        )
    )

    limit = params["feast"]["max_null_fraction"]
    worst = max(
        float(f[cols].isna().mean().max())
        for v, f in frames.items()
        for cols in [FEATURE_GROUPS[v]]
    )
    checks.append(_check("null_fraction", worst <= limit, f"worst {worst:.4f} (max {limit})"))

    stamps = pd.concat([f[TIMESTAMP_FIELD] for f in frames.values()])
    inside = bool((stamps >= pd.Timestamp(start)).all() and (stamps < pd.Timestamp(end)).all())
    checks.append(
        _check("timestamps_in_simulated_window", inside, f"{stamps.min()} .. {stamps.max()}")
    )

    reference = frames["account"].set_index(key)
    ids = _sample_ids(reference.index.to_numpy(), params)
    expected = pd.concat(
        [frames[v].set_index(key).loc[ids, FEATURE_GROUPS[v]] for v in FEATURE_GROUPS], axis=1
    )
    expected = expected[RAW_INPUT_COLUMNS].astype("int64")
    expected.insert(0, key, ids)
    expected = expected.reset_index(drop=True)

    for name, fetch in (
        (
            "online_store_materialised_and_matches_source",
            lambda: get_online_features(params, ids, smoke, store),
        ),
        (
            "offline_retrieval_matches_source",
            lambda: get_training_features(params, ids, smoke, store),
        ),
    ):
        try:
            got = fetch()
            ok = got.equals(expected)
            detail = f"{len(ids)} customers compared"
        except FeatureNotFoundError as exc:
            ok, detail = False, str(exc)
        checks.append(_check(name, ok, detail))

    # Point in time: asking for a moment before the snapshot must not return the snapshot.
    early = training_entity_frame(ids[:20], params)
    early[TIMESTAMP_FIELD] = simulated_timestamps(ids[:20], params) - timedelta(seconds=1)
    leaked_rows = store.get_historical_features(entity_df=early, features=feature_refs()).to_df()
    has_values = bool(len(leaked_rows) and leaked_rows[RAW_INPUT_COLUMNS].notna().any().any())
    checks.append(
        _check(
            "point_in_time_no_future_leak",
            not has_values,
            "lookups before the snapshot returned no values",
        )
    )
    return checks


# --------------------------------------------------------------------------- stage


def run(params: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    _require_validated(params, smoke)
    csv = raw_csv_path(params, smoke)
    if not csv.is_file():
        raise IngestionError(f"raw data not found: {csv} (run ingestion first)")

    frames = build_source_frames(pd.read_csv(csv), params)
    for view, frame in frames.items():
        write_parquet(feast_source_path(params, view, smoke), frame)

    for stale in (feast_registry_path(params, smoke), feast_online_store_path(params, smoke)):
        stale.unlink(missing_ok=True)  # rebuilt from scratch: outputs depend only on the inputs
    store = get_store(params, smoke)
    try:
        store.apply(build_definitions(params, smoke))
        start, end = simulated_window(params)
        store.materialize(start_date=start - timedelta(days=1), end_date=end + timedelta(days=1))
        checks = quality_checks(frames, params, smoke, store)
    finally:
        close_store(store)
    status = "passed" if all(c["passed"] for c in checks) else "failed"
    stamps = frames["account"][TIMESTAMP_FIELD]
    report = {
        "status": status,
        "project": params["feast"]["project"],
        "entity": params["feast"]["entity_join_key"],
        "n_customers": int(len(stamps)),
        "smoke": smoke,
        "timestamps": {
            "kind": "simulated (UCI has no timestamps)",
            "window_start": start.isoformat(),
            "window_days": params["feast"]["simulated_days"],
            "first": stamps.min().isoformat(),
            "last": stamps.max().isoformat(),
            "label_lag_days": params["feast"]["label_lag_days"],
        },
        "feature_views": {
            v: {"features": FEATURE_GROUPS[v], "rows": int(len(frames[v]))} for v in FEATURE_GROUPS
        },
        "excluded_sensitive": list(SENSITIVE_COLUMNS),
        "checks": checks,
    }
    write_json(feast_report_path(params, smoke), report)
    failed = [c["name"] for c in checks if not c["passed"]]
    if failed:
        raise DataValidationError(f"feature-store quality checks failed: {failed}", report)
    log.info("feature store ready: %d customers, %d checks passed", len(stamps), len(checks))
    return {
        "customers": int(len(stamps)),
        "checks_passed": len(checks),
        "report": str(feast_report_path(params, smoke)),
    }


if __name__ == "__main__":
    raise SystemExit(
        stage_main(run, description="Build the Feast feature store and check feature quality")
    )
