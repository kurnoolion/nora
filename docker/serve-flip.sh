#!/usr/bin/env bash
# serve-flip.sh — point a serve stack at a promoted label and recreate it
# (step 3 of 3 in the cross-host promote: promote.sh → serve-push.sh →
# serve-flip.sh). Runs ON THE SERVING HOST. Rollback is the same command
# with the previous label — there is no separate rollback path.
#
# What it does, in order:
#   1. resolve the stack env (.env.<stack>) and the target label
#      (serve/<label>/MANIFEST.json), show new-vs-current MANIFEST facts
#   2. ask for an explicit "yes" (the promote go/no-go lives here)
#   3. rewrite NORA_ENV_DIR / SIRA_DB_ROOT in the stack env (a .prev copy
#      is kept beside it) — only the pointers the label carries data for
#   4. RECREATE the stack (`compose up -d`; a plain `restart` does not
#      re-read env files)
#   5. verify the query service's /healthz reports serve_label == <label>,
#      print the identity line (label / data_fingerprint / code_version /
#      sira_prompt_scheme / cells)
#   6. append one line to serve/PROMOTE_LOG and print the rollback
#      one-liner + the golden-baseline reminder
#
# Usage:
#   ./serve-flip.sh <stack> <label> [--mode staged|expedited|override]
#       [--eval-run <id>] [--note "<text>"] [--yes] [--dry-run]
#       [--serve-root <serve/>] [--env-root <dir holding .env.<stack>>]
#
#   --mode        promote protocol this flip belongs to (recorded, never
#                 enforced): staged (default) = secondary stack first, golden
#                 eval vs baseline, then production; expedited = direct flip;
#                 override = eval regressed, promoting anyway (--note why)
#   --eval-run    golden run id that justified the flip (staged/override)
#   --yes         skip the confirmation prompt (scripted rollback)
#   --dry-run     show everything, change nothing
#   --serve-root  default $NORA_SERVE_ROOT, else /srv/nora/serve
#   --env-root    default $NORA_ENV_ROOT, else /srv/nora/env; falls back to
#                 this docker/ dir when the stack env only exists there
#                 (pre-migration hosts)
#
# Examples:
#   ./serve-flip.sh nora2 2026-09-01-a                    # staged: secondary first
#   ./serve-flip.sh nora1 2026-09-01-a --eval-run 42      # ...then production
#   ./serve-flip.sh nora1 2026-08-15-b --mode expedited --yes   # rollback
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

STACK="" LABEL="" MODE="staged" EVAL_RUN="" NOTE="" YES=0 DRY=0
SERVE_ROOT="${NORA_SERVE_ROOT:-/srv/nora/serve}"
ENV_ROOT="${NORA_ENV_ROOT:-/srv/nora/env}"
while (( $# > 0 )); do
  case "$1" in
    --mode)       MODE="$2"; shift 2;;
    --eval-run)   EVAL_RUN="$2"; shift 2;;
    --note)       NOTE="$2"; shift 2;;
    --yes)        YES=1; shift;;
    --dry-run)    DRY=1; shift;;
    --serve-root) SERVE_ROOT="$2"; shift 2;;
    --env-root)   ENV_ROOT="$2"; shift 2;;
    -h|--help)    sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*) echo "unknown option: $1" >&2; exit 2;;
    *) if [ -z "$STACK" ]; then STACK="$1"; elif [ -z "$LABEL" ]; then LABEL="$1";
       else echo "unexpected arg: $1" >&2; exit 2; fi; shift;;
  esac
done
[ -n "$STACK" ] && [ -n "$LABEL" ] || { echo "usage: $0 <stack> <label> [options]  (-h for all)" >&2; exit 2; }
case "$MODE" in staged|expedited|override) ;; *) echo "bad --mode: $MODE (staged|expedited|override)" >&2; exit 2;; esac
if [ "$MODE" = override ] && [ -z "$NOTE" ]; then
  echo "WARN: --mode override without --note — record why the regression is acceptable" >&2
fi

