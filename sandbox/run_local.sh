#!/usr/bin/env bash
# run_local.sh — one-command local SIRA stack on a laptop (macOS tested):
# nora-store (SIRA cells, query enrichment via Ollama) + nora-web (select-synth
# answers via Ollama), plus the Kev decision server for answer intent when you
# have it. Mirrors the served path (D-254): query expansion on, cross-encoder
# rerank off, select-synth on. For A/B stacks against a served-style LLM, use
# sandbox/run_stack.sh instead.
#
#   ENV_DIR=~/work/env_sira sandbox/run_local.sh         # start (logs in $ENV_DIR/logs)
#   ENV_DIR=~/work/env_sira sandbox/run_local.sh stop    # stop store, web and Kev
#
# stop kills only the processes this script started (PIDs in
# $ENV_DIR/logs/run_local.pids), never whatever else listens on the ports.
# Start refuses when a port is already taken (e.g. by a compose stack).
#
# Needs: the NORA venv at .venv (or PYTHON), the SIRA venv at sandbox/sira/.venv (see
# sandbox/SETUP.md), SIRA cells under $ENV_DIR/sira_db, and Ollama serving
# $MODEL with a 32K context (Ollama app → Settings → Context length).
#
# Settings (environment variables, all optional except ENV_DIR):
#   ENV_DIR            the env dir: sira_db/, out/parse/, state/ (required)
#   MODEL              Ollama model for enrichment and answers (default qwen3:8b)
#   SYNTH_BUDGET       select-synth token budget (default 24000, sized for 32K)
#   PYTHON             the NORA venv's python (default .venv/bin/python)
#   STORE_PORT=8040    nora-store port
#   WEB_PORT=8000      nora-web port
#   KEV_DIR            a Kev checkout; when set, Kev serves on KEV_PORT and
#                      nora-web classifies intent confidence-routed (Kev above
#                      the floor, else the rules). Unset: the rules classify.
#   KEV_PORT           Kev's port on 127.0.0.1 (required with KEV_DIR)
#   KEV_TIMEOUT=5      seconds nora-web waits for Kev before the rules answer
#                      (Kev slows sharply when Ollama and Kev compete for memory)
#   ID_LOOKUP=on       a message of only requirement IDs → lookup lane (off: SIRA)
#   PIN_IDS=on         requirement IDs named inside a question go first in the
#                      answer's context, from the Trees store (off: BM25 only)
#   INTENT_PROMPTS=on  per-intent select-synth prompts (off: today's prompt)
#   HISTORY_LANE=false no History store locally, so change questions use SIRA's
#                      change prompt; true routes them to History (an RHD-E003
#                      error without a store)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${ENV_DIR:?set ENV_DIR to your env dir, e.g. ENV_DIR=~/work/env_sira $0}"
LOGS=$ENV_DIR/logs
PIDS=$LOGS/run_local.pids

# Stop only what this script started: each recorded PID and its children.
stop() {
  [ -f "$PIDS" ] || return 0
  while read -r pid; do
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
  done < "$PIDS"
  rm -f "$PIDS"
}
if [ "${1:-}" = "stop" ]; then stop; echo "stopped"; exit 0; fi

[ -d "$ENV_DIR/sira_db" ] || { echo "no SIRA cells at $ENV_DIR/sira_db" >&2; exit 1; }
MODEL=${MODEL:-qwen3:8b}
PYTHON=${PYTHON:-.venv/bin/python}
STORE_PORT=${STORE_PORT:-8040}
WEB_PORT=${WEB_PORT:-8000}
mkdir -p "$LOGS"

stop; sleep 1
cd "$REPO"

PORTS=("$STORE_PORT" "$WEB_PORT")
if [ -n "${KEV_DIR:-}" ]; then
  : "${KEV_PORT:?set KEV_PORT to the port Kev should serve on}"
  PORTS+=("$KEV_PORT")
fi
for port in "${PORTS[@]}"; do
  if lsof -t -i ":$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $port is already in use (another stack?); stop it or pick other ports" >&2
    exit 1
  fi
done

# Kev decision server (strand sira-intent): ~0.5 s per classification warm
# on a laptop. Only when KEV_DIR points at a Kev checkout.
KEV=0
if [ -n "${KEV_DIR:-}" ]; then
  [ -d "$KEV_DIR" ] || { echo "KEV_DIR=$KEV_DIR not found" >&2; exit 1; }
  KEV=1
  (cd "$KEV_DIR" && exec nohup uv run --extra serve python -m kev.serve \
    --run jaredpalmer/kev-4b --port "$KEV_PORT") > "$LOGS/kev.log" 2>&1 &
  echo $! >> "$PIDS"
  ANALYZER=confidence-routed
