"""Test page — multi-section playground for free-form requirement
queries with thumbs-up/down feedback capture.

Currently only the `requirement_bot` section is functional; other
section IDs are placeholders rendered as "Coming soon" in the
template. Each Q&A pair is logged to `<env_dir>/state/
nora_test_feedback.db` via `FeedbackStore`; the user's later
thumbs-up/down + free-form feedback updates the same row.

The Requirement Bot reuses the production query path
(`_run_query_sync` in routes/query.py) so the test page exercises
the same retrieval/synthesis stack as `/query`. The test page
adds: per-Q&A persistence + a feedback widget. It does NOT add
a job queue — calls block synchronously until the answer arrives,
which is the expected UX for a "ask + read + vote" interaction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from core.src.web.feedback_db import CATEGORIES

logger = logging.getLogger(__name__)

router = APIRouter()


# URL of the SIRA per-query probe service (sandbox/sira_query/service.py).
# Defaults assume the service is started locally on port 8040; in
# production deployments this typically points at a same-host service
# the operator started alongside NORA's web app. See sandbox/SETUP.md
# "Per-query SIRA probe" for the full launch procedure.
_SIRA_QUERY_URL = os.getenv(
    "NORA_SIRA_QUERY_URL", "http://127.0.0.1:8040",
).rstrip("/")

# Timeout matches the SIRA service's own LLM call timeout (300s per
# llm_call) plus rerank-stage budget. At concurrency=1 + ~36s/call on
# a proxy-throttled LLM, top_n=20 reranks → ~12 min worst case for a
# single query. Set generously.
_SIRA_QUERY_TIMEOUT = float(os.getenv("NORA_SIRA_QUERY_TIMEOUT", "1200"))

# Score-based filter: which SIRA-ranked chunks should be pinned to
# NORA's synthesizer. A chunk must pass BOTH gates:
#
#   * Absolute floor: rerank_score >= NORA_SIRA_PIN_MIN_SCORE.
#     Anchored to the reranker prompt's score guide — score 21-40 is
#     "discusses related concepts," 41+ is "partial answer or better."
#     The default 30 sits in that range. With our observed bucketed
#     distribution (0/20/40/60/80 clusters), this drops everything ≤20
#     ("peripherally related but no answer").
#
#   * Relative threshold: rerank_score >= max_score × NORA_SIRA_PIN_REL_THRESHOLD.
#     Adapts to query difficulty: if the best chunk only scored 30,
#     pin chunks ≥15 (don't strip everything). Default 0.5 keeps
#     chunks at least half as relevant as the top.
#
# Set NORA_SIRA_PIN_MIN_SCORE=0 + NORA_SIRA_PIN_REL_THRESHOLD=0.0 to
# disable filtering (legacy behavior — pins all top_k chunks).
_PIN_MIN_SCORE = float(os.getenv("NORA_SIRA_PIN_MIN_SCORE", "30"))
_PIN_REL_THRESHOLD = float(os.getenv("NORA_SIRA_PIN_REL_THRESHOLD", "0.5"))

# Pin-selection mode (multi-mno-nora D-DRAFT-16). For multi-MNO/multi-release
# queries, the score-based filter above sorts the merged pool by absolute rerank
# score and the highest-scoring cell can take every slot — chunk-granularity
# asymmetry across corpora makes cross-cell scores unfairly favour one MNO.
#   * "rerank-topk" (default): the score-filter above (single-corpus-friendly).
#   * "balanced": round-robin across (mno, release) cells up to NORA_SIRA_PIN_MAX,
#     guaranteeing every resolved cell is represented; the synthesizer then does
#     the final relevance judgment over the balanced set (robust to the chunking
#     asymmetry the cross-encoder is not). Sized to fit the 32K synth context.
_PIN_MODE = os.getenv("NORA_SIRA_PIN_MODE", "rerank-topk").strip().lower()
_PIN_MAX = int(os.getenv("NORA_SIRA_PIN_MAX", "16"))


# ── merged-tab helpers (team-eval-pilot) ──────────────────────────────
#
# Three small helpers feeding the per-(question x lane) row that the
# merged tab writes to test_feedback: cited_ids (from the synthesizer's
# explicit citations) + lane_config snapshot (NORA env-flags / SIRA
# /healthz). Kept inline here for now; if the snapshot logic grows, factor
# to playground/snapshot.py per the strand's deferred items.

# Subset of /healthz fields stored as the SIRA lane_config snapshot. Keys
# absent from the response are skipped (the snapshot grows as the service
# adds knobs). The pin filters (PIN_MIN_SCORE / PIN_REL_THRESHOLD) are
# NORA-side, so they live in NORA's snapshot, not here.
_SIRA_HEALTHZ_SNAPSHOT_KEYS: tuple[str, ...] = (
    "corpus_size",
    "rerank_enabled",
    "query_enrich_enabled",
    "query_enrich_temperature",
    "fanout_enabled",
    "fanout_per_hit",
    "expansion_weight",
    "max_df_ratio",
    "default_top_k",
    "doc_enrich_run_pinned",
    "query_enrich_run_pinned",
    "rerank_run_pinned",
)


def _flatten_cited_ids(citations: list[Any] | None) -> list[str]:
    """Extract req_ids from a synthesizer citation list (the *explicit*
    LLM-cited subset, not the fallback combined list). Deduped + sorted
    so analysis SQL can treat `cited_ids` as a stable set.

    Tolerant of None / non-dict items / missing `req_id` keys — a
    malformed citation drops out rather than blowing up the Ask.
    """
    if not citations:
        return []
    seen: set[str] = set()
    for c in citations:
        if not isinstance(c, dict):
            continue
        rid = c.get("req_id")
        if rid and isinstance(rid, str):
            seen.add(rid)
    return sorted(seen)


def _pick_sira_snapshot(healthz_body: dict[str, Any]) -> dict[str, Any]:
    """Subset a /healthz response to the keys that affect retrieval
    reproducibility for the lane_config snapshot. Pure / network-free
    so it's directly unit-testable."""
    return {
        k: healthz_body[k]
        for k in _SIRA_HEALTHZ_SNAPSHOT_KEYS
        if k in healthz_body
    }


