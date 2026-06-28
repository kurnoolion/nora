#!/usr/bin/env bash
# run_stack.sh — launch ONE isolated SIRA-service + NORA-web stack (Path-B),
# wired from args, so you can run several in parallel to A/B different LLMs /
# ingestions. Each stack gets its own ports, its own LLM, and its own web state
# DBs (so Q&A logs / feedback don't conflate across stacks).
#
# Usage:
#   sandbox/run_stack.sh [OPTIONS] \
#       <label> <db_root> <svc_port> <web_port> <llm_base_url> <llm_model> [api_key]
#   sandbox/run_stack.sh [--state-dir DIR] --stop <label>
#
#   <label>        short id (e.g. qwen, prop) — names the state dir + log prefix
#   <db_root>      the ingestion dir (NORA_SIRA_DB_ROOT) for this stack
#   <svc_port>     SIRA service port (e.g. 8040)
#   <web_port>     NORA web port (e.g. 8080)
#   <llm_base_url> LLM endpoint base WITHOUT /v1 (e.g. http://dgx:8000);
#                  used as-is for the SIRA query-enrich shim and with /v1
#                  appended for the NORA synthesis LLM
#   <llm_model>    model name for both query-enrich and synthesis
#   [api_key]      optional bearer key for the LLM endpoint
#
# OPTIONS (any order, before OR after the positional args):
#   --state-dir DIR  base dir for per-stack state (pids + DBs); the stack lands
#                  under <DIR>/<label>/. Overrides $NORA_STACK_STATE_DIR.
#                  Default: /tmp/nora-stacks. (Pass the same --state-dir to
#                  --stop so it finds the pids.)
#   --log-dir DIR  where to write logs (default: the stack's state dir).
#                  Log files are  <DIR>/service-<svc_port>-<ts>.log  and
#                  <DIR>/web-<web_port>-<ts>.log  where ts = YYYYMMDD-HHMMSS.
#                  Each log starts with a header dumping the script args + all
#                  set NORA_*/SIRA_* env vars (API keys redacted).
#   --feedback-db PATH   web feedback DB    (default: <state>/feedback.db)
#   --config-db   PATH   web /config DB     (default: <state>/config.db)
#   --jobs-db     PATH   web jobs DB        (default: <state>/jobs.db)
#   --metrics-db  PATH   web metrics DB     (default: <state>/metrics.db)
#                  Each overrides its $NORA_*_DB env var. Point --feedback-db at
#                  one shared path across both stacks to POOL A/B feedback (rows
#                  carry llm_model). Precedence: flag > env var > default.
#   --dry-run      print the env + commands without launching.
#
# Env overrides (optional):
#   NORA_SIRA_DOC_ENRICH_RUN   pinned doc-enrich run name      (default: enrich-stable)
#   NORA_LLM_PROVIDER          synthesis provider tag          (default: openai)
#   NORA_STACK_STATE_DIR       base dir for per-stack state     (default: /tmp/nora-stacks)
#                              (--state-dir flag overrides this)
#   NORA_STACK_LOG_DIR         default log dir (overridden by --log-dir)
#   NORA_FEEDBACK_DB / NORA_CONFIG_DB / NORA_JOBS_DB / NORA_METRICS_DB
#                              per-DB path override (default: <state>/<name>.db).
#                              Point NORA_FEEDBACK_DB at the SAME path for both
#                              stacks to POOL A/B feedback into one DB — each row
#                              records `llm_model`, so it stays attributable
#                              (compare with GROUP BY llm_model). Config DB is
#                              best left per-stack (independent /config page).
#
# Notes:
#   * Run from your SIRA venv (python + uvicorn on PATH).
#   * Config is read at IMPORT time, so each process is pinned to its launch
#     env — that's why parallel stacks need separate processes, which this does.
#   * Path-B requires rerank OFF on the service; this sets NORA_SIRA_RERANK_ENABLED=false.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Print the leading comment block (everything after the shebang up to the first
# non-comment line) as help text.
usage() { awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit "${1:-0}"; }

