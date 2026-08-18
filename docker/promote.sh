#!/usr/bin/env bash
# promote.sh — snapshot the SERVE-set of a nora-build + sira-build into
# serve/<label>/ using hardlinks (docker-distro build/serve topology).
#
#   serve/<label>/
#   ├── MANIFEST.json          # which builds + git sha + timestamp
#   ├── nora/out/{vectorstore,graph,taxonomy,parse}/  # env_dir-SHAPED: mount as /data/env
#   └── sira/<MNO>__<MMMYYYY>/...                     # cells: mount as /data/db
#
# parse is part of the serve-set: the web app reads out/parse/ trees at
# SERVE time (Eval Studio ground-truth picker, req-browser) — a label
# without it serves those surfaces empty.
#
# Hardlinks (cp -al): instant, ~zero disk, real files (container-safe), and
# immune to later build wipes — rebuilding a build never mutates a promoted
# snapshot. Requires serve/ on the SAME filesystem as the builds. Promote only
# while no build is mid-write. Rollback = point the stack .env at a previous
# label. GC old labels with plain rm -rf.
#
# Usage:
#   ./promote.sh --serve-root <nora-data>/serve --label <label> \
#       [--nora-build <nora-builds/X>] [--sira-build <sira-builds/Y>] \
#       [--scheme <sira-prompt-scheme>]
# At least one of --nora-build / --sira-build is required.
# --scheme records the SIRA enrichment-prompt scheme (e.g. scheme-v1 /
#   scheme-v2) in the label MANIFEST so the serving healthz — and through
#   it every eval run's StackStamp — can attribute served data to the
#   variant hypothesis it came from.
# (--include-parse is accepted as a no-op — parse is in the default set.)
set -euo pipefail

SERVE_ROOT="" LABEL="" NORA_BUILD="" SIRA_BUILD="" SCHEME=""
while (( $# > 0 )); do
  case "$1" in
    --serve-root)    SERVE_ROOT="$2"; shift 2;;
    --label)         LABEL="$2"; shift 2;;
    --nora-build)    NORA_BUILD="$2"; shift 2;;
    --sira-build)    SIRA_BUILD="$2"; shift 2;;
    --scheme)        SCHEME="$2"; shift 2;;
    --include-parse) shift;;   # no-op: parse is in the default serve-set
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SERVE_ROOT" ] && [ -n "$LABEL" ] || { echo "need --serve-root and --label" >&2; exit 2; }
[ -n "$NORA_BUILD$SIRA_BUILD" ] || { echo "need --nora-build and/or --sira-build" >&2; exit 2; }

DEST="$SERVE_ROOT/$LABEL"
[ -e "$DEST" ] && { echo "refusing: $DEST already exists (labels are immutable; pick a new one)" >&2; exit 1; }
mkdir -p "$DEST"

promoted_nora="" promoted_sira=""
if [ -n "$NORA_BUILD" ]; then
  [ -d "$NORA_BUILD/out" ] || { echo "not a nora build (no out/): $NORA_BUILD" >&2; exit 1; }
  for d in vectorstore graph taxonomy parse; do
    if [ -d "$NORA_BUILD/out/$d" ]; then
      mkdir -p "$DEST/nora/out"
      cp -al "$NORA_BUILD/out/$d" "$DEST/nora/out/$d"
      echo "promoted nora out/$d"
    else
      echo "WARN: $NORA_BUILD/out/$d missing — skipped"
    fi
  done
  promoted_nora="$(cd "$NORA_BUILD" && pwd)"
fi
if [ -n "$SIRA_BUILD" ]; then
  found=0
  for cell in "$SIRA_BUILD"/*__*/; do
    [ -d "$cell" ] || continue
    mkdir -p "$DEST/sira"
    cp -al "$cell" "$DEST/sira/$(basename "$cell")"
    echo "promoted sira cell $(basename "$cell")"
    found=1
  done
  (( found )) || { echo "no <MNO>__<MMMYYYY> cells under $SIRA_BUILD" >&2; exit 1; }
  [ -f "$SIRA_BUILD/SOURCE.json" ] && cp -al "$SIRA_BUILD/SOURCE.json" "$DEST/sira/SOURCE.json"
  promoted_sira="$(cd "$SIRA_BUILD" && pwd)"
fi

sha="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
cat > "$DEST/MANIFEST.json" <<EOF
{
  "label": "$LABEL",
  "nora_build": "${promoted_nora}",
  "sira_build": "${promoted_sira}",
  "sira_prompt_scheme": "${SCHEME}",
  "repo_git_sha": "$sha",
  "promoted_at": "$(date -u +%FT%TZ)"
}
EOF
# Copy the manifest INSIDE each promoted subdir too: containers mount
# <label>/sira (or <label>/nora) as their data root, so the label-root
# copy is unreachable from inside the mount (field-found — the serving
# healthz could only see the label via env override). The subdir copy
# makes the manifest self-describing wherever the mount boundary falls.
for sub in nora sira; do
  [ -d "$DEST/$sub" ] && cp "$DEST/MANIFEST.json" "$DEST/$sub/MANIFEST.json"
done
echo ""
echo "promoted -> $DEST"
echo "point the stack .env at it and restart:"
[ -n "$promoted_nora" ] && echo "  NORA_ENV_DIR=$DEST/nora"
[ -n "$promoted_sira" ] && echo "  SIRA_DB_ROOT=$DEST/sira"
