#!/usr/bin/env bash
# Reproducibility demo: restore the data that matched an older commit, prove its hash
# matches what was recorded, then return to the current commit.
# Usage: scripts/repro_demo.sh <old-git-sha-or-tag>
set -euo pipefail

OLD="${1:?usage: $0 <old-git-sha-or-tag>}"
START="$(git rev-parse --abbrev-ref HEAD)"
trap 'git checkout -q "$START"' EXIT
DATA=data/raw/credit_default.csv

recorded_md5() {  # md5 recorded in dvc.lock for the raw data output at the current commit
  python - <<'PY'
import yaml
lock = yaml.safe_load(open("dvc.lock"))
for out in lock["stages"]["ingest"]["outs"]:
    if out["path"].endswith("credit_default.csv"):
        print(out["md5"])
PY
}
actual_md5() { python -c "import hashlib,sys;print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest())" "$DATA"; }

echo "== current ($START)"; dvc checkout -q; echo "recorded: $(recorded_md5)  actual: $(actual_md5)"
echo "== checking out $OLD"; git checkout -q "$OLD"; dvc checkout -q
REC="$(recorded_md5)"; ACT="$(actual_md5)"
echo "recorded: $REC  actual: $ACT"
[ "$REC" = "$ACT" ] && echo "OK: data at $OLD restored byte-for-byte" || { echo "MISMATCH"; exit 1; }
echo "== back to $START"; git checkout -q "$START"; dvc checkout -q
echo "recorded: $(recorded_md5)  actual: $(actual_md5)"