# ── options (any order, before the positional args) ──────────────────
DRY=0; STOP=0; STOP_LABEL=""
STATE_DIR_OPT=""; LOG_DIR_OPT=""
JOBS_DB_OPT=""; METRICS_DB_OPT=""; FEEDBACK_DB_OPT=""; CONFIG_DB_OPT=""
POS=()
# Options may appear ANYWHERE — before or after the positional args. An
# unrecognized --flag is a hard error (a typo like --logs-dir won't be silently
# ignored). Positionals are collected and restored after the loop.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --stop)        STOP=1; STOP_LABEL="${2:?--stop needs a <label>}"; shift 2;;
        --state-dir)   STATE_DIR_OPT="${2:?--state-dir needs a path}"; shift 2;;
        --log-dir)     LOG_DIR_OPT="${2:?--log-dir needs a path}"; shift 2;;
        --jobs-db)     JOBS_DB_OPT="${2:?--jobs-db needs a path}"; shift 2;;
        --metrics-db)  METRICS_DB_OPT="${2:?--metrics-db needs a path}"; shift 2;;
        --feedback-db) FEEDBACK_DB_OPT="${2:?--feedback-db needs a path}"; shift 2;;
        --config-db)   CONFIG_DB_OPT="${2:?--config-db needs a path}"; shift 2;;
        --dry-run)     DRY=1; shift;;
        --help|-h)     usage 0;;
        --*) echo "unknown option: $1 (did you mean --log-dir / --state-dir ?)" >&2; usage 2;;
        *) POS+=("$1"); shift;;
    esac
