"""Build a portable OCI image containing the evaluated model and a static init-container helper."""

import hashlib
import io
import json
import os
import platform
import tarfile
from pathlib import Path

OCI = "application/vnd.oci.image."


def add(tar, name, data, mode=0o644):
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def encoded(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def build_image(output, files, run_id, installer=None):
    installer = installer or Path(
        os.environ.get("PHASE5_MODEL_INSTALLER", "/usr/local/bin/install-credit-model")
    )
    arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as tar:
        add(tar, "install-model", installer.read_bytes(), 0o755)
        for name, path in sorted(files.items()):
            add(tar, "model/" + name, path.read_bytes())

    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w") as archive:

        def blob(data, kind):
            digest = hashlib.sha256(data).hexdigest()
            add(archive, "blobs/sha256/" + digest, data)
            return {"mediaType": OCI + kind, "digest": "sha256:" + digest, "size": len(data)}

        layer_ref = blob(layer.getvalue(), "layer.v1.tar")
        config = blob(
            encoded(
                {
                    "architecture": arch,
                    "os": "linux",
                    "config": {
                        "Entrypoint": ["/install-model"],
                        "Cmd": ["/output"],
                        "User": "65532:65532",
                        "Labels": {"mlflow.run_id": run_id},
                    },
                    "rootfs": {"type": "layers", "diff_ids": [layer_ref["digest"]]},
                }
            ),
            "config.v1+json",
        )
        manifest = blob(
            encoded(
                {
                    "schemaVersion": 2,
                    "mediaType": OCI + "manifest.v1+json",
                    "config": config,
                    "layers": [layer_ref],
                }
            ),
            "manifest.v1+json",
        )
        manifest["annotations"] = {"org.opencontainers.image.ref.name": "credit-model:" + run_id}
        add(archive, "oci-layout", encoded({"imageLayoutVersion": "1.0.0"}))
        add(archive, "index.json", encoded({"schemaVersion": 2, "manifests": [manifest]}))
    return manifest["digest"]