async def _snapshot_sira_lane_config() -> dict[str, Any]:
    """Fetch the per-query SIRA service's /healthz and apply
    `_pick_sira_snapshot`. Best-effort: returns `{"_error": "..."}` on
    network/parse failure so a snapshot hiccup never blocks the Ask
    flow (the row still gets written; analysis can spot the error
    later)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_SIRA_QUERY_URL}/healthz")
    except Exception as exc:
        return {"_error": f"healthz fetch failed: {exc}"[:200]}
    if resp.status_code != 200:
        return {"_error": f"healthz status {resp.status_code}"}
    try:
        return _pick_sira_snapshot(resp.json())
    except Exception as exc:
        return {"_error": f"healthz parse failed: {exc}"[:200]}


def _snapshot_nora_lane_config(result: dict[str, Any]) -> dict[str, Any]:
    """Compose NORA's lane_config snapshot from what the query pipeline
    returned for this request plus the few env knobs that shape it.
    Pure / synchronous; takes the result dict produced by
    `_run_query_for_test` so callers don't re-run the pipeline.

    Intentionally minimal for the pilot — extend as analyses identify
    which knobs are worth tracking on which questions.
    """
    snap: dict[str, Any] = {
        "llm_model": result.get("llm_model"),
        "query_intent": result.get("query_intent"),
        "candidate_count": result.get("candidate_count"),
    }
    for env in (
        "NORA_LLM_MODEL",
        "NORA_QUERY_RERANK_ENABLED",
        "NORA_QUERY_BROAD_TOP_K",
        "NORA_QUERY_NARROW_TOP_K",
        "NORA_INCLUDE_PARENT_BODY",
    ):
        v = os.getenv(env)
        if v is not None:
            snap[env] = v
    return snap


def _render_template_to_string(
    request: Request, name: str, context: dict[str, Any],
) -> str:
    """Render a Jinja template to a string (not a Response). Used to
    pre-render each lane's `_answer.html` fragment for the merged tab
    so both lanes compose into one response. Mirrors `_template_response`'s
    `root_path` injection so per-lane fragments and the outer container
    render identical URLs."""
    from core.src.web.app import config, templates
    ctx: dict[str, Any] = {"request": request, "root_path": config.root_path}
    ctx.update(context)
    return templates.get_template(name).render(ctx)


async def _run_nora_lane_for_merged(
    question: str, request: Request,
    *,
    emit_progress: "Callable[[str], Awaitable[None]] | None" = None,
) -> dict[str, Any]:
    """Run NORA's hybrid pipeline for the merged tab. Returns a
    standardized dict the merged branch consumes:

      {"error": "..."}                       — on failure
      {"result": ..., "elapsed_ms": ...,     — on success
       "retrieved_ids": [...], "reranked_ids": None,
       "cited_ids": [...], "lane_config": {...}}

    `reranked_ids` is always None for NORA — the hybrid pipeline doesn't
    expose a separate rerank step at this granularity.

    `emit_progress` is an optional async callback for streaming progress
    updates to the SSE endpoint. NORA's inner pipeline runs as a single
    `_run_query_for_test` blocking call — meaningful granularity below
    "start / done" requires restructuring QueryPipeline.query() to yield
    stage events, which is out of scope here.
    """
    async def _say(msg: str) -> None:
        if emit_progress is not None:
            await emit_progress(msg)

    await _say("Running NORA hybrid pipeline (retrieve + rerank + synthesize)…")
    start = time.time()
    try:
        result = await asyncio.to_thread(_run_query_for_test, question, request.app)
    except Exception as exc:
        logger.exception("NORA lane failed in merged tab")
        await _say(f"NORA error: {exc}")
        return {"error": f"NORA query failed: {exc}"}
    elapsed_ms = int((time.time() - start) * 1000)
    if "error" in result:
        await _say(f"NORA error: {result['error']}")
        return {"error": result["error"], "elapsed_ms": elapsed_ms}

    rag_chunks = result.get("rag_chunks") or []
    retrieved_ids = sorted({
        c.get("req_id") for c in rag_chunks if c.get("req_id")
    })
    await _say(
        f"NORA: {len(retrieved_ids)} chunks retrieved, answer ready ({elapsed_ms} ms)"
    )
    return {
        "result": result,
        "elapsed_ms": elapsed_ms,
        "retrieved_ids": list(retrieved_ids),
        "reranked_ids": None,
        "cited_ids": _flatten_cited_ids(result.get("llm_citations") or []),
        "lane_config": _snapshot_nora_lane_config(result),
    }


async def _run_sira_lane_for_merged(
    question: str, request: Request,
    *,
    emit_progress: "Callable[[str], Awaitable[None]] | None" = None,
) -> dict[str, Any]:
    """Run SIRA's BM25→rerank pipeline + NORA's synthesizer pinned to
    the SIRA top results, for the merged tab. Returns a standardized
    dict — same error shape as the NORA runner, plus SIRA-specific
    extras (sira_results, max_rerank_score, …) the template's SIRA
    preamble in `_answer.html` needs.

    `emit_progress` is an optional async callback for streaming progress
    updates. Multi-step shape — SIRA call → pin filter → synthesizer —
    gives several natural boundaries to surface live.
    """
    async def _say(msg: str) -> None:
        if emit_progress is not None:
            await emit_progress(msg)

    await _say("Calling SIRA service for retrieval (BM25 + LLM rerank)…")
    start = time.time()
    try:
        sira_result = await _call_sira_query(question)
    except Exception as exc:
        logger.exception("SIRA service call failed in merged tab")
        await _say(f"SIRA service error: {exc}")
        return {"error": f"SIRA service call failed: {exc}"}

    sira_results = sira_result.get("results", []) or []
    pinned_results, max_rerank_score = _select_pinned_chunks(sira_results)
    pinned_req_ids = {r["req_id"] for r in pinned_results if r.get("req_id")}
    for r in sira_results:
        r["pinned"] = r.get("req_id") in pinned_req_ids
    pinned_chunk_ids = [f"req:{rid}" for rid in pinned_req_ids]
    await _say(
        f"SIRA: {len(sira_results)} candidates reranked, "
        f"{len(pinned_chunk_ids)} pinned to synthesizer"
    )

    synth_result: dict[str, Any] = {}
    synth_error: str | None = None
    if pinned_chunk_ids:
        await _say(
            f"Running NORA synthesizer on {len(pinned_chunk_ids)} "
            f"SIRA-pinned chunks…"
        )
        synth_start = time.time()
        try:
            synth_result = await asyncio.to_thread(
                _run_query_for_test, question, request.app, pinned_chunk_ids,
            )
            # Surface NORA-synthesizer latency alongside SIRA's retrieval
            # timings (expand/search/rerank) so the test page shows the full
            # expand·search·rerank·synth chain, not just the retrieval lane.
            synth_ms = int((time.time() - synth_start) * 1000)
            timings = sira_result.setdefault("timings_ms", {})
            if isinstance(timings, dict):
                timings["synth_ms"] = synth_ms
            if "error" in synth_result:
                synth_error = synth_result["error"]
                await _say(f"SIRA synthesizer error: {synth_error}")
        except Exception as exc:
            logger.exception("SIRA-driven synthesizer call failed in merged tab")
            synth_error = f"Synthesizer failed: {exc}"
            await _say(f"SIRA synthesizer error: {exc}")
    else:
        await _say("SIRA: no chunks passed the pin filter — skipping synthesis")
    elapsed_ms = int((time.time() - start) * 1000)
    if not synth_error and pinned_chunk_ids:
        await _say(f"SIRA: answer ready ({elapsed_ms} ms)")

    retrieved_ids = [r["req_id"] for r in sira_results if r.get("req_id")]
    lane_config = await _snapshot_sira_lane_config()
    # SIRA's `results` are already in rerank-score order when rerank is on
    # (per service.py:832). When rerank is off they're in BM25 order and
    # there's no separate reranked view, so log None.
    reranked_ids = retrieved_ids if lane_config.get("rerank_enabled") else None

    return {
        "result": synth_result,
        "sira_result": sira_result,
        "sira_results": sira_results,
        "max_rerank_score": max_rerank_score,
        "pinned_count": len(pinned_req_ids),
        "synth_error": synth_error,
        "elapsed_ms": elapsed_ms,
        "retrieved_ids": retrieved_ids,
        "reranked_ids": reranked_ids,
        "cited_ids": _flatten_cited_ids(
            (synth_result.get("llm_citations") or []) if synth_result else []
        ),
        "lane_config": lane_config,
    }


async def _build_merged_response_html(
    request: Request,
    question: str,
    user_name: str | None,
    outputs: dict[str, dict[str, Any]],
) -> str:
    """Post-process lane outputs and render the merged container to a
    string. Shared between `/api/test/ask` (HTML response) and
    `/api/test/ask-stream` (final SSE event payload). Handles per-lane
    `test_feedback` row insertion, per-lane context building, per-lane
    `_answer.html` pre-rendering, and the outer two-column container."""
    feedback_store = request.app.state.feedback_store
    lanes_html: dict[str, str] = {}

    for lane, out in outputs.items():
        if "error" in out:
            lanes_html[lane] = (
                f'<div class="alert alert-danger mb-0">'
                f'<strong>{lane.upper()} error:</strong> {out["error"]}'
                f'</div>'
            )
            continue

        result = out["result"] or {}
        answer_text = result.get("answer", "")

        row_id = None
        try:
            row_id = await feedback_store.record_qa(
                section="merged",
                question=question,
                answer=answer_text,
                citations=result.get("citations", []),
                query_elapsed_ms=out["elapsed_ms"],
                llm_model=result.get("llm_model"),
                metadata={},
                lane=lane,
                user_name=user_name,
                retrieved_ids=out["retrieved_ids"],
                reranked_ids=out["reranked_ids"],
                cited_ids=out["cited_ids"],
                lane_config=out["lane_config"],
            )
        except Exception as exc:
            logger.warning(
                "FeedbackStore.record_qa failed for %s row: %s", lane, exc,
            )

        ctx: dict[str, Any] = {
            "row_id": row_id,
            "question": question,
            "lane": lane,
            "feedback_mode": "merged",
            "user_name": user_name,
            "categories": CATEGORIES,
            "answer": answer_text,
            "citations": result.get("citations", []),
            "llm_citations": result.get("llm_citations", []),
            "rag_chunks": result.get("rag_chunks", []),
            "rag_chunk_count": result.get("rag_chunk_count", 0),
            "candidate_count": result.get("candidate_count"),
            "llm_model": result.get("llm_model"),
            "elapsed_ms": out["elapsed_ms"],
            "citation_audit": result.get("citation_audit"),
            "llm_system_prompt": result.get("llm_system_prompt", ""),
            "llm_context_text": result.get("llm_context_text", ""),
            "query_intent": result.get("query_intent"),
            "graph_candidates": result.get("graph_candidates"),
        }
        if lane == "sira":
            ctx.update({
                "sira_results": out["sira_results"],
                "sira_candidates_reranked": out["sira_result"].get("candidates_reranked", 0),
                "sira_top_k": out["sira_result"].get("top_k", 0),
                "sira_pinned_count": out["pinned_count"],
                "sira_max_rerank_score": out["max_rerank_score"],
                "sira_pin_min_score": _PIN_MIN_SCORE,
                "sira_pin_rel_threshold": _PIN_REL_THRESHOLD,
                "sira_pin_mode": _PIN_MODE,
                "sira_pin_max": _PIN_MAX,
                "sira_timings_ms": out["sira_result"].get("timings_ms"),
                "sira_rerank_call_stats": out["sira_result"].get("rerank_call_stats"),
                "sira_notes": out["sira_result"].get("notes", []),
                # Multi-MNO surfacing (FR-multi-5). Present only when the
                # SIRA service ran in multi-cell mode; None on the legacy
                # single-dataset path.
                "sira_mode": out["sira_result"].get("mode"),
                "sira_resolved_cells": out["sira_result"].get("resolved_cells"),
                "sira_unresolved": out["sira_result"].get("unresolved"),
                "synth_error": out["synth_error"],
                "candidate_count": 0,
            })

        lanes_html[lane] = _render_template_to_string(
            request, "test/_answer.html", ctx,
        )

    return _render_template_to_string(
        request, "test/_merged_answer.html", {
            "question": question,
            "user_name": user_name,
            "lanes_html": lanes_html,
        },
    )


def _select_pinned_chunks(
    sira_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Apply the score-based filter to SIRA's ranked results.
    Returns (pinned_results, max_rerank_score).

    When max_rerank_score is 0, the rerank stage was disabled (via
    NORA_SIRA_RERANK_ENABLED=false) OR every candidate scored 0. In
    both cases the score-based filter has no signal to act on, so
    we pin all results trusting whatever ordering came out of the
    upstream stages (BM25-with-expansion). The user is opted into
    "no rerank" by toggling the env var; bypassing the filter is the
    consistent behavior.
    """
    if not sira_results:
        return [], 0
    max_score = max(int(r.get("rerank_score", 0) or 0) for r in sira_results)
    if _PIN_MODE == "balanced":
        # D-DRAFT-16: ignore the global score sort; round-robin across cells so
        # both MNOs are pinned regardless of which corpus the reranker favoured.
        # max_score is still surfaced for the template's filter caption.
        return _balanced_pin(sira_results, _PIN_MAX), max_score
    if max_score == 0:
        # Rerank disabled or universally-zero: pin everything as-is.
        return list(sira_results), 0
    rel_floor = max_score * _PIN_REL_THRESHOLD
    pinned = [
        r for r in sira_results
        if int(r.get("rerank_score", 0) or 0) >= _PIN_MIN_SCORE
        and int(r.get("rerank_score", 0) or 0) >= rel_floor
    ]
    return pinned, max_score


