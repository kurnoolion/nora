#!/usr/bin/env bash
# prep-offline.sh — HOST-side prep for OFFLINE=1 docker builds.
#
# Why: on agent-guarded hosts (work PC), the endpoint-security agent resets
# ALL network egress from container processes (even --network=host), while
# host processes are allowed. So everything the image builds would fetch
# (PyPI, apt, rustup, github) is fetched/built HERE with host tools into
# docker/vendor/, and OFFLINE=1 builds consume it with zero in-container
# network.
#
# Run on the build host whenever dependencies change:
#   ./prep-offline.sh                  # full prep (wheels + sira clone + bm25x)
#   SKIP_BM25X=1 ./prep-offline.sh     # skip the rust build (supply the wheel
#                                      #   yourself, e.g. extracted from an
#                                      #   online-built bm25x-builder image)
#
# Requires on the host: python3.12 + pip, git; for bm25x: rust + maturin
# (the same toolchain SETUP.md §2/3 installs for bare-metal SIRA).
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(cd .. && pwd)"
VENDOR="$PWD/vendor"
WHEELS="$VENDOR/wheels"
SIRA_REPO="${SIRA_REPO:-https://github.com/facebookresearch/sira.git}"
SIRA_REF="${SIRA_REF:-62ec59cfb0d76f28ceb3c3d80023ac58a98e4b7a}"
PY="${PY:-python3}"

$PY - <<'EOF'
import sys
assert sys.version_info[:2] == (3, 12), \
    f"host python is {sys.version.split()[0]} — need 3.12 (wheel tags must match the python:3.12 images)"
EOF
mkdir -p "$WHEELS"

echo "== [1/4] wheels: nora set (torch + requirements.txt + docling) =="
# pip wheel: downloads wheels where published, builds from sdist where not.
# torchvision rides with torch (same index/resolve — mismatched pairs fail
# to load); opencv-python-headless replaces docling's full opencv in-image.
$PY -m pip wheel -w "$WHEELS" torch torchvision
$PY -m pip wheel -w "$WHEELS" -r "$REPO_ROOT/requirements.txt" docling
$PY -m pip wheel -w "$WHEELS" opencv-python-headless

echo "== [2/4] wheels: sira trimmed set (SETUP.md §2a) =="
$PY -m pip wheel -w "$WHEELS" \
    aiohttp hydra-core omegaconf 'polars[rtcompat]' \
    huggingface_hub fastapi uvicorn httpx pydantic \
    pytrec_eval numpy requests
$PY -m pip wheel -w "$WHEELS" --no-deps beir

echo "== [3/4] sira clone @ ${SIRA_REF:0:9} + configs/patches (host git) =="
rm -rf "$VENDOR/sira"
git clone "$SIRA_REPO" "$VENDOR/sira"
git -C "$VENDOR/sira" fetch origin "$SIRA_REF"
git -C "$VENDOR/sira" checkout -q FETCH_HEAD
if [ "${SKIP_BM25X:-0}" != "1" ]; then
    command -v maturin >/dev/null || { echo "ERROR: maturin not found (pip install maturin; needs rust — SETUP.md §3)" >&2; exit 1; }
    ( cd "$VENDOR/sira/src/sira/bm25x/python" && maturin build --release -o "$WHEELS" )
else
    ls "$WHEELS"/bm25x-*.whl >/dev/null 2>&1 || echo "WARN: SKIP_BM25X=1 and no bm25x wheel in vendor/wheels — offline sira builds will fail until one is supplied."
fi
# Apply NORA's configs + prompts + patches to the vendored clone (host git),
# so the container never needs git or install_configs.
SIRA_CLONE="$VENDOR/sira" bash "$REPO_ROOT/sandbox/install_configs.sh"
rm -rf "$VENDOR/sira/.git"   # slim: patches applied; provenance in PREP.json

echo "== [4/4] manifest =="
$PY - "$VENDOR" "$SIRA_REF" <<'EOF'
import json, pathlib, subprocess, sys, datetime
vendor, ref = pathlib.Path(sys.argv[1]), sys.argv[2]
(vendor / "PREP.json").write_text(json.dumps({
    "sira_ref": ref,
    "host_python": sys.version.split()[0],
    "wheels": len(list((vendor / "wheels").glob("*.whl"))),
    "prepared_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, indent=2) + "\n")
EOF
echo ""
echo "done — $(ls "$WHEELS"/*.whl | wc -l) wheels + patched sira clone under docker/vendor/"
echo "build fully offline with:  OFFLINE=1 in .env  →  docker compose ... build"
