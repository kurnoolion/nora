#!/usr/bin/env bash
# run_stack.sh — launch ONE isolated SIRA-service + NORA-web stack (Path-B),
# wired from args, so you can run several in parallel to A/B different LLMs /
# ingestions. Each stack gets its own ports, its own LLM, and its own web state
# DBs (so Q&A logs / feedback don't conflate across stacks).
#
# Usage:
#   sandbox/run_stack.sh <label> <db_root> <svc_port> <web_port> <llm_base_url> <llm_model> [api_key]
#   sandbox/run_stack.sh --dry-run <label> <db_root> <svc_port> <web_port> <llm_base_url> <llm_model> [api_key]
#   sandbox/run_stack.sh --stop <label>
#
#   <label>        short id (e.g. qwen, prop) — names the state dir + logs
#   <db_root>      the ingestion dir (NORA_SIRA_DB_ROOT) for this stack
#   <svc_port>     SIRA service port (e.g. 8040)
#   <web_port>     NORA web port (e.g. 8080)
#   <llm_base_url> LLM endpoint base WITHOUT /v1 (e.g. http://dgx:8000);
#                  used as-is for the SIRA query-enrich shim and with /v1
#                  appended for the NORA synthesis LLM
#   <llm_model>    model name for both query-enrich and synthesis
#   [api_key]      optional bearer key for the LLM endpoint
#
# Env overrides (optional):
#   NORA_SIRA_DOC_ENRICH_RUN   pinned doc-enrich run name      (default: enrich-stable)
#   NORA_LLM_PROVIDER          synthesis provider tag          (default: openai)
#   NORA_STACK_STATE_DIR       base dir for per-stack state     (default: /tmp/nora-stacks)
#
# Notes:
#   * Run from your SIRA venv (python + uvicorn on PATH).
#   * Config is read at IMPORT time, so each process is pinned to its launch
#     env — that's why parallel stacks need separate processes, which this does.
#   * Path-B requires rerank OFF on the service; this sets NORA_SIRA_RERANK_ENABLED=false.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_BASE="${NORA_STACK_STATE_DIR:-/tmp/nora-stacks}"

# Print the leading comment block (everything after the shebang up to the first
# non-comment line) as help text.
usage() { awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit "${1:-0}"; }

# ── stop mode ────────────────────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
    label="${2:-}"
    [[ -n "$label" ]] || { echo "error: --stop needs a <label>" >&2; exit 2; }
    sdir="$STATE_BASE/$label"
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

# ── dry-run flag ─────────────────────────────────────────────────────
DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY=1; shift; fi
[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && usage 0
[[ $# -ge 6 ]] || { echo "error: expected 6 args (got $#)" >&2; usage 2; }

label="$1"; db_root="$2"; svc_port="$3"; web_port="$4"; llm_base="$5"; llm_model="$6"
api_key="${7:-}"
enrich_run="${NORA_SIRA_DOC_ENRICH_RUN:-enrich-stable}"
provider="${NORA_LLM_PROVIDER:-openai}"
llm_base="${llm_base%/}"                       # strip trailing slash
sdir="$STATE_BASE/$label"

if [[ $DRY -eq 0 ]]; then
    [[ -d "$db_root" ]] || { echo "error: db_root not found: $db_root" >&2; exit 1; }
    mkdir -p "$sdir"
fi

echo "stack '$label':"
echo "  SIRA service  → http://127.0.0.1:$svc_port   (db_root=$db_root, enrich_run=$enrich_run, rerank=off)"
echo "  NORA web      → http://127.0.0.1:$web_port    (synth LLM=$llm_model @ $llm_base/v1)"
echo "  state/logs    → $sdir"
echo ""

if [[ $DRY -eq 1 ]]; then
    cat <<EOF
[dry-run] would launch:

# SIRA service
NORA_SIRA_DB_ROOT=$db_root \\
NORA_SIRA_DOC_ENRICH_RUN=$enrich_run \\
NORA_SIRA_RERANK_ENABLED=false \\
NORA_LLM_SHIM_URL=$llm_base NORA_LLM_MODEL=$llm_model \\
  uvicorn sandbox.sira_query.service:app --port $svc_port

# NORA web
NORA_SIRA_QUERY_URL=http://127.0.0.1:$svc_port \\
NORA_SIRA_SYNTH_MODE=llm-select \\
NORA_LLM_PROVIDER=$provider NORA_LLM_BASE_URL=$llm_base/v1 \\
NORA_LLM_MODEL=$llm_model NORA_LLM_API_KEY=${api_key:+<set>} \\
  python -m core.src.web.app --port $web_port \\
    --jobs-db $sdir/jobs.db --metrics-db $sdir/metrics.db \\
    --feedback-db $sdir/feedback.db --config-db $sdir/config.db
EOF
    exit 0
fi

cd "$REPO_ROOT"

# ── SIRA service ─────────────────────────────────────────────────────
(
    export NORA_SIRA_DB_ROOT="$db_root"
    export NORA_SIRA_DOC_ENRICH_RUN="$enrich_run"
    export NORA_SIRA_RERANK_ENABLED=false
    export NORA_LLM_SHIM_URL="$llm_base"
    export NORA_LLM_MODEL="$llm_model"
    [[ -n "$api_key" ]] && export NORA_SIRA_RERANK_LLM_API_KEY="$api_key"
    nohup uvicorn sandbox.sira_query.service:app --port "$svc_port" \
        > "$sdir/service.log" 2>&1 &
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
    nohup python -m core.src.web.app --port "$web_port" \
        --jobs-db "$sdir/jobs.db" \
        --metrics-db "$sdir/metrics.db" \
        --feedback-db "$sdir/feedback.db" \
        --config-db "$sdir/config.db" \
        > "$sdir/web.log" 2>&1 &
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
        || echo "→ not up yet; check $sdir/service.log"
fi

echo ""
echo "open  http://127.0.0.1:$web_port   ·   stop  sandbox/run_stack.sh --stop $label"