die() { echo "SFLIP-E$1: $2" >&2; echo "  fix: $3" >&2; exit 1; }
# read KEY=value from an env file (last assignment wins; no interpolation)
envget() { grep -E "^$2=" "$1" | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//; s/^"//; s/"$//'; }

# ---- 1. resolve stack env + label ------------------------------------------
ENVFILE="$ENV_ROOT/.env.$STACK"
if [ ! -f "$ENVFILE" ] && [ -f "$HERE/.env.$STACK" ]; then
  ENVFILE="$HERE/.env.$STACK"; echo "NOTE: using pre-migration stack env $ENVFILE"
fi
[ -f "$ENVFILE" ] || die 001 "no stack env for '$STACK' ($ENV_ROOT/.env.$STACK)" \
  "stack names are the .env.<stack> files under $ENV_ROOT; ls that dir"

NEW="$SERVE_ROOT/$LABEL"
[ -f "$NEW/MANIFEST.json" ] || die 002 "no label at $NEW (or no MANIFEST.json)" \
  "push it first from the build machine: serve-push.sh $LABEL <this host>"
[ ! -d "$SERVE_ROOT/.incoming/$LABEL" ] || die 003 "label is still in .incoming/ (push unfinished)" \
  "re-run serve-push.sh on the build machine until it reports 'pushed'"

cur_nora="$(envget "$ENVFILE" NORA_ENV_DIR)"
cur_sira="$(envget "$ENVFILE" SIRA_DB_ROOT)"
[ -n "$cur_nora" ] && [ -n "$cur_sira" ] || die 004 "stack env lacks NORA_ENV_DIR and/or SIRA_DB_ROOT" \
  "the stack env must carry both keys (see env.example); add them, then re-run"
# current label = the serve/<label>/ ancestor of the current pointers
label_of() { case "$1" in "$SERVE_ROOT"/*) basename "$(dirname "$1")";; *) echo "(not a serve label: $1)";; esac; }
CUR_LABEL="$(label_of "$cur_sira")"
[ "$(label_of "$cur_nora")" = "$CUR_LABEL" ] || CUR_LABEL="$(label_of "$cur_nora") / $(label_of "$cur_sira")"

new_nora="" new_sira=""
[ -d "$NEW/nora" ] && new_nora="$NEW/nora"
[ -d "$NEW/sira" ] && new_sira="$NEW/sira"
[ -n "$new_nora$new_sira" ] || die 005 "label has neither nora/ nor sira/" "re-promote and re-push"

mfield() { grep -o "\"$2\": *\"[^\"]*\"" "$1" 2>/dev/null | head -1 | cut -d'"' -f4; }
show_manifest() {  # $1 = label dir, $2 = heading
  local m="$1/MANIFEST.json"
  if [ -f "$m" ]; then
    printf '%-9s label=%s scheme=%s sha=%s promoted=%s\n' "$2" "$(mfield "$m" label)" \
      "$(mfield "$m" sira_prompt_scheme)" "$(mfield "$m" repo_git_sha)" "$(mfield "$m" promoted_at)"
    printf '%-9s nora_build=%s\n%-9s sira_build=%s\n' "" "$(basename "$(mfield "$m" nora_build)")" \
      "" "$(basename "$(mfield "$m" sira_build)")"
  else
    printf '%-9s %s (no MANIFEST)\n' "$2" "$CUR_LABEL"
  fi
}

echo "stack      : $STACK  ($ENVFILE)"
echo "mode       : $MODE${EVAL_RUN:+  eval-run=$EVAL_RUN}${NOTE:+  note=\"$NOTE\"}"
show_manifest "$(dirname "$cur_sira")" "current:"
show_manifest "$NEW" "new:"
[ -n "$new_nora" ] && echo "  NORA_ENV_DIR : $cur_nora -> $new_nora" \
                   || echo "  NORA_ENV_DIR : $cur_nora (unchanged — label carries no nora/)"
[ -n "$new_sira" ] && echo "  SIRA_DB_ROOT : $cur_sira -> $new_sira" \
                   || echo "  SIRA_DB_ROOT : $cur_sira (unchanged — label carries no sira/)"
if [ "$CUR_LABEL" = "$LABEL" ]; then
  echo "NOTE: stack already points at $LABEL — this flip only recreates the stack"
fi

# ---- 2. go / no-go ----------------------------------------------------------
if (( DRY )); then echo "--- dry run: no changes made ---"; exit 0; fi
if (( ! YES )); then
  read -r -p "flip $STACK -> $LABEL? type yes to proceed: " ans
  [ "$ans" = yes ] || { echo "aborted (no changes)"; exit 1; }
fi

# ---- 3. rewrite pointers ------------------------------------------------------
cp -p "$ENVFILE" "$ENVFILE.prev"
[ -n "$new_nora" ] && sed -i "s|^NORA_ENV_DIR=.*|NORA_ENV_DIR=$new_nora|" "$ENVFILE"
[ -n "$new_sira" ] && sed -i "s|^SIRA_DB_ROOT=.*|SIRA_DB_ROOT=$new_sira|" "$ENVFILE"
echo "env rewritten ($ENVFILE.prev kept)"

# ---- 4. recreate ------------------------------------------------------------
# compose resolves relative env_file paths against the compose-file dir, so
# stack envs living outside docker/ must name WEB_ENV_FILE / SIRA_QUERY_ENV_FILE
# (and SECRETS_ENV_FILE) with absolute paths.
( cd "$HERE" && docker compose --env-file "$ENVFILE" --profile serve up -d ) \
  || die 010 "compose up failed — stack env already rewritten" \
     "inspect 'docker compose --env-file $ENVFILE ps'; roll back with: $0 $STACK $CUR_LABEL --mode expedited --yes"

# ---- 5. verify identity -----------------------------------------------------
PORT="$(envget "$ENVFILE" SIRA_QUERY_PORT)"; PORT="${PORT:-8040}"
HZ="http://127.0.0.1:$PORT/healthz"
body="" got=""
for _ in $(seq 1 30); do
  body="$(curl -sf --max-time 3 "$HZ" 2>/dev/null || true)"
  got="$(printf '%s' "$body" | grep -o '"serve_label": *"[^"]*"' | head -1 | cut -d'"' -f4)"
  [ "$got" = "$LABEL" ] && break
  sleep 2
done
hz() { printf '%s' "$body" | grep -o "\"$1\": *\"[^\"]*\"" | head -1 | cut -d'"' -f4; }
FP="$(hz data_fingerprint)"
CELLS="$(printf '%s' "$body" | grep -o '"data_fingerprint_cells": *{[^}]*}' | grep -o '":' | wc -l | tr -d ' ')"
echo "healthz    : label=${got:-?} fp=${FP:0:12} code=$(hz code_version) scheme=$(hz sira_prompt_scheme) cells=${CELLS:-0}"
if [ "$got" != "$LABEL" ]; then
  die 020 "healthz on $HZ does not report serve_label=$LABEL (got '${got:-<none>}')" \
      "docker compose --env-file $ENVFILE logs sira-query; roll back: $0 $STACK $CUR_LABEL --mode expedited --yes"
fi
[ -n "$FP" ] || echo "WARN: no data_fingerprint on healthz — eval StackStamps will fall back to the label name"

# ---- 6. record + hand-off ---------------------------------------------------
LOG="$SERVE_ROOT/PROMOTE_LOG"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$STACK" "$LABEL" "$CUR_LABEL" \
  "$(whoami)" "$MODE" "${EVAL_RUN:--}" "${FP:0:12}" "${NOTE:--}" >> "$LOG"
echo "logged     : $LOG"
echo ""
echo "flipped $STACK -> $LABEL"
echo "rollback   : $0 $STACK $CUR_LABEL --mode expedited --yes"
GOLDEN="$(envget "$ENVFILE" GOLDEN_DIR)"
echo "baseline   : after the next accepted golden run on $STACK, refresh" \
     "${GOLDEN:-\$GOLDEN_DIR}/baselines/$STACK.txt with its GEV block"