done
if [[ ${#POS[@]} -gt 0 ]]; then set -- "${POS[@]}"; else set --; fi

# state base: --state-dir > $NORA_STACK_STATE_DIR > /tmp/nora-stacks
STATE_BASE="${STATE_DIR_OPT:-${NORA_STACK_STATE_DIR:-/tmp/nora-stacks}}"

# ── stop mode ────────────────────────────────────────────────────────
if [[ $STOP -eq 1 ]]; then
    sdir="$STATE_BASE/$STOP_LABEL"
    [[ -d "$sdir" ]] || { echo "no stack state at $sdir" >&2; exit 1; }
    for role in service web; do
        pf="$sdir/$role.pid"
        if [[ -f "$pf" ]] && kill -0 "$(cat "$pf")" 2>/dev/null; then
            kill "$(cat "$pf")" && echo "stopped $role (pid $(cat "$pf"))"
        else
            echo "$role not running"
        fi
        rm -f "$pf"
    done
    exit 0
fi

[[ $# -ge 6 ]] || { echo "error: expected 6 args (got $#)" >&2; usage 2; }

label="$1"; db_root="$2"; svc_port="$3"; web_port="$4"; llm_base="$5"; llm_model="$6"
api_key="${7:-}"
enrich_run="${NORA_SIRA_DOC_ENRICH_RUN:-enrich-stable}"
provider="${NORA_LLM_PROVIDER:-openai}"
llm_base="${llm_base%/}"                       # strip trailing slash
sdir="$STATE_BASE/$label"
log_dir="${LOG_DIR_OPT:-${NORA_STACK_LOG_DIR:-$sdir}}"
ts="$(date +%Y%m%d-%H%M%S)"
svc_log="$log_dir/service-${svc_port}-${ts}.log"
web_log="$log_dir/web-${web_port}-${ts}.log"

# Web state DBs — per-stack by default, but each is overridable via its env var
# so you can SHARE one across stacks (e.g. one feedback DB for A/B comparison —
# the rows carry `llm_model`, so a shared feedback DB stays attributable).
# precedence: CLI flag > env var > <state>/<name>.db
jobs_db="${JOBS_DB_OPT:-${NORA_JOBS_DB:-$sdir/jobs.db}}"
metrics_db="${METRICS_DB_OPT:-${NORA_METRICS_DB:-$sdir/metrics.db}}"
feedback_db="${FEEDBACK_DB_OPT:-${NORA_FEEDBACK_DB:-$sdir/feedback.db}}"
config_db="${CONFIG_DB_OPT:-${NORA_CONFIG_DB:-$sdir/config.db}}"

if [[ $DRY -eq 0 ]]; then
    [[ -d "$db_root" ]] || { echo "error: db_root not found: $db_root" >&2; exit 1; }
    mkdir -p "$sdir" "$log_dir"
fi

echo "stack '$label':"
echo "  SIRA service  → http://127.0.0.1:$svc_port   (db_root=$db_root, enrich_run=$enrich_run, rerank=off)"
echo "  NORA web      → http://127.0.0.1:$web_port    (synth LLM=$llm_model @ $llm_base/v1)"
echo "  state         → $sdir"
echo "  logs          → $svc_log"
echo "                  $web_log"
echo ""

if [[ $DRY -eq 1 ]]; then
    cat <<EOF
[dry-run] would launch (each log prefixed with a header of args + NORA/SIRA env):

# SIRA service  → $svc_log
NORA_SIRA_DB_ROOT=$db_root NORA_SIRA_DOC_ENRICH_RUN=$enrich_run \\
NORA_SIRA_RERANK_ENABLED=false \\
NORA_LLM_SHIM_URL=$llm_base NORA_LLM_MODEL=$llm_model \\
  uvicorn sandbox.sira_query.service:app --port $svc_port

# NORA web  → $web_log
NORA_SIRA_QUERY_URL=http://127.0.0.1:$svc_port NORA_SIRA_SYNTH_MODE=llm-select \\
NORA_LLM_PROVIDER=$provider NORA_LLM_BASE_URL=$llm_base/v1 \\
NORA_LLM_MODEL=$llm_model NORA_LLM_API_KEY=${api_key:+<set>} \\
  python -m core.src.web.app --port $web_port \\
    --jobs-db $jobs_db --metrics-db $metrics_db \\
    --feedback-db $feedback_db --config-db $config_db
EOF
    exit 0
fi

# Write a header to a log: script args + the role's DB paths + every set
# NORA_/SIRA_ env var (API keys / tokens / secrets redacted). Called INSIDE
# each subshell so `env` reflects that process's exported config.
write_header() {
    local role="$1" logf="$2"
    {
        echo "=========================================================="
        echo "run_stack.sh — $role  (label=$label)  started $ts"
        echo "=========================================================="
        echo "[args]"
        echo "  label         = $label"
        echo "  db_root       = $db_root"
        echo "  service_port  = $svc_port"
        echo "  web_port      = $web_port"
        echo "  llm_base_url  = $llm_base"
        echo "  llm_model     = $llm_model"
        echo "  api_key       = ${api_key:+<set>}"
        echo "  enrich_run    = $enrich_run"
        echo "  provider      = $provider"
        echo "  state_dir     = $sdir"
        echo "  log_dir       = $log_dir"
        if [[ "$role" == web ]]; then
            echo "  jobs-db       = $jobs_db"
            echo "  metrics-db    = $metrics_db"
            echo "  feedback-db   = $feedback_db"
            echo "  config-db     = $config_db"
        fi
        echo "[NORA/SIRA env set in this process]"
        { env | grep -E '^(NORA_|SIRA_)' || true; } \
            | sed -E 's/((API_KEY|TOKEN|SECRET)=).*/\1<redacted>/' | sort | sed 's/^/  /'
        echo "----------------------------------------------------------"
        echo "[$role stdout+stderr follows]"
    } > "$logf"
}

cd "$REPO_ROOT"

# ── SIRA service ─────────────────────────────────────────────────────
(
    export NORA_SIRA_DB_ROOT="$db_root"
    export NORA_SIRA_DOC_ENRICH_RUN="$enrich_run"
    export NORA_SIRA_RERANK_ENABLED=false
    export NORA_LLM_SHIM_URL="$llm_base"
    export NORA_LLM_MODEL="$llm_model"
    [[ -n "$api_key" ]] && export NORA_SIRA_RERANK_LLM_API_KEY="$api_key"
    write_header service "$svc_log"
    nohup uvicorn sandbox.sira_query.service:app --port "$svc_port" \
        >> "$svc_log" 2>&1 &
    echo $! > "$sdir/service.pid"
)

# ── NORA web ─────────────────────────────────────────────────────────
(
    export NORA_SIRA_QUERY_URL="http://127.0.0.1:$svc_port"
    export NORA_SIRA_SYNTH_MODE=llm-select
    export NORA_LLM_PROVIDER="$provider"
    export NORA_LLM_BASE_URL="$llm_base/v1"
    export NORA_LLM_MODEL="$llm_model"
    [[ -n "$api_key" ]] && export NORA_LLM_API_KEY="$api_key"
    write_header web "$web_log"
    nohup python -m core.src.web.app --port "$web_port" \
        --jobs-db "$jobs_db" \
        --metrics-db "$metrics_db" \
        --feedback-db "$feedback_db" \
        --config-db "$config_db" \
        >> "$web_log" 2>&1 &
    echo $! > "$sdir/web.pid"
)

echo "  pids          → service $(cat "$sdir/service.pid"), web $(cat "$sdir/web.pid")"

# ── readiness: poll the SIRA service healthz (the web depends on it) ──
if command -v curl >/dev/null 2>&1; then
    printf "  waiting for service healthz "
    for _ in $(seq 1 20); do
        if curl -sf "http://127.0.0.1:$svc_port/healthz" >/dev/null 2>&1; then
            echo "→ up ✓"; break
        fi
        printf "."; sleep 1
    done
    curl -sf "http://127.0.0.1:$svc_port/healthz" >/dev/null 2>&1 \
        || echo "→ not up yet; check $svc_log"
fi

echo ""
echo "open  http://127.0.0.1:$web_port   ·   stop  sandbox/run_stack.sh --stop $label"