else
  echo "KEV_DIR not set: intent comes from the rules classifier"
  ANALYZER=rules
fi

# nora-store: needs the SIRA venv (bm25x).
NORA_STORE_ROOT=$ENV_DIR/sira_db \
NORA_SIRA_QUERY_ENRICH_ENABLED=true \
NORA_SIRA_RERANK_ENABLED=false \
NORA_LLM_SHIM_URL=http://localhost:11434 \
NORA_LLM_MODEL=$MODEL \
nohup sandbox/sira/.venv/bin/uvicorn core.src.store_service.service:app \
  --port "$STORE_PORT" > "$LOGS/store.log" 2>&1 &
echo $! >> "$PIDS"

# nora-web: select-synth answers straight from SIRA chunks (no NORA vector store).
NORA_STORE_URL=http://127.0.0.1:$STORE_PORT \
NORA_ID_LOOKUP=${ID_LOOKUP:-on} \
NORA_PIN_NAMED_IDS=${PIN_IDS:-on} \
NORA_SIRA_INTENT_PROMPTS=${INTENT_PROMPTS:-on} \
NORA_HISTORY_DIFF_ENABLED=${HISTORY_LANE:-false} \
NORA_QUERY_ANALYZER=$ANALYZER \
NORA_DECISION_PROVIDER=systemone \
NORA_DECISION_BASE_URL=${KEV_PORT:+http://127.0.0.1:$KEV_PORT} \
NORA_DECISION_TIMEOUT=${KEV_TIMEOUT:-5} \
NORA_SIRA_SYNTH_MODE=select-synth \
NORA_SIRA_SYNTH_TOKEN_BUDGET=${SYNTH_BUDGET:-24000} \
NORA_SIRA_SELECT_SYNTH_TOP_K=20 \
NORA_SIRA_SELECT_SYNTH_TEXT_CHARS=6000 \
NORA_LLM_PROVIDER=ollama \
NORA_LLM_MODEL=$MODEL \
nohup "$PYTHON" -m core.src.web.app --env-dir "$ENV_DIR" --port "$WEB_PORT" \
  > "$LOGS/web.log" 2>&1 &
echo $! >> "$PIDS"

# Our own processes must still be alive: a health answer from some other
# listener does not count.
alive() { while read -r pid; do kill -0 "$pid" 2>/dev/null || return 1; done < "$PIDS"; }
up=0
for _ in $(seq 1 30); do
  alive || break
  curl -s -o /dev/null "localhost:$STORE_PORT/healthz" \
    && curl -s -o /dev/null "localhost:$WEB_PORT/test" && { up=1; break; }
  sleep 2
done
[ "$up" = 1 ] || { echo "store or web did not come up; see $LOGS" >&2; stop; exit 1; }
if [ "$KEV" = 1 ]; then
  for _ in $(seq 1 60); do
    case "$(curl -s "localhost:$KEV_PORT/v1/models")" in *temperature*) break;; esac
    sleep 3
  done
  # Warm-up: Kev's first classification takes ~13 s; spend it here, not on the
  # first real question. Goes through nora's own client, so it is the real path.
  NORA_QUERY_ANALYZER=confidence-routed NORA_DECISION_PROVIDER=systemone \
  NORA_DECISION_BASE_URL="http://127.0.0.1:$KEV_PORT" "$PYTHON" -c \
    "from core.src.web.routes.sira_intent import classify; classify('warm up')" \
    > /dev/null 2>&1 || echo "Kev warm-up failed (see $LOGS/kev.log); nora-web falls back to the rules"
fi
echo "store: http://127.0.0.1:$STORE_PORT/healthz   web: http://127.0.0.1:$WEB_PORT/test   model: $MODEL"
echo "intent: analyzer=$ANALYZER prompts=${INTENT_PROMPTS:-on} id_lookup=${ID_LOOKUP:-on} pin_ids=${PIN_IDS:-on} history_lane=${HISTORY_LANE:-false}"
if [ "$KEV" = 1 ]; then echo "kev:   http://127.0.0.1:$KEV_PORT/v1/models"; fi
