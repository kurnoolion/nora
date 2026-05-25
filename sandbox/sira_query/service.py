"""SIRA per-query inference service — for NORA's Test page SIRA tab.

Loads SIRA's BM25 index + corpus + telecom prompts once at startup,
exposes `POST /sira-query` for interactive per-query retrieval.
Mirrors the same end-to-end pipeline SIRA's batch scripts run:

    1. Query enrichment via LLM (uses `query_requirement_v01.txt`)
    2. DF-filter the expansion phrases via bm25x.filter_query_expansion
    3. BM25 search with weighted expansion via bm25x.search_with_expansion
    4. LLM pointwise rerank of top_n candidates (uses `relevance_requirement_v01.txt`)
    5. Return top_k ranked results

All LLM calls go through the existing FastAPI shim on port 8030.

Run (from the sandbox/sira/.venv):

    source ~/work/nora/sandbox/activate.sh
    export NORA_SIRA_DB_ROOT=$HOME/work/nora/sandbox/adapter/out
    uvicorn sandbox.sira_query.service:app --port 8040

NORA's Test page (`/test`) gets a SIRA tab that posts to this service.
See sandbox/SETUP.md "Per-query SIRA probe" for the full setup.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Config from env ─────────────────────────────────────────────────

_DB_ROOT = os.getenv("NORA_SIRA_DB_ROOT", "")
_DATASET = os.getenv("NORA_SIRA_DATASET", "nora")
_SHIM_URL = os.getenv("NORA_LLM_SHIM_URL", "http://127.0.0.1:8030").rstrip("/")
_SHIM_MODEL = os.getenv("NORA_LLM_MODEL", "")

# Rerank-only LLM override. Query enrichment continues to go through
# the standard shim (and so reaches whatever upstream LLM the shim
# was configured with — typically the proprietary 100B+). Setting any
# of these env vars routes the rerank stage to a DIFFERENT endpoint
# without touching the rest of the pipeline. Use case: keep proprietary
# LLM for high-quality query enrichment, swap to local Ollama for fast
# rerank (proprietary path is ~5s/call × 50 candidates = 4min; local
# 8B Ollama is ~300ms/call × 50 = ~15s).
#
# Env vars (all optional, all empty → use shim defaults):
#   NORA_SIRA_RERANK_LLM_URL   base URL (e.g. http://localhost:11434
#                              for Ollama's OpenAI-compat endpoint)
#   NORA_SIRA_RERANK_LLM_MODEL model name (e.g. qwen3:8b-q4_k_m)
#   NORA_SIRA_RERANK_LLM_API_KEY  auth header value; non-empty needed
#                              for some endpoints, Ollama ignores it.
_RERANK_LLM_URL = os.getenv("NORA_SIRA_RERANK_LLM_URL", "").rstrip("/")
_RERANK_LLM_MODEL = os.getenv("NORA_SIRA_RERANK_LLM_MODEL", "")
_RERANK_LLM_API_KEY = os.getenv("NORA_SIRA_RERANK_LLM_API_KEY", "")

# Max tokens for the rerank LLM call. Default was 64 sized for the
# proprietary LLM (no chain-of-thought preamble). Modern reasoning-
# trained models (Qwen3, DeepSeek-R1, etc.) emit <think>...</think>
# blocks before the actual answer — at 64 tokens the response gets
# cut off inside the thinking block and the {"score": <int>} JSON
# never reaches the parser, which then returns 0 for every chunk.
# 256 is a safe default for CoT-capable models; bump to 512+ if you
# see thinking blocks that get cut off.
_RERANK_MAX_TOKENS = int(os.getenv("NORA_SIRA_RERANK_MAX_TOKENS", "256"))

# Batch rerank: score N chunks per LLM call instead of one chunk per
# call. Saves 25× API round-trip overhead when set to top_n. Default
# 0 = per-call mode (current behavior). Set to e.g. 25 to batch all
# candidates into one call. Smaller batch sizes (5-10) trade some
# speed for less context pressure on the LLM.
#
# Failure-mode tradeoff: per-call degrades gracefully (one bad call =
# one zero score). Batch fails atomically (one bad call = N zero
# scores for that batch). Worth testing on your model before committing.
_RERANK_BATCH_SIZE = int(os.getenv("NORA_SIRA_RERANK_BATCH_SIZE", "0"))

# Output budget when batching. Each chunk needs ~10-15 tokens in the
# JSON output ({"id": N, "score": NN}, plus formatting). 4096 covers
# batch_size up to ~50 with CoT models that add a thinking preamble.
_RERANK_BATCH_MAX_TOKENS = int(os.getenv("NORA_SIRA_RERANK_BATCH_MAX_TOKENS", "4096"))

# Plan-aware-sira fan-out (see strand plan-aware-sira / D-DRAFT-10).
# The BEIR adapter now emits doc-level (`doc:<plan>`) and section-level
# (`section:<plan>:<num>`) rows alongside per-requirement rows. The
# multi-granularity rows carry req_id POINTER LISTS rather than full
# content — they exist to provide a strong matching signal for plan-
# level queries while sidestepping BM25's length-norm penalty. At
# retrieval time, this service fans them out into their constituent
# req-level chunks so the synthesizer receives real content (and so
# req-level citations resolve correctly).
_FANOUT_ENABLED = os.getenv("NORA_SIRA_FANOUT_ENABLED", "true").lower() in {
    "1", "true", "yes", "on",
}
# Max req_ids to fan out from a single doc/section hit. Bounds the
# synthesizer's context budget; large plans (200+ reqs) would
# otherwise dominate the top-K.
_FANOUT_PER_HIT = int(os.getenv("NORA_SIRA_FANOUT_PER_HIT", "50"))

# Defaults to ../sira/ relative to this file (the upstream clone).
_SIRA_CLONE_ROOT = Path(os.getenv(
    "NORA_SIRA_CLONE_ROOT",
    str(Path(__file__).resolve().parents[1] / "sira"),
))
_QUERY_PROMPT_PATH = os.getenv(
    "NORA_QUERY_PROMPT",
    "scripts/configs/enrich/prompts/query_requirement_v01.txt",
)
_RERANK_PROMPT_PATH = os.getenv(
    "NORA_RERANK_PROMPT",
    "scripts/configs/rerank/prompts/relevance_requirement_v01.txt",
)

# SIRA pipeline knobs — defaults chosen for interactive use, not eval.
# Rerank top_n in particular needs to be small for interactive: at
# concurrency=1 + ~36s/call (proxy-throttled work-PC environment),
# top_n=200 means ~2 hours per query. top_n=20 is ~12 min — slow but
# tolerable for a diagnostic probe.
_MAX_DF_RATIO = float(os.getenv("NORA_SIRA_MAX_DF_RATIO", "0.05"))
_EXPANSION_WEIGHT = float(os.getenv("NORA_SIRA_EXPANSION_WEIGHT", "0.5"))
_DEFAULT_TOP_K = int(os.getenv("NORA_SIRA_TOP_K", "10"))
_RERANK_TOP_N = int(os.getenv("NORA_SIRA_RERANK_TOP_N", "20"))

# Quick-disable for the LLM-as-judge rerank stage. The reranker makes
# one LLM call per candidate via the shim; at proxy-throttled
# concurrency=1 that's the dominant latency in interactive queries
# (~5s mean, ~18s p95 on user's work PC). Set false to skip the
# rerank stage entirely and return BM25-with-expansion results in
# BM25-score order — useful for measuring whether rerank is adding
# meaningful Recall@10 lift on a given corpus + prompt combination.
_RERANK_ENABLED = os.getenv("NORA_SIRA_RERANK_ENABLED", "true").lower() in {
    "1", "true", "yes", "on",
}

# Run-pinning — determinism across "which offline run drives this service?".
# Three knobs (each independent):
#   NORA_SIRA_DOC_ENRICH_RUN   — exact run-name under runs/doc-enrich/<name>
#   NORA_SIRA_QUERY_ENRICH_RUN — exact run-name under runs/query-enrich/<name>
#   NORA_SIRA_RERANK_RUN       — exact run-name under runs/rerank/<name>
# Plus a shortcut:
#   NORA_SIRA_USE_LATEST_RUNS=true — auto-pick most-recently-modified run
#                                    in each stage dir (when above env vars
#                                    are unset).
# Fallback when nothing resolves: best.jsonl / SIRA_CLONE_ROOT prompts
# (the historical pre-patch behavior).
_DOC_ENRICH_RUN = os.getenv("NORA_SIRA_DOC_ENRICH_RUN", "")
_QUERY_ENRICH_RUN = os.getenv("NORA_SIRA_QUERY_ENRICH_RUN", "")
_RERANK_RUN = os.getenv("NORA_SIRA_RERANK_RUN", "")
_USE_LATEST = os.getenv("NORA_SIRA_USE_LATEST_RUNS", "").lower() in ("1", "true", "yes")


# ── Lazy-loaded state ──────────────────────────────────────────────

_bm25 = None
_doc_ids: list[str] = []
_doc_id_to_idx: dict[str, int] = {}
_corpus_by_id: dict[str, dict[str, str]] = {}
_query_prompt_template: str = ""
_rerank_prompt_template: str = ""
_max_df_absolute: int = 0
_load_error: str | None = None

# Provenance — surfaced via /healthz so the user can verify what's
# actually loaded without grepping startup logs.
_doc_enrich_source: str | None = None
_doc_enrich_applied_docs: int = 0
_query_prompt_source: str | None = None
_rerank_prompt_source: str | None = None


def _resolve_run_dir(stage_dir: Path, pinned_name: str) -> Path | None:
    """Resolve a per-stage run directory.

    - If pinned_name is non-empty, use that exact subdir (or fail).
    - Else if NORA_SIRA_USE_LATEST_RUNS is set, pick the most-recently-
      modified subdir.
    - Else return None (caller falls back to best-pointer behavior).
    """
    if pinned_name:
        cand = stage_dir / pinned_name
        return cand if cand.is_dir() else None
    if _USE_LATEST and stage_dir.is_dir():
        subs = [p for p in stage_dir.iterdir() if p.is_dir()]
        if subs:
            return max(subs, key=lambda p: p.stat().st_mtime)
    return None


def _load_state() -> None:
    """Load BM25 index + corpus + prompts. Called once on first use.

    All errors are stashed in `_load_error` rather than raising, so
    /healthz can report them — `uvicorn` shouldn't crash on a partial
    setup; the user can curl healthz to see what's missing.
    """
    global _bm25, _max_df_absolute, _query_prompt_template, _rerank_prompt_template, _load_error
    global _doc_enrich_source, _doc_enrich_applied_docs
    global _query_prompt_source, _rerank_prompt_source
    if _bm25 is not None:
        return

    if not _DB_ROOT:
        _load_error = (
            "NORA_SIRA_DB_ROOT not set. Point at the adapter output, "
            "e.g. ~/work/nora/sandbox/adapter/out"
        )
        return

    base = Path(_DB_ROOT) / _DATASET
    corpus_path = base / "raw" / "corpus.jsonl"
    index_dir = base / "index" / "best"

    if not corpus_path.exists():
        _load_error = f"corpus.jsonl not found at {corpus_path}"
        return
    if not index_dir.exists():
        _load_error = (
            f"BM25 index not found at {index_dir}. "
            "Run `python scripts/eval_bm25.py data=nora db_root=…` first."
        )
        return

    # Corpus: build id → {title, text} mapping. Also build the
    # reverse id → index lookup used by fan-out (plan-aware-sira).
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            rid = obj["_id"]
            _doc_id_to_idx[rid] = len(_doc_ids)
            _doc_ids.append(rid)
            _corpus_by_id[rid] = {
                "title": obj.get("title", ""),
                "text": obj.get("text", ""),
            }
    _max_df_absolute = max(1, int(len(_doc_ids) * _MAX_DF_RATIO))

    # BM25 index — baseline (not run-specific). SIRA's enrichment is
    # applied to a loaded index via batch_enrich; the index file itself
    # is the vanilla one regardless of which doc-enrich run we use.
    from bm25x import BM25
    _bm25 = BM25.load(str(index_dir))

    # Resolve per-stage run directories (or None if falling back to
    # best-pointer behavior).
    doc_run = _resolve_run_dir(base / "runs" / "doc-enrich", _DOC_ENRICH_RUN)
    query_run = _resolve_run_dir(base / "runs" / "query-enrich", _QUERY_ENRICH_RUN)
    rerank_run = _resolve_run_dir(base / "runs" / "rerank", _RERANK_RUN)

    # Doc enrichment: apply phrases to the loaded BM25 index. Prefer the
    # pinned/latest run's `enrichments.kept.jsonl`; fall back to
    # `enrichments/doc/best.jsonl` (the SIRA-promoted best run by score).
    # If neither exists, the service runs vanilla BM25 + query-side
    # enrichment only (the historical pre-patch behavior).
    phrases_path: Path | None = None
    if doc_run is not None:
        cand = doc_run / "enrichments.kept.jsonl"
        if cand.exists():
            phrases_path = cand
    if phrases_path is None:
        fallback = base / "enrichments" / "doc" / "best.jsonl"
        if fallback.exists() and fallback.stat().st_size > 0:
            phrases_path = fallback

    if phrases_path is not None:
        doc_id_to_idx = {did: i for i, did in enumerate(_doc_ids)}
        # Aggregate phrases per doc_id — `enrichments.kept.jsonl` can have
        # multiple lines for the same doc_id (from sharded enrichment
        # runs). Mirror SIRA's add_doc_index_adapter.py:365-371 pattern.
        enrichments: dict[str, list[str]] = {}
        missing_in_corpus = 0
        with open(phrases_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                did = row.get("doc_id") or row.get("_id")
                phrases = row.get("phrases") or []
                if not did or not phrases:
                    continue
                if did in doc_id_to_idx:
                    enrichments.setdefault(did, []).extend(phrases)
                else:
                    missing_in_corpus += 1
        items: list[tuple[int, list[str]]] = [
            (doc_id_to_idx[did], phrases)
            for did, phrases in enrichments.items()
        ]
        try:
            # The bm25x Python API method is `enrich_batch` (verb_noun);
            # the Rust internal docstring says "Batch enrich:" which
            # describes the action, not the binding name.
            _bm25.enrich_batch(items)
            _doc_enrich_applied_docs = len(items)
            _doc_enrich_source = str(phrases_path)
            logger.info(
                "Doc enrichment applied: %d docs from %s",
                len(items), phrases_path,
            )
            if missing_in_corpus:
                logger.warning(
                    "Phrases file referenced %d doc_ids not in corpus.jsonl (skipped)",
                    missing_in_corpus,
                )
        except Exception as exc:
            logger.error(
                "enrich_batch failed (%s) — falling back to vanilla BM25", exc,
            )
            _doc_enrich_source = None
            _doc_enrich_applied_docs = 0
    else:
        logger.warning(
            "No doc-enrichment phrases found — running vanilla BM25 + query-side enrichment only"
        )

    # Query enrichment prompt — prefer the run's own copy so the prompt
    # used at query time is byte-identical to what the offline pipeline
    # used. Fall back to SIRA_CLONE_ROOT/sandbox-copied prompt if not
    # found in the run.
    qp_path: Path | None = None
    if query_run is not None:
        cand = query_run / "query_prompt.txt"
        if cand.exists():
            qp_path = cand
    if qp_path is None:
        fallback_qp = _SIRA_CLONE_ROOT / _QUERY_PROMPT_PATH
        if fallback_qp.exists():
            qp_path = fallback_qp
    if qp_path is not None:
        _query_prompt_template = qp_path.read_text(encoding="utf-8")
        _query_prompt_source = str(qp_path)
        logger.info("Query enrichment prompt loaded from %s", qp_path)
    else:
        _query_prompt_template = ""
        logger.warning(
            "Query enrichment prompt not found in run dir or SIRA_CLONE_ROOT — expansion stage skipped"
        )

    # Rerank prompt — same logic.
    rp_path: Path | None = None
    if rerank_run is not None:
        cand = rerank_run / "prompt.txt"
        if cand.exists():
            rp_path = cand
    if rp_path is None:
        fallback_rp = _SIRA_CLONE_ROOT / _RERANK_PROMPT_PATH
        if fallback_rp.exists():
            rp_path = fallback_rp
    if rp_path is not None:
        _rerank_prompt_template = rp_path.read_text(encoding="utf-8")
        _rerank_prompt_source = str(rp_path)
        logger.info("Rerank prompt loaded from %s", rp_path)
    else:
        _rerank_prompt_template = ""
        logger.warning(
            "Rerank prompt not found in run dir or SIRA_CLONE_ROOT — rerank stage skipped"
        )

    _load_error = None
    logger.info(
        "SIRA query service ready — corpus=%d docs, max_df=%d, expansion_weight=%.2f, top_n=%d, doc_enrich_applied=%d",
        len(_doc_ids), _max_df_absolute, _EXPANSION_WEIGHT, _RERANK_TOP_N,
        _doc_enrich_applied_docs,
    )


# ── FastAPI app ────────────────────────────────────────────────────

app = FastAPI(title="NORA SIRA per-query probe")


class _SiraQueryRequest(BaseModel):
    query: str
    top_k: int | None = None


@app.on_event("startup")
def _startup() -> None:
    try:
        _load_state()
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("Startup load failed")
        global _load_error
        _load_error = f"unexpected: {exc}"


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": _bm25 is not None,
        "load_error": _load_error,
        "db_root": _DB_ROOT,
        "dataset": _DATASET,
        "corpus_size": len(_doc_ids),
        "max_df_ratio": _MAX_DF_RATIO,
        "max_df_absolute": _max_df_absolute,
        "expansion_weight": _EXPANSION_WEIGHT,
        "default_top_k": _DEFAULT_TOP_K,
        "rerank_top_n": _RERANK_TOP_N,
        "rerank_enabled": _RERANK_ENABLED,
        # Rerank-only LLM override. When any of these is set, rerank
        # calls bypass the shim and go directly to the configured
        # endpoint. Query enrichment continues to use the shim.
        "rerank_llm_url": _RERANK_LLM_URL or "(unset → uses shim)",
        "rerank_llm_model": _RERANK_LLM_MODEL or "(unset → uses shim model)",
        "rerank_llm_api_key_set": bool(_RERANK_LLM_API_KEY),
        "rerank_max_tokens": _RERANK_MAX_TOKENS,
        "rerank_batch_size": _RERANK_BATCH_SIZE,
        "rerank_batch_max_tokens": _RERANK_BATCH_MAX_TOKENS,
        "fanout_enabled": _FANOUT_ENABLED,
        "fanout_per_hit": _FANOUT_PER_HIT,
        # Counts of doc/section rows in the loaded corpus — confirms the
        # adapter wrote multi-granularity rows (plan-aware-sira).
        "n_doc_rows": sum(1 for x in _doc_ids if x.startswith("doc:")),
        "n_section_rows": sum(1 for x in _doc_ids if x.startswith("section:")),
        "n_req_rows": sum(
            1 for x in _doc_ids
            if not x.startswith("doc:") and not x.startswith("section:")
        ),
        "shim_url": _SHIM_URL,
        "shim_model": _SHIM_MODEL or "(unset — falls back to whatever the shim sends)",
        "query_prompt_loaded": bool(_query_prompt_template),
        "rerank_prompt_loaded": bool(_rerank_prompt_template),
        # Provenance — verify everything ties back to the run you expect.
        "doc_enrich_source": _doc_enrich_source or "(none — vanilla BM25)",
        "doc_enrich_applied_docs": _doc_enrich_applied_docs,
        "query_prompt_source": _query_prompt_source or "(none)",
        "rerank_prompt_source": _rerank_prompt_source or "(none)",
        "doc_enrich_run_pinned": _DOC_ENRICH_RUN or "(unset)",
        "query_enrich_run_pinned": _QUERY_ENRICH_RUN or "(unset)",
        "rerank_run_pinned": _RERANK_RUN or "(unset)",
        "use_latest_runs": _USE_LATEST,
    }


# ── LLM call helpers ───────────────────────────────────────────────

async def _llm_call(
    client: httpx.AsyncClient,
    prompt: str,
    max_tokens: int = 512,
    temperature: float = 0.0,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    """One OpenAI-shaped chat-completion call. Routes to the shim by
    default; pass `base_url` / `model` / `api_key` to override for a
    specific call site (used by the rerank stage to swap to a local
    Ollama LLM while leaving query enrichment on the shim/proprietary
    path)."""
    url = base_url or _SHIM_URL
    used_model = model or _SHIM_MODEL or "sira-shim"
    payload: dict[str, Any] = {
        "model": used_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers: dict[str, str] = {}
    if api_key:
        # OpenAI-style Authorization header. Ollama ignores it but
        # accepts it; some OpenAI-compat endpoints require it.
        headers["Authorization"] = f"Bearer {api_key}"
    resp = await client.post(
        f"{url}/v1/chat/completions", json=payload, headers=headers or None,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"LLM endpoint ({url}) returned {resp.status_code}: {resp.text[:200]}"
        )
    data = resp.json()
    return data["choices"][0]["message"]["content"] or ""


def _parse_phrases(raw: str) -> list[str]:
    """Mirror sira.llm.parse_phrases — pull `keywords` list from a
    JSON object embedded in the LLM response."""
    end_char = {"{": "}", "[": "]"}
    for start in ("{", "["):
        idx = raw.find(start)
        if idx == -1:
            continue
        end = raw.rfind(end_char[start])
        if end <= idx:
            continue
        try:
            parsed = json.loads(raw[idx : end + 1])
            if isinstance(parsed, dict):
                parsed = parsed.get("keywords", [])
            if isinstance(parsed, list):
                return [p for p in parsed if isinstance(p, str) and p.strip()]
        except json.JSONDecodeError:
            continue
    return []


def _parse_score(raw: str) -> int:
    """Pull integer `score` from a JSON object in the LLM response.
    Returns 0 on any parse failure (matches SIRA's reranker behavior)."""
    idx = raw.find("{")
    end = raw.rfind("}")
    if idx == -1 or end <= idx:
        return 0
    try:
        obj = json.loads(raw[idx : end + 1])
        return int(obj.get("score", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0


# ── Multi-granularity fan-out (plan-aware-sira / D-DRAFT-10) ───────


def _is_pointer_row(corpus_id: str) -> bool:
    """True for doc-level / section-level rows whose text body is a
    pointer list rather than full requirement content."""
    return corpus_id.startswith("doc:") or corpus_id.startswith("section:")


def _extract_referenced_req_ids(text: str) -> list[str]:
    """Pull req_ids out of a doc/section row's text body.

    The adapter writes the body as a "Contains N requirements:\\n<space-
    separated ids>" block. Implementation just splits on whitespace
    and keeps any token that exists in `_corpus_by_id` — corpus-
    agnostic (no assumption about req_id prefix shape) and tolerant of
    surrounding text. Preserves the order ids appear in the body.
    """
    seen: set[str] = set()
    out: list[str] = []
    for tok in text.split():
        tok = tok.strip().strip(",.;()[]")
        if not tok or tok in seen:
            continue
        if tok in _corpus_by_id and not _is_pointer_row(tok):
            seen.add(tok)
            out.append(tok)
    return out


def _fanout_reranked(
    reranked: list[tuple[int, float, int]],
) -> list[tuple[int, float, int, str]]:
    """Expand doc/section pointer rows into their constituent req-level
    chunks. Returns 4-tuples `(idx, bm25_score, rerank_score, source)`
    where `source` is `"direct"` for normally-retrieved rows or
    `"fanout:<parent_id>"` for expanded children.

    Behavior:
      - Preserves input rerank ordering for direct results.
      - Fanned-out children are inserted immediately after their
        parent doc/section row.
      - Dedup by chunk-index — a child req only appears once even if
        retrieved directly AND fanned out from multiple parents.
        First occurrence wins.
      - Each fanned-out child INHERITS its parent's rerank score (so
        downstream score-based filtering treats them as the parent's
        equivalent).
      - Per-hit cap (`_FANOUT_PER_HIT`) bounds the synthesizer's
        context budget — large plans don't dominate top-K.

    When `_FANOUT_ENABLED` is False, returns the input list tagged
    `"direct"` for every row (no expansion).
    """
    if not _FANOUT_ENABLED:
        return [(idx, bm, rr, "direct") for idx, bm, rr in reranked]

    out: list[tuple[int, float, int, str]] = []
    seen_idx: set[int] = set()
    for idx, bm, rr in reranked:
        if idx in seen_idx:
            continue
        seen_idx.add(idx)
        cid = _doc_ids[idx]
        out.append((idx, bm, rr, "direct"))
        if not _is_pointer_row(cid):
            continue
        # Pointer row — fan out
        text = _corpus_by_id.get(cid, {}).get("text", "")
        child_ids = _extract_referenced_req_ids(text)
        added = 0
        for child_rid in child_ids:
            if added >= _FANOUT_PER_HIT:
                break
            child_idx = _doc_id_to_idx.get(child_rid)
            if child_idx is None or child_idx in seen_idx:
                continue
            seen_idx.add(child_idx)
            out.append((child_idx, 0.0, rr, f"fanout:{cid}"))
            added += 1
    return out


def _format_batch_rerank_prompt(query: str, docs: list[tuple[int, str]]) -> str:
    """Build a batch-scoring prompt for `docs = [(local_id, text), ...]`.
    Output spec is a JSON array of `{"id": N, "score": <0-100>}` in
    document order. Rubric mirrors the per-call relevance_v01 prompt's
    scoring bands so batch mode produces comparable scores."""
    docs_block = "\n\n".join(
        f"[{lid}] {text[:4000]}" for lid, text in docs
    )
    return (
        "You are scoring documents for relevance to a query. For each "
        "document, output an integer 0-100 score.\n\n"
        "Scoring rubric:\n"
        "- 0: completely unrelated topic, no shared concepts\n"
        "- 1-20: tangentially related (shared spec family or RAT) but no "
        "sentence addresses the query\n"
        "- 21-40: same procedure / topic family, no specific answer\n"
        "- 41-60: heading/anchor for the queried topic, OR partial info\n"
        "- 61-80: contains the answer in one normative sentence "
        "(\"shall\" / \"shall not\" / \"should\")\n"
        "- 81-100: directly and clearly answers the query with a normative verb\n"
        "\n"
        f"Query: {query}\n\n"
        "Documents:\n"
        f"{docs_block}\n\n"
        "Output ONLY a JSON array, one entry per document, in the same "
        "order. Use the bracketed id from each document. Example shape:\n"
        '[{"id": 0, "score": 75}, {"id": 1, "score": 12}, ...]'
    )


def _parse_batch_scores(raw: str, expected_ids: list[int]) -> dict[int, int]:
    """Extract `{id: score}` pairs from a batch rerank LLM response.

    Returns a dict keyed by local id. Missing / unparseable ids
    default to 0 (matches per-call _parse_score failure semantics).
    Tolerates: CoT thinking preambles before the JSON, trailing
    commentary after, single-object vs nested array, extra whitespace.
    """
    out: dict[int, int] = {i: 0 for i in expected_ids}
    # Find the JSON array: first '[' followed by content with ']' that
    # parses as a list. Naively find first '[' / last ']' and try
    # json.loads; if that fails, scan for individual `{"id": N, "score": M}`
    # objects as a fallback.
    lb = raw.find("[")
    rb = raw.rfind("]")
    if lb != -1 and rb > lb:
        try:
            arr = json.loads(raw[lb : rb + 1])
            if isinstance(arr, list):
                for item in arr:
                    if not isinstance(item, dict):
                        continue
                    try:
                        i = int(item.get("id"))
                        s = int(item.get("score", 0))
                    except (TypeError, ValueError):
                        continue
                    if i in out:
                        out[i] = max(0, min(100, s))
                return out
        except json.JSONDecodeError:
            pass
    # Fallback: per-object regex-ish scan. Helps when the model emits
    # commentary between objects or wraps each in backticks.
    import re
    obj_re = re.compile(
        r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"score"\s*:\s*(\d+)\s*\}'
    )
    for m in obj_re.finditer(raw):
        try:
            i = int(m.group(1))
            s = int(m.group(2))
        except ValueError:
            continue
        if i in out:
            out[i] = max(0, min(100, s))
    return out


# ── Main endpoint ──────────────────────────────────────────────────

@app.post("/sira-query")
async def sira_query(req: _SiraQueryRequest) -> dict[str, Any]:
    """Run the full SIRA pipeline on a single query and return the
    top-K reranked results.

    Pipeline:
        1. Query enrichment (LLM call → DF-filter → tokenize)
        2. BM25 search with expansion (top_n candidates)
        3. LLM pointwise rerank of those candidates
        4. Sort by rerank score, return top_k
    """
    _load_state()
    if _load_error:
        raise HTTPException(status_code=503, detail=_load_error)

    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query is empty")

    top_k = req.top_k if (req.top_k and req.top_k > 0) else _DEFAULT_TOP_K
    top_n = max(top_k, _RERANK_TOP_N)
    timings: dict[str, int] = {}
    notes: list[str] = []

    async with httpx.AsyncClient(timeout=300.0) as client:
        # 1. Query enrichment ---------------------------------------
        t0 = time.time()
        kept_phrases: list[str] = []
        expansion_terms = ""
        if _query_prompt_template:
            try:
                prompt = _query_prompt_template.format(doc_text=req.query, max_n=4)
                raw = await _llm_call(client, prompt, max_tokens=512, temperature=0.4)
                proposed = _parse_phrases(raw)
                # DF-filter via bm25x — exactly what the batch script does.
                kept_phrases, _rejected = _bm25.filter_query_expansion(
                    req.query, proposed, _max_df_absolute,
                )
                kept_stems: list[str] = []
                for p in kept_phrases:
                    kept_stems.extend(_bm25.tokenize(p))
                expansion_terms = " ".join(kept_stems) if kept_stems else ""
            except Exception as exc:
                notes.append(f"query-enrich failed (continuing without expansion): {exc}")
        else:
            notes.append("query enrichment prompt missing — search runs without expansion")
        timings["expand_ms"] = int((time.time() - t0) * 1000)

        # 2. BM25 search with expansion -----------------------------
        t0 = time.time()
        try:
            results = _bm25.search_with_expansion(
                [req.query], [expansion_terms],
                k=top_n, weight=_EXPANSION_WEIGHT,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"bm25 search failed: {exc}")
        hits: list[tuple[int, float]] = list(results[0])
        timings["search_ms"] = int((time.time() - t0) * 1000)

        # 3. LLM rerank ---------------------------------------------
        # IMPORTANT: at concurrency=1 (default in our nora.yaml for
        # proxy-throttled environments) this stage is the slow one —
        # top_n × ~per_call_latency. We process serially via asyncio
        # but each call still goes one-at-a-time through the shim.
        # Per-call timing is collected for the response so the user
        # can see the latency distribution + any outliers.
        t0 = time.time()
        reranked: list[tuple[int, float, int]] = []
        rerank_call_ms: list[int] = []
        if _RERANK_ENABLED and _rerank_prompt_template and hits:
            try:
                if _RERANK_BATCH_SIZE > 0:
                    # ── Batch mode ──────────────────────────────────
                    # Score N chunks per LLM call. One call's failure
                    # zeroes the whole batch (per-call mode degrades
                    # one chunk at a time).
                    rerank_scores_by_idx: dict[int, int] = {}
                    for batch_start in range(0, len(hits), _RERANK_BATCH_SIZE):
                        batch_hits = hits[batch_start : batch_start + _RERANK_BATCH_SIZE]
                        docs = []
                        for local_id, (idx, _bm25_score) in enumerate(batch_hits):
                            rid = _doc_ids[idx]
                            doc = _corpus_by_id[rid]
                            doc_text = (f"{doc['title']}\n\n{doc['text']}")[:4000]
                            docs.append((local_id, doc_text))
                        prompt = _format_batch_rerank_prompt(req.query, docs)
                        call_t0 = time.time()
                        try:
                            raw = await _llm_call(
                                client, prompt,
                                max_tokens=_RERANK_BATCH_MAX_TOKENS,
                                temperature=0.0,
                                base_url=_RERANK_LLM_URL or None,
                                model=_RERANK_LLM_MODEL or None,
                                api_key=_RERANK_LLM_API_KEY or None,
                            )
                            scores_map = _parse_batch_scores(
                                raw, [lid for lid, _ in docs],
                            )
                        except Exception as exc:
                            notes.append(
                                f"batch rerank failed for batch "
                                f"{batch_start}-{batch_start + len(batch_hits)}: {exc}"
                            )
                            scores_map = {lid: 0 for lid, _ in docs}
                        rerank_call_ms.append(int((time.time() - call_t0) * 1000))
                        # Map local ids → global hit idx → rerank score.
                        for local_id, (idx, _bm25_score) in enumerate(batch_hits):
                            rerank_scores_by_idx[idx] = scores_map.get(local_id, 0)
                    # Materialize the reranked tuples in original hit
                    # order; sort below normalizes.
                    reranked = [
                        (idx, score, rerank_scores_by_idx.get(idx, 0))
                        for idx, score in hits
                    ]
                else:
                    # ── Per-call mode ───────────────────────────────
                    for idx, score in hits:
                        rid = _doc_ids[idx]
                        doc = _corpus_by_id[rid]
                        # Mirror SIRA's batch reranker — title + body, capped at 4000 chars.
                        doc_text = (f"{doc['title']}\n\n{doc['text']}")[:4000]
                        prompt = _rerank_prompt_template.format(
                            query=req.query, document=doc_text,
                        )
                        call_t0 = time.time()
                        try:
                            raw = await _llm_call(
                                client, prompt,
                                max_tokens=_RERANK_MAX_TOKENS, temperature=0.0,
                                # Route rerank to the rerank-specific
                                # endpoint when configured; otherwise
                                # falls through to the shim (which is what
                                # query enrichment already uses).
                                base_url=_RERANK_LLM_URL or None,
                                model=_RERANK_LLM_MODEL or None,
                                api_key=_RERANK_LLM_API_KEY or None,
                            )
                            rerank_score = _parse_score(raw)
                        except Exception as exc:
                            notes.append(f"rerank failed for {rid}: {exc}")
                            rerank_score = 0
                        rerank_call_ms.append(int((time.time() - call_t0) * 1000))
                        reranked.append((idx, score, rerank_score))
                # Sort by rerank score desc, then BM25 desc as tiebreaker.
                reranked.sort(key=lambda x: (-x[2], -x[1]))
            except Exception as exc:
                notes.append(f"rerank stage aborted: {exc}")
                reranked = [(idx, score, 0) for idx, score in hits]
        else:
            if not _RERANK_ENABLED:
                notes.append(
                    "rerank stage disabled via NORA_SIRA_RERANK_ENABLED=false "
                    "— results are BM25-with-expansion only (no LLM rerank)"
                )
            elif not _rerank_prompt_template:
                notes.append("rerank prompt missing — results are BM25-with-expansion only")
            reranked = [(idx, score, 0) for idx, score in hits]
        timings["rerank_ms"] = int((time.time() - t0) * 1000)

    # Compute rerank-call statistics for instrumentation surface.
    def _pct(sorted_xs: list[int], p: float) -> int:
        if not sorted_xs:
            return 0
        k = max(0, min(len(sorted_xs) - 1, int(round(p * (len(sorted_xs) - 1)))))
        return sorted_xs[k]

    rerank_call_stats: dict[str, Any] = {}
    if rerank_call_ms:
        sorted_ms = sorted(rerank_call_ms)
        rerank_call_stats = {
            "count": len(sorted_ms),
            "total_ms": sum(sorted_ms),
            "min_ms": sorted_ms[0],
            "max_ms": sorted_ms[-1],
            "mean_ms": int(sum(sorted_ms) / len(sorted_ms)),
            "p50_ms": _pct(sorted_ms, 0.50),
            "p95_ms": _pct(sorted_ms, 0.95),
            # Full ordered list — query-order, not sorted — so a user
            # can spot if e.g. the first 3 calls were slow then it
            # smoothed out (cold start) or if there's a degraded tail.
            "call_ms": rerank_call_ms,
        }
        logger.info(
            "sira-query rerank: count=%d total=%dms mean=%dms p50=%dms p95=%dms max=%dms",
            len(sorted_ms), sum(sorted_ms),
            rerank_call_stats["mean_ms"],
            rerank_call_stats["p50_ms"],
            rerank_call_stats["p95_ms"],
            sorted_ms[-1],
        )

    # Fan-out doc/section pointer rows into req-level chunks
    # (plan-aware-sira). Direct results stay in place; children are
    # inserted right after their parent. Disabled / no pointer rows
    # in top-K → no-op.
    fanned = _fanout_reranked(reranked)

    # Format final response. top_k is applied to the fanned-out list,
    # so a single doc/section hit can expand top_k's effective coverage
    # by up to _FANOUT_PER_HIT children — the synthesizer ends up with
    # actual req-level content for plan-summarize queries.
    out: list[dict[str, Any]] = []
    for rank, (idx, bm25_score, rerank_score, source) in enumerate(
        fanned[:top_k], 1,
    ):
        rid = _doc_ids[idx]
        doc = _corpus_by_id[rid]
        text_preview = doc["text"].replace("\n", " ").strip()[:400]
        out.append({
            "rank": rank,
            "req_id": rid,
            "rerank_score": rerank_score,
            "bm25_score": round(float(bm25_score), 4),
            "title": doc["title"],
            "text_preview": text_preview,
            "source": source,
        })

    return {
        "query": req.query,
        "top_k": top_k,
        "candidates_reranked": len(reranked),
        "expansion_phrases_kept": kept_phrases,
        "results": out,
        "timings_ms": timings,
        "rerank_call_stats": rerank_call_stats,
        "notes": notes,
    }
