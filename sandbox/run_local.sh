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
# Needs: the NORA venv at .venv, the SIRA venv at sandbox/sira/.venv (see
# sandbox/SETUP.md), SIRA cells under $ENV_DIR/sira_db, and Ollama serving
# $MODEL with a 32K context (Ollama app → Settings → Context length).
#
# Settings (environment variables, all optional except ENV_DIR):
#   ENV_DIR            the env dir: sira_db/, out/parse/, state/ (required)
#   MODEL              Ollama model for enrichment and answers (default qwen3:8b)
#   SYNTH_BUDGET       select-synth token budget (default 24000, sized for 32K)
#   KEV_DIR            a Kev checkout; when set, Kev serves on :8009 and nora-web
#                      classifies intent confidence-routed (Kev above the floor,
#                      else the rules). Unset: the rules classify.
#   ID_LOOKUP=on       a message of only requirement IDs → lookup lane (off: SIRA)
#   INTENT_PROMPTS=on  per-intent select-synth prompts (off: today's prompt)
#   HISTORY_LANE=false no History store locally, so change questions use SIRA's
#                      change prompt; true routes them to History (an RHD-E003
#                      error without a store)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTS=(-i :8040 -i :8000 -i :8009)
stop() { lsof -t "${PORTS[@]}" -sTCP:LISTEN | xargs kill 2>/dev/null || true; }
if [ "${1:-}" = "stop" ]; then stop; echo "stopped"; exit 0; fi

: "${ENV_DIR:?set ENV_DIR to your env dir, e.g. ENV_DIR=~/work/env_sira $0}"
[ -d "$ENV_DIR/sira_db" ] || { echo "no SIRA cells at $ENV_DIR/sira_db" >&2; exit 1; }
MODEL=${MODEL:-qwen3:8b}
LOGS=$ENV_DIR/logs
mkdir -p "$LOGS"

stop; sleep 1
cd "$REPO"

# Kev decision server (strand sira-intent): MLX on Apple Silicon, ~0.5 s per
# classification warm. Only when KEV_DIR points at a Kev checkout.
KEV=0
if [ -n "${KEV_DIR:-}" ]; then
  [ -d "$KEV_DIR" ] || { echo "KEV_DIR=$KEV_DIR not found" >&2; exit 1; }
  KEV=1
  (cd "$KEV_DIR" && nohup uv run --extra serve python -m kev.serve \
    --run jaredpalmer/kev-4b --port 8009 > "$LOGS/kev.log" 2>&1 &)
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
  --port 8040 > "$LOGS/store.log" 2>&1 &

# nora-web: select-synth answers straight from SIRA chunks (no NORA vector store).
NORA_STORE_URL=http://127.0.0.1:8040 \
NORA_ID_LOOKUP=${ID_LOOKUP:-on} \
NORA_SIRA_INTENT_PROMPTS=${INTENT_PROMPTS:-on} \
NORA_HISTORY_DIFF_ENABLED=${HISTORY_LANE:-false} \
NORA_QUERY_ANALYZER=$ANALYZER \
NORA_DECISION_PROVIDER=systemone \
NORA_DECISION_BASE_URL=http://127.0.0.1:8009 \
NORA_SIRA_SYNTH_MODE=select-synth \
NORA_SIRA_SYNTH_TOKEN_BUDGET=${SYNTH_BUDGET:-24000} \
NORA_SIRA_SELECT_SYNTH_TOP_K=20 \
NORA_SIRA_SELECT_SYNTH_TEXT_CHARS=6000 \
NORA_LLM_PROVIDER=ollama \
NORA_LLM_MODEL=$MODEL \
nohup .venv/bin/python -m core.src.web.app --env-dir "$ENV_DIR" --port 8000 \
  > "$LOGS/web.log" 2>&1 &

for _ in $(seq 1 30); do
  curl -s -o /dev/null localhost:8040/healthz && curl -s -o /dev/null localhost:8000/test && break
  sleep 2
done
if [ "$KEV" = 1 ]; then
  for _ in $(seq 1 60); do
    case "$(curl -s localhost:8009/v1/models)" in *temperature*) break;; esac
    sleep 3
  done
  # Warm-up: Kev's first classification takes ~13 s; spend it here, not on the
  # first real question. Goes through nora's own client, so it is the real path.
  NORA_QUERY_ANALYZER=confidence-routed NORA_DECISION_PROVIDER=systemone \
  NORA_DECISION_BASE_URL=http://127.0.0.1:8009 .venv/bin/python -c \
    "from core.src.web.routes.sira_intent import classify; classify('warm up')" \
    > /dev/null 2>&1 || echo "Kev warm-up failed (see $LOGS/kev.log); nora-web falls back to the rules"
fi
echo "store: http://127.0.0.1:8040/healthz   web: http://127.0.0.1:8000/test   model: $MODEL"
echo "intent: analyzer=$ANALYZER prompts=${INTENT_PROMPTS:-on} id_lookup=${ID_LOOKUP:-on} history_lane=${HISTORY_LANE:-false}"
if [ "$KEV" = 1 ]; then echo "kev:   http://127.0.0.1:8009/v1/models"; fi
