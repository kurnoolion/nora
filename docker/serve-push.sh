#!/usr/bin/env bash
# serve-push.sh — push a promoted serve label from the BUILD machine to the
# SERVING host (step 2 of 3 in the cross-host promote: promote.sh →
# serve-push.sh → serve-flip.sh).
#
#   build machine: serve/<label>/            serving host: serve/<label>/
#   ├── MANIFEST.json            ── rsync ──▶ (same shape, real files)
#   ├── nora/out/...
#   └── sira/<cells>/
#
# Guarantees:
#   * a remote label either exists COMPLETE or not at all — the transfer
#     lands in serve/.incoming/<label>/ and is renamed into place only
#     after a checksum verify pass (rsync -c) reports zero differences;
#   * labels are immutable on both ends — an existing remote label is
#     refused, never overwritten (roll back by flipping to an older label,
#     re-promote under a new label to ship a fix);
#   * interrupted pushes are resumable — re-run the same command and rsync
#     continues into the same .incoming/ staging dir.
#
# Runs as the invoking operator's own account over ssh (no shared push
# identity); files land owned by that account with the nora-ops group
# inherited from the setgid serve/ tree. The pusher must be a member of
# nora-ops on the serving host.
#
# Usage:
#   ./serve-push.sh <label> <host> [--serve-root <local serve/>]
#       [--remote-root <remote serve/>] [--dry-run]
#
#   <host>          ssh target, e.g. serve-host or user@serve-host
#   --serve-root    local serve/ (default: $NORA_SERVE_ROOT, else
#                   /srv/nora/serve)
#   --remote-root   serve/ on the serving host (default: same as local —
#                   both hosts use the /srv/nora layout)
#   --dry-run       print the remote commands + an rsync --dry-run summary
#
# Example:
#   ./serve-push.sh 2026-09-01-a serve-host
set -euo pipefail

LABEL="" HOST="" SERVE_ROOT="${NORA_SERVE_ROOT:-/srv/nora/serve}" REMOTE_ROOT="" DRY=0
while (( $# > 0 )); do
  case "$1" in
    --serve-root)  SERVE_ROOT="$2"; shift 2;;
    --remote-root) REMOTE_ROOT="$2"; shift 2;;
    --dry-run)     DRY=1; shift;;
    -h|--help)     sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*) echo "unknown option: $1" >&2; exit 2;;
    *) if [ -z "$LABEL" ]; then LABEL="$1"; elif [ -z "$HOST" ]; then HOST="$1";
       else echo "unexpected arg: $1" >&2; exit 2; fi; shift;;
  esac
done
[ -n "$LABEL" ] && [ -n "$HOST" ] || { echo "usage: $0 <label> <host> [--serve-root D] [--remote-root D] [--dry-run]" >&2; exit 2; }
REMOTE_ROOT="${REMOTE_ROOT:-$SERVE_ROOT}"

die() { echo "SPUSH-E$1: $2" >&2; echo "  fix: $3" >&2; exit 1; }

# ---- local preconditions ----------------------------------------------------
SRC="$SERVE_ROOT/$LABEL"
[ -d "$SRC" ] || die 001 "no local label $SRC" \
  "run promote.sh --serve-root $SERVE_ROOT --label $LABEL ... first"
[ -f "$SRC/MANIFEST.json" ] || die 002 "$SRC has no MANIFEST.json (not a promote.sh label)" \
  "promote under a fresh label; never hand-assemble a label dir"
grep -q "\"label\": *\"$LABEL\"" "$SRC/MANIFEST.json" || die 003 "MANIFEST label != directory name" \
  "the label dir was renamed after promote; re-promote under the intended label"
subdirs=""
for sub in nora sira; do [ -d "$SRC/$sub" ] && subdirs="$subdirs $sub"; done
[ -n "$subdirs" ] || die 004 "label has neither nora/ nor sira/" "re-promote with --nora-build and/or --sira-build"

# ---- remote preconditions ---------------------------------------------------
INCOMING="$REMOTE_ROOT/.incoming"
STAGE="$INCOMING/$LABEL"
DEST="$REMOTE_ROOT/$LABEL"
remote() { ssh -o BatchMode=yes "$HOST" "$@"; }

echo "label      : $LABEL  (subdirs:$subdirs)"
echo "from       : $(hostname -s):$SRC"
echo "to         : $HOST:$DEST"
echo "size       : $(du -sh "$SRC" | cut -f1)"

if (( DRY )); then
  echo "--- dry run: remote commands ---"
  echo "ssh $HOST test ! -e $DEST                     # refuse existing label"
  echo "ssh $HOST mkdir -p $STAGE"
  echo "rsync -rlptD --chmod=ug+rwX,Dg+s --delete $SRC/ $HOST:$STAGE/"
  echo "rsync -rlptDc --dry-run -i $SRC/ $HOST:$STAGE/  # must print nothing"
  echo "ssh $HOST mv $STAGE $DEST                      # atomic publish"
  echo "--- dry run: transfer summary ---"
  rsync -rlptD --dry-run --stats "$SRC/" "$HOST:$STAGE/" 2>/dev/null | grep -E 'Number of|Total transferred' || \
    echo "(remote unreachable or staging dir absent — summary skipped)"
  exit 0
fi

remote "test -d '$REMOTE_ROOT'" \
  || die 010 "remote serve root $HOST:$REMOTE_ROOT missing or ssh failed" \
     "check ssh access (BatchMode, keys) and the /srv/nora layout on the serving host"
remote "test -w '$REMOTE_ROOT'" \
  || die 011 "remote serve root not writable as $(whoami)@$HOST" \
     "join nora-ops on the serving host; serve/ must be group-writable + setgid"
if remote "test -e '$DEST'"; then
  die 012 "remote label already exists: $HOST:$DEST (labels are immutable)" \
      "flip to it as-is with serve-flip.sh, or re-promote under a new label"
fi
if remote "test -d '$STAGE'"; then
  echo "NOTE: resuming into existing staging dir $STAGE"
fi
remote "mkdir -p '$STAGE'"

# ---- transfer ---------------------------------------------------------------
# -rlptD = -a minus owner/group: ownership comes from the pushing account +
# setgid group inheritance on the serving host, never from the build
# machine's numeric ids. --chmod keeps the tree group-writable (GC by any
# operator) and re-applies setgid on dirs. Hardlinks on the source become
# plain files on the remote — that is the intended de-coupling from the
# build machine's builds/.
echo "--- transfer ---"
rsync -rlptD --chmod=ug+rwX,Dg+s --delete --info=progress2 "$SRC/" "$HOST:$STAGE/"

echo "--- verify (checksum) ---"
diff_lines="$(rsync -rlptDc --dry-run -i "$SRC/" "$HOST:$STAGE/" | grep -v '^\.d' || true)"
if [ -n "$diff_lines" ]; then
  echo "$diff_lines" | head -20 >&2
  die 020 "checksum verify found differences (see above) — label NOT published" \
      "re-run the same command (rsync resumes into $STAGE); if it persists, inspect the remote disk"
fi

# ---- publish ----------------------------------------------------------------
if ! remote "mv '$STAGE' '$DEST'"; then
  die 021 "atomic publish failed (staging left at $STAGE)" \
      "check that $DEST did not appear concurrently; re-run to retry the mv"
fi
remote "rmdir '$INCOMING' 2>/dev/null || true"

echo ""
echo "pushed -> $HOST:$DEST"
echo "next, on the serving host:"
echo "  cd <nora checkout>/docker && ./serve-flip.sh <stack> $LABEL"
