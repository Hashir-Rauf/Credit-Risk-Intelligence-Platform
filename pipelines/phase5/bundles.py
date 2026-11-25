"""Transfer project-relative files between isolated pods (Feast uses the same /app in each)."""

import shutil
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from src.common.config import repo_root


def pack(output, paths):
    root = repo_root()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=1) as archive:
        for path in paths:
            files = sorted(path.rglob("*")) if path.is_dir() else [path]
            for file in files:
                if not file.is_dir():
                    archive.write(file, file.relative_to(root).as_posix())


def unpack(bundle):
    root = repo_root()
    with ZipFile(bundle) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if not path.parts or path.parts[0] not in {"data", "models", "reports"}:
                raise ValueError("Unexpected bundle root")
            if ".." in path.parts or "\\" in entry.filename:
                raise ValueError("Invalid bundle path")
            target = root / Path(*path.parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError("Bundle path escapes workspace")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)
