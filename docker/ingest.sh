#!/usr/bin/env bash
# ingest.sh — quoting-proof launcher for containerized pipeline lane runs.
#
# Wraps the `docker compose run nora-pipeline` invocation so the inner
# `sh -c` string is authored HERE once (no nested-quoting on the command
# line). Runs detached by default; console output is redirected inside the
# container to <env>/reports/ on the /data/env mount, so the full log lands
# on the host regardless of --rm / terminal lifetime.
#
# Usage:
#   ./ingest.sh [options] <MNO> <MMMYYYY> [-- <extra run_cli args>]
#
# Options:
#   -e <file>   wiring env file (default: .env.builds) — its NORA_ENV_DIR
#               must point at a BUILD dir, never a promoted serve label
#   -l <lane>   pipeline lane: ingestion | nora (default: ingestion)
#   -f          pass --force (redo work the skip logic would reuse)
#   --fg        run attached in the foreground, output to the terminal
#               (no log redirect) — for quick diagnosis
#   DRY_RUN=1   print the docker command instead of running it
#
# Examples:
#   ./ingest.sh GP Feb2026
#   ./ingest.sh -f GP Feb2026 -- --skip-standards
#   ./ingest.sh -l nora GP Feb2026
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

ENVFILE="$HERE/.env.builds" LANE="ingestion" FORCE="" FG=0
while (( $# > 0 )); do
  case "$1" in
    -e) ENVFILE="$2"; shift 2;;
    -l) LANE="$2"; shift 2;;
    -f) FORCE="--force"; shift;;
    --fg) FG=1; shift;;
    --) shift; break;;
    -*) echo "unknown option: $1" >&2; exit 2;;
    *) if [ -z "${MNO:-}" ]; then MNO="$1"; elif [ -z "${REL:-}" ]; then REL="$1";
       else echo "unexpected arg: $1 (extra run_cli args go after --)" >&2; exit 2; fi; shift;;
  esac
done
[ -n "${MNO:-}" ] && [ -n "${REL:-}" ] || { grep -m1 -A2 '^# Usage' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ -f "$ENVFILE" ] || { echo "wiring env not found: $ENVFILE" >&2; exit 2; }
echo "$REL" | grep -qE '^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[0-9]{4}$' \
  || echo "WARN: release '$REL' is not MMMYYYY (e.g. Feb2026) — the pipeline will fail loud" >&2

# Extra args are joined verbatim into the in-container script — keep them
# simple tokens (flags/paths without spaces or quotes).
EXTRA="$*"

CLI="python -m core.src.pipeline.run_cli --env-dir /data/env --lane $LANE --mno $MNO --release $REL $FORCE $EXTRA"
LOG="/data/env/reports/lane-${LANE}-${MNO}__${REL}-$(date +%Y%m%d_%H%M%S).log"

if (( FG )); then
    INNER="mkdir -p /data/env/reports && exec $CLI"
    RUNFLAGS=(--rm -T)
else
    INNER="mkdir -p /data/env/reports && exec $CLI > $LOG 2>&1"
    RUNFLAGS=(-d --rm -T)
fi

CMD=(docker compose --env-file "$ENVFILE" --profile ingest
     run "${RUNFLAGS[@]}" nora-pipeline sh -c "$INNER")
if [ "${DRY_RUN:-0}" = "1" ]; then printf 'DRY:'; printf ' %q' "${CMD[@]}"; echo; exit 0; fi
"${CMD[@]}"

if ! (( FG )); then
    # host-side log path for tail -f (best effort: read NORA_ENV_DIR from the env file)
    HOST_ENV="$(grep -E '^NORA_ENV_DIR=' "$ENVFILE" | tail -1 | cut -d= -f2-)"
    echo "detached. follow with:"
    echo "  tail -f ${HOST_ENV:-<NORA_ENV_DIR>}${LOG#/data/env}"
    echo "  docker ps   # the run container disappears (--rm) when the lane finishes"
fi
