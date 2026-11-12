#!/usr/bin/env bash
# promote.sh — snapshot the SERVE-set of a nora-build + sira-build into
# serve/<label>/ using hardlinks (docker-distro build/serve topology).
#
#   serve/<label>/
#   ├── MANIFEST.json          # which builds + git sha + timestamp
#   ├── nora/out/{vectorstore,graph,taxonomy,parse}/  # env_dir-SHAPED: mount as /data/env
#   └── sira/<MNO>/<MMMYYYY>/...                      # cells: mount as /data/db
#
# parse is part of the serve-set: the web app reads out/parse/ trees at
# SERVE time (Eval Studio ground-truth picker, req-browser) — a label
# without it serves those surfaces empty.
#
# Hardlinks (cp -al): instant, ~zero disk, real files (container-safe), and
# immune to later build wipes — a rebuild that writes new files never mutates
# a promoted snapshot. Hardlinks do NOT isolate in-place writes, so the NORA
# vectorstore is copied (cp -a): chroma updates chroma.sqlite3 on open, and
# through a link that write reached the build and every label sharing it. Requires serve/ on the SAME filesystem as the builds. Promote only
# while no build is mid-write. Rollback = point the stack .env at a previous
# label. GC old labels with plain rm -rf.
#
# Usage:
#   ./promote.sh --serve-root <nora-data>/serve --label <label> \
#       [--nora-build <nora-builds/X>] [--sira-build <sira-builds/Y>] \
#       [--scheme <sira-prompt-scheme>] [--stages-root <stage store>]
# At least one of --nora-build / --sira-build is required.
# --stages-root names the host's stage store (the wiring env's STAGES_ROOT;
#   cycle.sh passes it). parse and taxonomy cells come from the build's
#   out/ when it has them, else from the store entry the build's
#   STAGES.json names (pipeline/locate.py, stage-graph §10c): both are
#   hard-linked into the label, which keeps the out/ layout, so serving
#   reads it unchanged. The store must be on the serve root's filesystem.
# --scheme records the SIRA enrichment-prompt scheme (e.g. scheme-v1 /
#   scheme-v2) in the label MANIFEST so the serving healthz — and through
#   it every eval run's StackStamp — can attribute served data to the
#   variant hypothesis it came from. Promoting SIRA cells without it
#   warns (not fails — a label is valid without it, just less traceable).
# (--include-parse is accepted as a no-op — parse is in the default set.)
set -euo pipefail

SERVE_ROOT="" LABEL="" NORA_BUILD="" SIRA_BUILD="" SCHEME="" STAGES_ROOT=""
while (( $# > 0 )); do
  case "$1" in
    --serve-root)    SERVE_ROOT="$2"; shift 2;;
    --label)         LABEL="$2"; shift 2;;
    --nora-build)    NORA_BUILD="$2"; shift 2;;
    --sira-build)    SIRA_BUILD="$2"; shift 2;;
    --scheme)        SCHEME="$2"; shift 2;;
    --stages-root)   STAGES_ROOT="$2"; shift 2;;
    --include-parse) shift;;   # no-op: parse is in the default serve-set
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SERVE_ROOT" ] && [ -n "$LABEL" ] || { echo "need --serve-root and --label" >&2; exit 2; }
[ -n "$NORA_BUILD$SIRA_BUILD" ] || { echo "need --nora-build and/or --sira-build" >&2; exit 2; }
if [ -n "$SIRA_BUILD" ] && [ -z "$SCHEME" ]; then
  echo "WARN: --sira-build without --scheme — MANIFEST will record an empty" \
       "sira_prompt_scheme (labels are immutable; it cannot be filled in later)" >&2
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
locate_cli() { ( cd "$REPO" && python3 -m core.src.pipeline.locate "$NORA_BUILD" "$@" ${STAGES_ROOT:+--stages-root "$STAGES_ROOT"} ); }

