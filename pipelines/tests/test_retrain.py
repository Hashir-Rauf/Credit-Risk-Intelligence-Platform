"""Retrain support that needs no Airflow: reading a model package out of the registry's OCI archive."""

import tarfile
from pathlib import Path

import pytest
from pipelines.phase5 import oci
from pipelines.retrain import fetch_model


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(
        oci.platform, "machine", lambda: "x86_64"
    )  # the builder maps this to an architecture
    installer = tmp_path / "install"
    installer.write_bytes(b"#!installer")
    files = {}
    for name in ("model.joblib", "feature_schema.json", "metrics.json", "evaluation.json"):
        path = tmp_path / name
        path.write_bytes(f"content of {name}".encode())
        files[name] = path
    output = tmp_path / "image.oci.tar"
    oci.build_image(output, files, "run-123", installer=installer)
    return output


def test_extract_places_the_four_files_where_the_api_image_expects_them(archive, tmp_path):
    root = tmp_path / "out"
    written = fetch_model.extract(archive, root)
    assert written == sorted(fetch_model.WANTED.values())
    assert (root / "models/model.joblib").read_bytes() == b"content of model.joblib"
    assert (root / "reports/evaluation.json").read_bytes() == b"content of evaluation.json"
    assert not (root / "install-model").exists()  # the installer binary is never extracted


def test_a_package_missing_a_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(oci.platform, "machine", lambda: "x86_64")
    installer = tmp_path / "install"
    installer.write_bytes(b"x")
    only = tmp_path / "model.joblib"
    only.write_bytes(b"m")
    image = tmp_path / "partial.tar"
    oci.build_image(image, {"model.joblib": only}, "run-1", installer=installer)
    with pytest.raises(ValueError, match="incomplete"):
        fetch_model.extract(image, tmp_path / "out")


def test_names_outside_the_allowed_set_are_ignored(archive, tmp_path):
    # An archive is only trusted for the four known names; nothing else can be written.
    assert set(fetch_model.WANTED) == {
        "model/model.joblib",
        "model/feature_schema.json",
        "model/metrics.json",
        "model/evaluation.json",
    }
    with tarfile.open(archive) as image:
        assert "index.json" in image.getnames()
    fetch_model.extract(archive, tmp_path / "o")
    written = {
        str(p.relative_to(tmp_path / "o")).replace("\\", "/")
        for p in Path(tmp_path / "o").rglob("*")
        if p.is_file()
    }
    assert written == set(fetch_model.WANTED.values())


class FakeClient:
    """Just enough of MlflowClient for the promotion rules."""

    def __init__(self, versions, aliases):
        self.tags = {str(v): dict(t) for v, t in versions.items()}
        self.aliases = dict(aliases)

    def get_model_version(self, name, version):
        return type("V", (), {"tags": self.tags[str(version)], "version": str(version)})

    def get_model_version_by_alias(self, name, alias):
        if alias not in self.aliases:
            raise KeyError(alias)
        return type("V", (), {"version": self.aliases[alias]})

    def set_model_version_tag(self, name, version, key, value):
        self.tags[str(version)][key] = value

    def set_registered_model_alias(self, name, alias, version):
        self.aliases[alias] = str(version)

    def delete_registered_model_alias(self, name, alias):
        self.aliases.pop(alias, None)


def test_only_recommended_or_former_champions_may_be_promoted():
    from pipelines.retrain import promote_alias

    client = FakeClient(
        {
            "2": {"promotion_decision": "rejected"},
            "3": {"promotion_decision": "recommended"},
            "1": {"was_champion": "true"},
            "9": {},
        },
        {"champion": "1"},
    )
    assert "recommended" in promote_alias.check(client, "m", "3")
    assert "rollback" in promote_alias.check(client, "m", "1")
    for refused in ("2", "9"):
        with pytest.raises(SystemExit, match="refusing"):
            promote_alias.check(client, "m", refused)


def test_apply_moves_the_alias_and_remembers_the_previous_champion():
    from pipelines.retrain import promote_alias

    client = FakeClient(
        {"1": {}, "4": {"promotion_decision": "recommended"}}, {"champion": "1", "challenger": "4"}
    )
    result = promote_alias.apply(client, "m", "4", now=1000)
    assert result == {"previous": "1", "version": "4", "changed": True}
    assert client.aliases == {"champion": "4"}  # challenger alias cleared
    assert client.tags["1"]["was_champion"] == "true"  # so it can be rolled back to
    assert (
        client.tags["4"]["previous_champion"] == "1" and client.tags["4"]["promoted_at"] == "1000"
    )


def test_apply_is_a_no_op_when_the_version_already_serves():
    from pipelines.retrain import promote_alias

    client = FakeClient({"4": {}}, {"champion": "4"})
    assert promote_alias.apply(client, "m", "4")["changed"] is False


def test_recorded_metrics_strip_the_test_prefix_and_carry_seed_and_size():
    from pipelines.retrain import decision

    run = type(
        "R",
        (),
        {
            "data": type(
                "D",
                (),
                {
                    "metrics": {"test_roc_auc": 0.79, "test_n": 4500.0, "val_x": 1.0},
                    "tags": {"seed": "42"},
                },
            )
        },
    )
    client = type("C", (), {"get_run": lambda self, rid: run})()
    metrics, seed, size = decision.recorded_metrics(client, "rid")
    assert metrics == {"roc_auc": 0.79, "n": 4500.0} and seed == "42" and size == 4500.0
