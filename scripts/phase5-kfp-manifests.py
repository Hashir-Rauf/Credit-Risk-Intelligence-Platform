"""Render a matching KFP release locally, including its pinned Argo dependency."""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

SOURCES = [
    (
        "kubeflow/pipelines",
        "2.18.2",
        "008ceace3043975073366fc483865540772e045c1401d5de9a580fbc6e78d295",
    ),
    (
        "argoproj/argo-workflows",
        "v4.1.6",
        "e793bc7ac65f63fc90af373d75a91fd560a3183b4e0a81c581ab952e8d4b2645",
    ),
]


def fetch_manifests(folder, repository, tag, digest):
    archive = folder / f"{repository.split('/')[1]}.tar.gz"
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            raise RuntimeError("Install curl to download the release archives")
        subprocess.run(  # noqa: S603
            [
                curl,
                "-fL",
                "--retry",
                "3",
                "--max-time",
                "600",
                "-o",
                str(archive),
                f"https://codeload.github.com/{repository}/tar.gz/refs/tags/{tag}",
            ],
            check=True,
        )
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise ValueError(f"Release checksum differs for {repository} {tag}")
    prefix = f"{repository.split('/')[1]}-{tag.removeprefix('v')}"
    with tarfile.open(archive) as tar:
        for entry in tar:
            parts = PurePosixPath(entry.name).parts
            if parts[:2] != (prefix, "manifests") or not entry.isfile():
                continue
            if ".." in parts:
                raise ValueError("Invalid release path")
            dest = folder.joinpath(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(entry) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
    return folder / prefix / "manifests"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--kubectl", default="kubectl")
    args = parser.parse_args()
    folder = args.directory.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    kfp, argo = [fetch_manifests(folder, *source) for source in SOURCES]
    root = kfp / "kustomize"
    ref = re.compile(
        r"https://github\.com/argoproj/argo-workflows/manifests/([^?\s]+)\?ref=v4\.1\.6(?:&timeout=\w+)?"
    )
    for file in (root / "third-party/argo").rglob("kustomization.yaml"):

        def local(match, parent=file.parent):
            target = argo / match[1]
            if not target.is_dir():
                raise FileNotFoundError(target)
            return Path(os.path.relpath(target, parent)).as_posix()

        file.write_text(ref.sub(local, file.read_text(encoding="utf-8")), encoding="utf-8")
    for name, overlay in [
        ("cluster", "cluster-scoped-resources"),
        ("standalone", "env/platform-agnostic"),
    ]:
        subprocess.run(  # noqa: S603
            [args.kubectl, "kustomize", str(root / overlay), "-o", str(folder / f"{name}.yaml")],
            check=True,
        )
    print("Rendered cluster.yaml and standalone.yaml in", folder)


if __name__ == "__main__":
    main()