DEST="$SERVE_ROOT/$LABEL"
[ -e "$DEST" ] && { echo "refusing: $DEST already exists (labels are immutable; pick a new one)" >&2; exit 1; }
# read the manifest before the label exists: a refusal must not leave a
# half-made label behind (labels are never reused)
if [ -n "$NORA_BUILD" ]; then
  for d in parse taxonomy; do
    locate_cli "$d" --cells >/dev/null \
      || { echo "cannot read $NORA_BUILD/STAGES.json — refusing (the label would miss stored cells)" >&2; exit 1; }
    gone="$(locate_cli "$d" --missing)" || {
      echo "refusing: $NORA_BUILD/STAGES.json names $d output that neither the build nor the stage store holds:" >&2
      echo "$gone" | sed 's/^/  /' >&2
      echo "(removed from the store while the build needed it? re-run the stage in this build${STAGES_ROOT:+, or check --stages-root})" >&2
      exit 1; }
  done
fi
mkdir -p "$DEST"

promoted_nora="" promoted_sira=""
if [ -n "$NORA_BUILD" ]; then
  [ -d "$NORA_BUILD/out" ] || [ -f "$NORA_BUILD/STAGES.json" ] \
    || { echo "not a nora build (no out/, no STAGES.json): $NORA_BUILD" >&2; exit 1; }
  for d in vectorstore graph taxonomy parse; do
    found=0
    if [ -d "$NORA_BUILD/out/$d" ]; then
      mkdir -p "$DEST/nora/out"
      if [ "$d" = vectorstore ]; then
        # Real copy: chroma writes its sqlite file in place on open, and a
        # hardlink would carry that write into the build and every label.
        cp -a "$NORA_BUILD/out/$d" "$DEST/nora/out/$d"
        echo "promoted nora out/$d (copied)"
      else
        cp -al "$NORA_BUILD/out/$d" "$DEST/nora/out/$d"
        echo "promoted nora out/$d"
      fi
      found=1
    fi
    # per-cell stages: cells only the stage store holds, by the build's manifest
    if [ "$d" = parse ] || [ "$d" = taxonomy ]; then
      cells="$(locate_cli "$d" --cells)" || { echo "cannot read $NORA_BUILD/STAGES.json — refusing (the label would miss stored cells)" >&2; exit 1; }
      for c in $cells; do
        [ -d "$NORA_BUILD/out/$d/$c" ] && continue
        src="$(locate_cli "$d" "$c")" || { echo "$d $c: listed but not found" >&2; exit 1; }
        mkdir -p "$DEST/nora/out/$d/${c%/*}"
        cp -al "$src" "$DEST/nora/out/$d/$c"
        echo "promoted nora $d $c (from the stage store)"
        found=1
      done
    fi
    (( found )) || echo "WARN: $NORA_BUILD/out/$d missing — skipped"
  done
  # the build's recipe and stage manifest travel with the label: publish
  # records them on the release set (stage graph, step 3)
  for f in RECIPE.json STAGES.json; do
    [ -f "$NORA_BUILD/$f" ] && { mkdir -p "$DEST/nora"; cp "$NORA_BUILD/$f" "$DEST/nora/$f"; echo "promoted nora $f"; }
  done
  promoted_nora="$(cd "$NORA_BUILD" && pwd)"
fi
if [ -n "$SIRA_BUILD" ]; then
  found=0
  # cells are <MNO>/<MMMYYYY>/ (since 2026-11-06); a build from before holds <MNO>__<MMMYYYY>/
  for cell in "$SIRA_BUILD"/*/*/ "$SIRA_BUILD"/*__*/; do
    [ -d "$cell" ] && [ -f "$cell/raw/metadata.json" ] || continue
    rel="${cell#"$SIRA_BUILD"/}"; rel="${rel%/}"
    mkdir -p "$DEST/sira/$(dirname "$rel")"
    cp -al "$cell" "$DEST/sira/$rel"
    echo "promoted sira cell $rel"
    found=1
  done
  (( found )) || { echo "no <MNO>/<MMMYYYY> cells under $SIRA_BUILD" >&2; exit 1; }
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
if [ -n "$promoted_nora" ]; then echo "  NORA_ENV_DIR=$DEST/nora"; fi
if [ -n "$promoted_sira" ]; then echo "  STORE_ROOT=$DEST/sira"; fi