def _balanced_pin(
    sira_results: list[dict[str, Any]], limit: int,
) -> list[dict[str, Any]]:
    """Round-robin across (mno, release) cells, capped at `limit`.

    SIRA returns the merged pool already in rerank order, so each cell's
    slice is in-cell rerank order; we interleave the slices (cell-0 rank-0,
    cell-1 rank-0, …) so every resolved cell gets fair representation in the
    pinned set rather than the top-scoring cell taking every slot. Cell
    insertion order is preserved (first appearance in `sira_results`).
    """
    by_cell: "dict[tuple[str, str], list[dict[str, Any]]]" = {}
    for r in sira_results:
        by_cell.setdefault((r.get("mno", ""), r.get("release", "")), []).append(r)
    out: list[dict[str, Any]] = []
    rank = 0
    width = max((len(v) for v in by_cell.values()), default=0)
    while rank < width and len(out) < limit:
        for cell_list in by_cell.values():
            if rank < len(cell_list):
                out.append(cell_list[rank])
                if len(out) >= limit:
                    break
        rank += 1
    return out


async def _call_sira_query(question: str, top_k: int | None = None) -> dict[str, Any]:
    """POST the question to the SIRA per-query probe service and
    return its JSON response.

    Errors are surfaced verbatim — caller renders them in the answer
    template as an error block.
    """
    payload: dict[str, Any] = {"query": question}
    if top_k:
        payload["top_k"] = top_k
    async with httpx.AsyncClient(timeout=_SIRA_QUERY_TIMEOUT) as client:
        resp = await client.post(
            f"{_SIRA_QUERY_URL}/sira-query", json=payload,
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"SIRA service returned {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _corpus_label() -> str:
    """Best-effort short label for the corpus the web UI is bound to.

    Reads the active ``EnvironmentConfig.mnos`` + ``.releases`` lists
    when an env config can be located for the configured ``env_dir``;
    otherwise returns ``"the indexed"`` so the blurb stays grammatical.

    Format:
      * Single MNO + single release: ``"VZW Feb2026"``.
      * Multi-MNO or multi-release: ``"<N MNOs × M releases>"``.
      * Unknown: ``"the indexed"``.
    """
    try:
        from core.src.web.routes.query import _find_env_config_for_web
        env_cfg = _find_env_config_for_web()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("corpus label: env-config lookup failed (%s)", e)
        return "the indexed"
    if env_cfg is None:
        return "the indexed"
    mnos = [m for m in (env_cfg.mnos or []) if m]
    releases = [r for r in (env_cfg.releases or []) if r]
    if len(mnos) == 1 and len(releases) == 1:
        return f"{mnos[0]} {releases[0]}"
    if mnos and releases:
        return f"{len(mnos)} MNOs × {len(releases)} releases"
    return "the indexed"


def _build_sections() -> list[dict[str, Any]]:
    """Build the per-request section registry with a corpus-aware blurb.

    Sections are otherwise static (handlers wired in ``_run_section()``);
    only the ``requirement_bot`` blurb is dynamic so the Test page
    reflects what's actually ingested on-prem instead of a hardcoded
    corpus name.
    """
    label = _corpus_label()
    return [
        {
            "id": "requirement_bot",
            "label": "Requirement Bot",
            "enabled": True,
            "blurb": (
                f"Ask any free-form question about the {label} "
                "requirements corpus. The answer is synthesized from "
                "retrieved chunks; rate it below to log feedback for "
                "offline review."
            ),
        },
        {
            "id": "compliance_check",
            "label": "Compliance Check",
            "enabled": False,
            "blurb": "Single-requirement compliance against device specs.",
        },
        {
            "id": "cross_mno_compare",
            "label": "Cross-MNO Compare",
            "enabled": False,
            "blurb": "Compare requirement coverage across operators.",
        },
        {
            "id": "standards_lookup",
            "label": "Standards Lookup",
            "enabled": False,
            "blurb": "Look up 3GPP TS section text by spec + section.",
        },
        {
            "id": "sira_retrieval",
            "label": "SIRA Retrieval",
            "enabled": True,
            "blurb": (
                "Probe SIRA's per-query retrieval (BM25 + query enrichment "
                "+ LLM rerank) against the same corpus. Returns ranked req_ids "
                "with text previews — the synthesizer stage is skipped. "
                "Requires the SIRA query service running locally (see "
                "sandbox/SETUP.md \"Per-query SIRA probe\")."
            ),
        },
    ]


# Section IDs are static — only the blurb on requirement_bot is dynamic;
# this constant survives only as a quick id-validity check.
_SECTION_IDS: set[str] = {
    "requirement_bot",
    "compliance_check",
    "cross_mno_compare",
    "standards_lookup",
    "sira_retrieval",
}


# -- Pages ------------------------------------------------------------------


@router.get("/test", response_class=HTMLResponse)
async def playground_page(request: Request, section: str = "requirement_bot"):
    from core.src.web.app import _template_response

    # Default to first enabled section if user passed an unknown id
    active_section = section if section in _SECTION_IDS else "requirement_bot"

    return _template_response(request, "test/index.html", {
        "sections": _build_sections(),
        "active_section": active_section,
    })


# -- API: ask + feedback ----------------------------------------------------


@router.post("/api/test/ask", response_class=HTMLResponse)
async def playground_ask(request: Request):
    """Submit a question, run the query pipeline, log the Q&A row,
    return rendered answer + citations + feedback widget seeded with
    the row id."""
    from core.src.web.app import _template_response

    form = await request.form()
    question = (form.get("question") or "").strip()
    section = (form.get("section") or "requirement_bot").strip()

    if not question:
        return _template_response(request, "test/_answer.html", {
            "error": "Question is required.",
        })

    # ── Merged tab (team-eval-pilot) ─────────────────────────────────
    # New default UX: one form, two lane checkboxes (NORA + SIRA), one
    # row per (question x lane) inserted into test_feedback, two-column
    # side-by-side response. Legacy section-tab paths (requirement_bot /
    # sira_retrieval) are kept below for back-compat and direct POSTs.
    if section == "merged":
        lanes_checked = [l for l in form.getlist("lanes") if l in ("nora", "sira")]
        user_name = (form.get("user_name") or "").strip() or None
        if not lanes_checked:
            return _template_response(request, "test/_answer.html", {
                "error": "Please select at least one retrieval lane (NORA or SIRA).",
            })

        # Run enabled lanes in parallel. Each runner is fault-isolated;
        # one lane failing does not block the other from rendering.
        runners: dict[str, Any] = {}
        if "nora" in lanes_checked:
            runners["nora"] = _run_nora_lane_for_merged(question, request)
        if "sira" in lanes_checked:
            runners["sira"] = _run_sira_lane_for_merged(question, request)
        outputs = dict(zip(runners.keys(),
                           await asyncio.gather(*runners.values(),
                                                return_exceptions=False)))
        html = await _build_merged_response_html(
            request, question, user_name, outputs,
        )
        return HTMLResponse(content=html)

    # Hand off to the section's runner. requirement_bot → NORA's full
    # pipeline; sira_retrieval → SIRA service via HTTP proxy. The other
    # placeholder sections remain tab-disabled.
    if section == "sira_retrieval":
        # Two-step flow:
        #   1. Call the SIRA service → get ranked req_ids
        #   2. Pin NORA's synthesizer to those chunks → get an answer
        # The synthesizer is shared with the requirement_bot tab, so
        # this is apples-to-apples: SAME synthesizer, only the retrieval
        # lane (NORA hybrid vs. SIRA BM25+enrich+rerank) differs.
        start = time.time()
        try:
            sira_result = await _call_sira_query(question)
        except Exception as exc:
            logger.exception("SIRA query failed")
            return _template_response(request, "test/_answer.html", {
                "error": f"SIRA service call failed: {exc}",
                "section": section,
                "question": question,
            })

        # Apply the score-based filter (see _select_pinned_chunks docstring).
        # We display ALL ranked results in the template but only pin the
        # high-confidence ones for the synthesizer.
        sira_results = sira_result.get("results", [])
        pinned_results, max_rerank_score = _select_pinned_chunks(sira_results)
        pinned_req_ids = {r["req_id"] for r in pinned_results if r.get("req_id")}
        # Mark each ranked result as pinned/filtered for the template.
        for r in sira_results:
            r["pinned"] = r.get("req_id") in pinned_req_ids

        # Convert pinned req_ids → NORA's chunk_id format
        # (`req:{req_id}` per chunk_builder.py:144).
        pinned_chunk_ids = [f"req:{rid}" for rid in pinned_req_ids]

        synth_result: dict[str, Any] = {}
        synth_error: str | None = None
        if pinned_chunk_ids:
            try:
                synth_result = await asyncio.to_thread(
                    _run_query_for_test, question, request.app, pinned_chunk_ids,
                )
                if "error" in synth_result:
                    synth_error = synth_result["error"]
            except Exception as exc:
                logger.exception("SIRA-driven synthesizer call failed")
                synth_error = f"Synthesizer failed: {exc}"
        elapsed_ms = int((time.time() - start) * 1000)

        # Record feedback row for the SIRA-synthesized answer (same
        # FeedbackStore + same shape as requirement_bot, just tagged
        # with section="sira_retrieval").
        row_id = None
        if synth_result and not synth_error:
            try:
                feedback_store = request.app.state.feedback_store
                row_id = await feedback_store.record_qa(
                    section=section,
                    question=question,
                    answer=synth_result.get("answer", ""),
                    citations=synth_result.get("citations", []),
                    query_elapsed_ms=elapsed_ms,
                    llm_model=synth_result.get("llm_model"),
                    metadata={
                        "sira_pinned_chunks": len(pinned_chunk_ids),
                        "sira_candidates_reranked": sira_result.get("candidates_reranked", 0),
                    },
                )
            except Exception as exc:
                logger.warning("FeedbackStore.record_qa failed for SIRA row: %s", exc)

        return _template_response(request, "test/_answer.html", {
            "section": section,
            "question": question,
            "row_id": row_id,
            # SIRA-retrieval view
            "sira_results": sira_results,
            "sira_expansion_phrases": sira_result.get("expansion_phrases_kept", []),
            "sira_timings_ms": sira_result.get("timings_ms", {}),
            "sira_rerank_call_stats": sira_result.get("rerank_call_stats", {}),
            "sira_candidates_reranked": sira_result.get("candidates_reranked", 0),
            "sira_notes": sira_result.get("notes", []),
            "sira_top_k": sira_result.get("top_k"),
            "sira_pinned_count": len(pinned_chunk_ids),
            "sira_max_rerank_score": max_rerank_score,
            "sira_pin_min_score": _PIN_MIN_SCORE,
            "sira_pin_rel_threshold": _PIN_REL_THRESHOLD,
            "sira_pin_mode": _PIN_MODE,
            "sira_pin_max": _PIN_MAX,
            "elapsed_ms": elapsed_ms,
            # Synthesizer view — pass the SAME fields requirement_bot
            # passes, so the shared template renders citation audit /
            # LLM prompt / fragment view consistently across tabs.
            "answer": synth_result.get("answer", "") if synth_result else "",
            "citations": synth_result.get("citations", []) if synth_result else [],
            "llm_citations": synth_result.get("llm_citations", []) if synth_result else [],
            "rag_chunks": synth_result.get("rag_chunks", []) if synth_result else [],
            "rag_chunk_count": synth_result.get("rag_chunk_count", 0) if synth_result else 0,
            "citation_audit": synth_result.get("citation_audit") if synth_result else None,
            "llm_system_prompt": synth_result.get("llm_system_prompt", "") if synth_result else "",
            "llm_context_text": synth_result.get("llm_context_text", "") if synth_result else "",
            # query_intent / graph_candidates — the pinned-chunk pipeline
            # path skips Stage 1 (taxonomy) + Stage 3 (graph scoping)
            # entirely, so these are None for SIRA. Passing through what
            # the synthesizer returned (typically None) — the template's
            # `{% if query_intent %}` gates silently hide the panels.
            "query_intent": synth_result.get("query_intent") if synth_result else None,
            "graph_candidates": synth_result.get("graph_candidates") if synth_result else None,
            # candidate_count is the legacy "candidates from graph"
            # footer variable for the requirement_bot path. On SIRA we
            # already show "N pinned to synth" prominently in the
            # preamble, so set this to 0 to suppress the duplicate
            # (and misleadingly-labeled) footer text.
            "candidate_count": 0,
            "synth_error": synth_error,
        })

    if section != "requirement_bot":
        return _template_response(request, "test/_answer.html", {
            "error": f"Section '{section}' is not yet implemented.",
        })

    # Run the query (blocks the request — sync-style UX). Reuses the
    # existing /query path's pipeline construction.
    start = time.time()
    try:
        result = await asyncio.to_thread(_run_query_for_test, question, request.app)
    except Exception as e:
        logger.exception("Test query failed")
        return _template_response(request, "test/_answer.html", {
            "error": f"Query failed: {e}",
        })
    elapsed_ms = int((time.time() - start) * 1000)

    if "error" in result:
        return _template_response(request, "test/_answer.html", {
            "error": result["error"],
        })

    # Persist the Q&A row. The feedback widget renders below with the
    # returned row id; the user's vote later updates this row in
    # place.
    feedback_store = request.app.state.feedback_store
    row_id = await feedback_store.record_qa(
        section=section,
        question=question,
        answer=result.get("answer", ""),
        citations=result.get("citations", []),
        query_elapsed_ms=elapsed_ms,
        llm_model=result.get("llm_model"),
        metadata={"candidate_count": result.get("candidate_count")},
    )

    return _template_response(request, "test/_answer.html", {
        "row_id": row_id,
        "question": question,
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "llm_citations": result.get("llm_citations", []),
        "rag_chunks": result.get("rag_chunks", []),
        "rag_chunk_count": result.get("rag_chunk_count", 0),
        "elapsed_ms": elapsed_ms,
        "candidate_count": result.get("candidate_count"),
        "section": section,
        "disambiguation_required": result.get("disambiguation_required", False),
        "groups": result.get("groups", []),
        "llm_system_prompt": result.get("llm_system_prompt", ""),
        "llm_context_text": result.get("llm_context_text", ""),
        "citation_audit": result.get("citation_audit"),
        "query_intent": result.get("query_intent"),
        "graph_candidates": result.get("graph_candidates"),
    })


@router.post("/api/test/ask-stream")
async def playground_ask_stream(request: Request):
    """SSE streaming variant of /api/test/ask for the merged tab.

    Form fields: same as /api/test/ask (question, section='merged',
    lanes[], user_name).

    Yields three event types:
      - `event: progress` with `{lane, message}` — fired as each lane
        runner crosses a stage boundary (call SIRA / pin / synth / done).
        Used to update the per-lane spinner+label on the frontend.
      - heartbeat lines (`: heartbeat`) every ~2s when no progress event
        is pending, so proxies don't time out the streamed connection.
      - `event: done` with `{html}` — final payload carrying the
        rendered _merged_answer.html string. Frontend swaps it into
        #test-answer and hides the progress display.

    Only supports `section=merged`. Legacy section URLs continue to use
    /api/test/ask (no streaming).
    """
    form = await request.form()
    question = (form.get("question") or "").strip()
    section = (form.get("section") or "merged").strip()
    lanes_checked = [l for l in form.getlist("lanes") if l in ("nora", "sira")]
    user_name = (form.get("user_name") or "").strip() or None

    if section != "merged":
        return JSONResponse(
            {"error": "ask-stream only supports section=merged; "
                      "legacy tabs use /api/test/ask"},
            status_code=400,
        )
    if not question:
        return JSONResponse(
            {"error": "Question is required."}, status_code=400,
        )
    if not lanes_checked:
        return JSONResponse(
            {"error": "Please select at least one retrieval lane "
                      "(NORA or SIRA)."},
            status_code=400,
        )

    progress_q: asyncio.Queue = asyncio.Queue()

    def _make_emitter(lane: str) -> Callable[[str], Awaitable[None]]:
        async def _emit(msg: str) -> None:
            await progress_q.put(
                {"type": "progress", "lane": lane, "message": msg}
            )
        return _emit

    runners: dict[str, Any] = {}
    if "nora" in lanes_checked:
        runners["nora"] = _run_nora_lane_for_merged(
            question, request, emit_progress=_make_emitter("nora"),
        )
    if "sira" in lanes_checked:
        runners["sira"] = _run_sira_lane_for_merged(
            question, request, emit_progress=_make_emitter("sira"),
        )

    async def event_stream():
        tasks = {
            lane: asyncio.create_task(coro)
            for lane, coro in runners.items()
        }

        # Drain progress queue + check for task completion. Heartbeats
        # at the queue's get-timeout keep proxies from closing the
        # connection during quiet stretches (NORA's blocking call can
        # last seconds without emitting anything).
        while not all(t.done() for t in tasks.values()):
            try:
                msg = await asyncio.wait_for(progress_q.get(), timeout=2.0)
                yield f"event: progress\ndata: {json.dumps(msg)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"

        # Drain any final progress events that may have been pushed
        # between the last loop iteration and task completion.
        while not progress_q.empty():
            msg = progress_q.get_nowait()
            yield f"event: progress\ndata: {json.dumps(msg)}\n\n"

        # All tasks done — collect results, build final HTML, emit done.
        outputs = {lane: t.result() for lane, t in tasks.items()}
        try:
            html = await _build_merged_response_html(
                request, question, user_name, outputs,
            )
        except Exception as exc:
            logger.exception("merged HTML rendering failed in stream endpoint")
            html = (
                f'<div class="alert alert-danger">'
                f'<strong>Render error:</strong> {exc}</div>'
            )
        yield f"event: done\ndata: {json.dumps({'html': html})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disable response buffering on nginx if present; SSE needs
            # the bytes to reach the client as they're yielded.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/test/synthesize-group", response_class=HTMLResponse)
async def playground_synthesize_group(request: Request):
    """Step 3c — user picked a group from a disambiguation response.

    Form fields:
      - `question`: original query (so the answer addresses it)
      - `chunk_ids`: comma-separated chunk_ids of the picked group
      - `section`: same as /api/test/ask, passed through

    Re-runs the query with `pinned_chunk_ids` set, which skips Stages
    2-4.7 and synthesizes only from those chunks.
    """
    from core.src.web.app import _template_response

    form = await request.form()
    question = (form.get("question") or "").strip()
    chunk_ids_raw = (form.get("chunk_ids") or "").strip()
    section = (form.get("section") or "requirement_bot").strip()

    if not question:
        return _template_response(request, "test/_answer.html", {
            "error": "Question is required.",
        })
    if not chunk_ids_raw:
        return _template_response(request, "test/_answer.html", {
            "error": "No chunk_ids provided. Pick a group first.",
        })

    chunk_ids = [c.strip() for c in chunk_ids_raw.split(",") if c.strip()]
    if not chunk_ids:
        return _template_response(request, "test/_answer.html", {
            "error": "chunk_ids empty after parse.",
        })

    start = time.time()
    try:
        result = await asyncio.to_thread(
            _run_query_for_test, question, request.app, chunk_ids,
        )
    except Exception as e:
        logger.exception("Synthesize-group query failed")
        return _template_response(request, "test/_answer.html", {
            "error": f"Query failed: {e}",
        })
    elapsed_ms = int((time.time() - start) * 1000)

    if "error" in result:
        return _template_response(request, "test/_answer.html", {
            "error": result["error"],
        })

    feedback_store = request.app.state.feedback_store
    row_id = await feedback_store.record_qa(
        section=section,
        question=question,
        answer=result.get("answer", ""),
        citations=result.get("citations", []),
        query_elapsed_ms=elapsed_ms,
        llm_model=result.get("llm_model"),
        metadata={
            "candidate_count": result.get("candidate_count"),
            "synthesize_group": True,
            "pinned_chunk_count": len(chunk_ids),
        },
    )

    return _template_response(request, "test/_answer.html", {
        "row_id": row_id,
        "question": question,
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "llm_citations": result.get("llm_citations", []),
        "rag_chunks": result.get("rag_chunks", []),
        "rag_chunk_count": result.get("rag_chunk_count", 0),
        "elapsed_ms": elapsed_ms,
        "candidate_count": result.get("candidate_count"),
        "section": section,
        # On the synthesis re-run, disambiguation cannot fire (we're
        # past it), so these are always defaults — pass them through
        # for template consistency.
        "disambiguation_required": False,
        "groups": [],
        "llm_system_prompt": result.get("llm_system_prompt", ""),
        "llm_context_text": result.get("llm_context_text", ""),
        "citation_audit": result.get("citation_audit"),
    })


@router.post("/api/test/feedback", response_class=HTMLResponse)
async def playground_feedback(
    request: Request,
    row_id: int = Form(...),
    # Legacy vote path fields
    vote: str = Form(""),
    free_form_feedback: str = Form(""),
    # Merged-tab fields — all optional, presence of user_score
    # dispatches to the merged-tab record_user_feedback path.
    user_score: str = Form(""),
    user_categories: list[str] = Form([]),
    comment: str = Form(""),
    user_name: str = Form(""),
):
    """Update an existing Q&A row with the user's feedback. Dispatches on
    which fields are present:
      - `user_score` set → merged-tab path (`record_user_feedback`)
      - otherwise        → legacy vote path (`record_feedback`)

    Returns a small confirmation HTML fragment for HTMX swap."""
    from core.src.web.app import _template_response
    feedback_store = request.app.state.feedback_store

    # ── Merged-tab dispatch ───────────────────────────────────────
    if user_score:
        try:
            score_i = int(user_score)
        except ValueError:
            return _template_response(request, "test/_feedback_ack.html", {
                "error": f"Invalid score: {user_score!r}", "row_id": row_id,
            })
        try:
            ok = await feedback_store.record_user_feedback(
                row_id=row_id,
                user_score=score_i,
                user_categories=user_categories or [],
                comment=(comment or "").strip() or None,
                user_name=(user_name or "").strip() or None,
            )
        except ValueError as exc:
            return _template_response(request, "test/_feedback_ack.html", {
                "error": str(exc), "row_id": row_id,
            })
        except Exception as exc:
            logger.exception("Merged-tab feedback persist failed")
            return _template_response(request, "test/_feedback_ack.html", {
                "error": f"Could not save feedback: {exc}", "row_id": row_id,
            })
        if not ok:
            return _template_response(request, "test/_feedback_ack.html", {
                "error": f"No feedback row with id={row_id}", "row_id": row_id,
            })
        return _template_response(request, "test/_feedback_ack.html", {
            "row_id": row_id, "user_score": score_i, "merged": True,
        })

    # ── Legacy vote path (unchanged behavior) ─────────────────────
    vote_clean = vote.strip().lower() or None
    if vote_clean not in ("up", "down", None):
        return _template_response(request, "test/_feedback_ack.html", {
            "error": f"Invalid vote: {vote!r}",
        })
    try:
        ok = await feedback_store.record_feedback(
            row_id=row_id,
            vote=vote_clean,
            free_form_feedback=(free_form_feedback or "").strip() or None,
        )
    except Exception as e:
        logger.exception("Feedback persist failed")
        return _template_response(request, "test/_feedback_ack.html", {
            "error": f"Could not save feedback: {e}",
        })
    if not ok:
        return _template_response(request, "test/_feedback_ack.html", {
            "error": f"No feedback row with id={row_id}",
        })
    return _template_response(request, "test/_feedback_ack.html", {
        "row_id": row_id,
        "vote": vote_clean,
    })


# -- Helpers ----------------------------------------------------------------


def _run_query_for_test(
    question: str,
    app=None,
    pinned_chunk_ids: list[str] | None = None,
) -> dict:
    """Adapt the existing /query pipeline runner into a dict shape
    the test page templates can consume directly. Re-imports the
    helper from the query module so we don't fork pipeline
    construction logic. `app` is passed through so the cached
    pipeline on `app.state` is reused across requests.

    Surfaces three citation views to the template:
      - `citations`: legacy combined list (LLM-cited + fallback)
      - `llm_citations`: subset cited explicitly in the answer text
      - `rag_chunks`: every chunk RAG returned (with text for the
        click-to-expand fragment view)

    Step 3c additions:
      - `disambiguation_required`, `groups` — surfaced when the pipeline
        short-circuits at Stage 4.7 with multiple plausible groups.
      - `pinned_chunk_ids` parameter — when set, the pipeline skips
        retrieval and synthesizes only from those chunks (used after
        the user picks a group from a disambiguation response).
    """
    from core.src.web.routes.query import _run_query_sync

    raw = _run_query_sync(question, app=app, pinned_chunk_ids=pinned_chunk_ids)
    if "error" in raw:
        return {"error": raw["error"]}

    # _run_query_sync may attach _llm_metrics; pop it (we don't
    # display it on the test page).
    raw.pop("_llm_metrics", None)

    return {
        "answer": raw.get("answer", ""),
        "citations": raw.get("citations", []) or [],
        "llm_citations": raw.get("llm_citations", []) or [],
        "rag_chunks": raw.get("rag_chunks", []) or [],
        "rag_chunk_count": raw.get("rag_chunk_count", 0),
        "candidate_count": raw.get("candidate_count"),
        "llm_model": raw.get("llm_model"),
        "disambiguation_required": raw.get("disambiguation_required", False),
        "groups": raw.get("groups", []),
        "llm_system_prompt": raw.get("llm_system_prompt", ""),
        "llm_context_text": raw.get("llm_context_text", ""),
        "citation_audit": raw.get("citation_audit"),
        "query_intent": raw.get("query_intent"),
        "graph_candidates": raw.get("graph_candidates"),
    }
