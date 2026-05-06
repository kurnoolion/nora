"""Query page and API routes."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import traceback
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.src.web.jobs import JobQueue

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

# Relevance threshold for the QueryPipeline's Stage-4.5 filter. Chunks
# with cosine distance above this value are dropped; if every chunk is
# dropped, the pipeline returns its "not found" answer instead of
# synthesizing from weak fragments.
#
# Default 0.5 was calibrated on the OA corpus + qwen3-embedding:4b-q8_0
# via tools/threshold_sweep — relevant queries scored 0.20-0.41,
# off-topic queries 0.74-0.77, leaving a comfortable 0.33 gap. Different
# embedding models produce different distance distributions, so this
# default may need re-tuning when the embedding model changes. Override
# at runtime via NORA_MAX_DISTANCE_THRESHOLD=<float>; set to "off" / ""
# to disable the filter entirely.
_DEFAULT_MAX_DISTANCE_THRESHOLD = 0.5
_MAX_DISTANCE_THRESHOLD_ENV_VAR = "NORA_MAX_DISTANCE_THRESHOLD"


def _resolve_max_distance_threshold() -> float | None:
    """Return the threshold to pass to QueryPipeline. None disables it."""
    import os
    raw = os.environ.get(_MAX_DISTANCE_THRESHOLD_ENV_VAR)
    if raw is None:
        return _DEFAULT_MAX_DISTANCE_THRESHOLD
    raw = raw.strip().lower()
    if raw in ("", "off", "none", "disable", "disabled"):
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not a valid float; using default %.2f",
            _MAX_DISTANCE_THRESHOLD_ENV_VAR, raw, _DEFAULT_MAX_DISTANCE_THRESHOLD,
        )
        return _DEFAULT_MAX_DISTANCE_THRESHOLD


def _graph_path() -> Path:
    """Resolve `<env_dir>/out/graph/knowledge_graph.json`. The Web UI
    is env_dir-bound (D-022); set `env_dir` in `config/web.json`."""
    from core.src.web.app import config
    return config.env_dir_path() / "out" / "graph" / "knowledge_graph.json"


def _vectorstore_dir() -> Path:
    """Resolve `<env_dir>/out/vectorstore/`."""
    from core.src.web.app import config
    return config.env_dir_path() / "out" / "vectorstore"


def _find_env_config_for_web():
    """Locate the env JSON whose `env_dir` matches the Web UI's
    configured env_dir. Returns an EnvironmentConfig or None if no
    match (env_dir unset, or no environments/*.json with that path).
    """
    from core.src.web.app import config as web_config
    if not web_config.env_dir:
        return None
    from core.src.env.config import EnvironmentConfig
    target = Path(web_config.env_dir).resolve()
    envs_dir = PROJECT_ROOT / "environments"
    if not envs_dir.exists():
        return None
    for json_path in sorted(envs_dir.glob("*.json")):
        try:
            env = EnvironmentConfig.load_json(json_path)
            if Path(env.env_dir).resolve() == target:
                return env
        except Exception as e:
            logger.debug("Skipping env file %s: %s", json_path, e)
    return None


def _build_llm_from_env_or_default():
    """Construct the LLM provider for /query and /test.

    Resolves provider / model / timeout via the unified D-044 chain:
    CLI flag (n/a here) > NORA_LLM_* env var > config/llm.json >
    EnvironmentConfig (legacy back-compat) > default. The
    OpenAI-compatible provider additionally reads NORA_LLM_BASE_URL /
    NORA_LLM_API_KEY directly at construction time.

    Reuses PipelineContext.create_llm_provider so the dispatch matches
    the eval pipeline exactly. Returns the provider, or a mock on
    failure (web path is non-fail-loud — falls back to mock so the
    UI keeps responding).
    """
    from core.src.env.config import (
        resolve_llm_provider, resolve_llm_model, resolve_llm_timeout,
    )
    from core.src.pipeline.runner import PipelineContext

    env_cfg = _find_env_config_for_web()
    provider = resolve_llm_provider(
        env_config_value=env_cfg.model_provider if env_cfg else None,
    )
    model = resolve_llm_model(
        env_config_value=env_cfg.model_name if env_cfg else None,
    )
    timeout = resolve_llm_timeout(
        env_config_value=env_cfg.model_timeout if env_cfg else None,
    )
    logger.info(
        "Web LLM resolved: provider=%s model=%s timeout=%ds",
        provider, model, timeout,
    )
    ctx = PipelineContext(
        documents_dir=Path("."),
        corrections_dir=None,
        eval_dir=None,
        verbose=False,
        model_provider=provider,
        model_name=model,
        model_timeout=timeout,
    )
    return ctx.create_llm_provider(require_real=False)

router = APIRouter()


# -- Pages ------------------------------------------------------------------

@router.get("/query", response_class=HTMLResponse)
async def query_page(request: Request):
    from core.src.web.app import _template_response

    graph_exists = _graph_path().exists()
    vs_config_path = _vectorstore_dir() / "config.json"
    vectorstore_exists = vs_config_path.exists()

    return _template_response(request, "query.html", {
        "graph_exists": graph_exists,
        "vectorstore_exists": vectorstore_exists,
    })


# -- API --------------------------------------------------------------------

@router.post("/api/query/ask")
async def submit_query(request: Request):
    job_queue: JobQueue = request.app.state.job_queue

    form = await request.form()
    query_text = form.get("query_text", "").strip()
    submitted_by = form.get("submitted_by", "").strip() or "anonymous"

    if not query_text:
        return JSONResponse({"error": "Query text is required."}, status_code=400)

    job = await job_queue.submit(
        job_type="query",
        submitted_by=submitted_by,
        query_text=query_text,
    )

    asyncio.create_task(
        run_query_background(job.id, query_text, job_queue, request.app)
    )

    return JSONResponse({"job_id": job.id})


@router.get("/api/query/{job_id}/result", response_class=HTMLResponse)
async def query_result(request: Request, job_id: str):
    from core.src.web.app import _template_response

    job_queue: JobQueue = request.app.state.job_queue
    job = await job_queue.get_meta(job_id)

    if job is None:
        return _template_response(request, "partials/query_result.html", {
            "status": "failed",
            "error_message": "Job not found.",
        })

    ctx = {
        "status": job.status,
        "error_message": job.error_message,
        "answer": None,
        "citations": [],
        "timing": None,
    }

    if job.status == "completed" and job.result_summary:
        try:
            result_data = json.loads(job.result_summary)
            ctx["answer"] = result_data.get("answer", "")
            ctx["citations"] = result_data.get("citations", [])
            ctx["timing"] = result_data.get("timing")
        except (json.JSONDecodeError, TypeError):
            ctx["answer"] = job.result_summary

    return _template_response(request, "partials/query_result.html", ctx)


# -- Background execution ---------------------------------------------------

class _PipelineBuildError(RuntimeError):
    """Raised by `_build_pipeline` when prerequisites aren't met
    (e.g. empty vectorstore). Caller surfaces the message to the UI."""


_pipeline_build_lock = threading.Lock()


def _build_pipeline(graph_path: Path, vectorstore_dir: Path):
    """Construct a QueryPipeline + LLM. Heavy: loads graph (~10MB),
    embedder model weights, opens Chroma, builds BM25 over the full
    chunk corpus. ~5-15s cold. Idempotent per env_dir; cache the
    result on `app.state`.

    RAG-only mode: when `graph_path` doesn't exist (graph stage was
    skipped via --rag-only / --skip-graph / config), a stub graph is
    built from the vectorstore's chunk metadata and the pipeline runs
    with `_bypass_graph=True`. Stage 3 then emits an empty
    CandidateSet so retrieval falls back to the metadata path."""
    from core.src.query.pipeline import (
        QueryPipeline,
        build_stub_graph_from_store,
        load_graph,
    )
    from core.src.vectorstore import make_embedder
    from core.src.vectorstore.config import VectorStoreConfig
    from core.src.vectorstore.store_chroma import ChromaDBStore

    vs_config_path = vectorstore_dir / "config.json"
    if vs_config_path.exists():
        vs_config = VectorStoreConfig.load_json(vs_config_path)
    else:
        vs_config = VectorStoreConfig(persist_directory=str(vectorstore_dir))

    # Use the provider factory so vectorstores built with Ollama
    # embeddings (e.g. qwen3-embedding:4b) load via OllamaEmbedder
    # rather than HuggingFace — HF rejects the `:` in model names
    # when prefixing them with "sentence-transformers/".
    embedder = make_embedder(vs_config)

    store = ChromaDBStore(
        persist_directory=vs_config.persist_directory,
        collection_name=vs_config.collection_name,
        distance_metric=vs_config.distance_metric,
    )

    if store.count == 0:
        raise _PipelineBuildError(
            "Vector store is empty. Run the vectorstore pipeline stage "
            "first (Pipeline page, or: "
            "python -m core.src.vectorstore.vectorstore_cli)."
        )

    # Graph: prefer the on-disk graph; fall back to a metadata-derived
    # stub when the graph stage was skipped (--rag-only / --skip-graph
    # / config). `_bypass_graph=True` makes Stage 3 emit empty
    # candidates so retrieval uses the metadata path. RAG-only mode is
    # also chosen when the graph file simply doesn't exist (pipeline
    # not yet run).
    if graph_path.exists():
        graph = load_graph(graph_path)
        rag_only = False
    else:
        logger.info(
            "Graph file %s missing — running in RAG-only mode "
            "(stub graph from vectorstore metadata).", graph_path,
        )
        graph = build_stub_graph_from_store(store)
        rag_only = True

    llm = _build_llm_from_env_or_default()
    synthesizer = None
    if llm is not None and not getattr(llm, "_is_mock", False):
        from core.src.query.synthesizer import LLMSynthesizer
        synthesizer = LLMSynthesizer(llm, max_tokens=30000 // 4)
    else:
        logger.info("No real LLM configured, falling back to mock synthesizer")

    threshold = _resolve_max_distance_threshold()
    if threshold is None:
        logger.info("Relevance threshold filter: DISABLED")
    else:
        logger.info("Relevance threshold filter: max_distance=%.3f", threshold)

    pipeline = QueryPipeline(
        graph=graph,
        embedder=embedder,
        store=store,
        synthesizer=synthesizer,
        top_k=10,
        max_context_chars=30000,
        max_distance_threshold=threshold,
    )
    if rag_only:
        pipeline._bypass_graph = True
    return pipeline, llm


def _get_or_build_pipeline(app, graph_path: Path, vectorstore_dir: Path):
    """Return (pipeline, llm) cached on `app.state`. First call pays
    the cold-start (~5-15s); subsequent calls are immediate.

    When `app` is None (e.g. in tests calling `_run_query_sync`
    directly) the cache is bypassed and a fresh pipeline is built.

    Concurrent first-callers serialize on `_pipeline_build_lock` so
    only one expensive build runs even under burst load.

    Cache invalidation: today the cache lives until process restart.
    Re-running the graph or vectorstore pipeline stages does NOT
    refresh it — restart the web server (or add an explicit reset
    endpoint later)."""
    if app is None:
        return _build_pipeline(graph_path, vectorstore_dir)

    cached = getattr(app.state, "query_pipeline", None)
    if cached is not None:
        return cached

    with _pipeline_build_lock:
        cached = getattr(app.state, "query_pipeline", None)
        if cached is not None:
            return cached
        logger.info(
            "Building QueryPipeline for the first time "
            "(graph=%s, vectorstore=%s)…", graph_path, vectorstore_dir,
        )
        t0 = time.time()
        pipeline, llm = _build_pipeline(graph_path, vectorstore_dir)
        logger.info("QueryPipeline ready in %.1fs (cached on app.state)", time.time() - t0)
        app.state.query_pipeline = (pipeline, llm)
        return pipeline, llm


def _run_query_sync(query_text: str, app=None) -> dict:
    """Run the query pipeline synchronously (called via asyncio.to_thread).

    Pass `app` (the FastAPI instance) to reuse the cached pipeline
    across requests. Without it, every call rebuilds — only used in
    legacy tests."""
    start = time.time()

    from core.src.web.app import config as web_config
    if not web_config.env_dir:
        return {
            "error": (
                "env_dir is not configured. Set it via one of: "
                "(1) `env_dir` in config/web.json, "
                "(2) `--env-dir <path>` on the CLI, or "
                "(3) the `ENV_DIR` environment variable. "
                "Example path: /home/you/work/env_vzw."
            ),
        }

    graph_path = _graph_path()
    vectorstore_dir = _vectorstore_dir()

    # Note: missing graph_path is not an error — _build_pipeline
    # falls back to a stub graph + RAG-only mode in that case.
    # The vectorstore must still exist; that check happens inside
    # _build_pipeline (raises _PipelineBuildError on empty store).
    try:
        pipeline, llm = _get_or_build_pipeline(app, graph_path, vectorstore_dir)
    except _PipelineBuildError as e:
        return {"error": str(e)}

    llm_calls_before = llm.call_count if llm else 0
    llm_start = time.time()
    response = pipeline.query(query_text)
    llm_elapsed = time.time() - llm_start
    elapsed = time.time() - start
    llm_calls_after = llm.call_count if llm else 0

    # Two views of citations for the UI:
    #   - `citations`: legacy/back-compat — every citation surface in
    #     the response (LLM-cited + context-fallback). The /query page
    #     and metrics use this.
    #   - `llm_citations`: subset where Citation.llm_cited is True —
    #     the ones the LLM actually mentioned in the answer text.
    citations = []
    llm_citations = []
    for c in response.citations:
        entry = {}
        if c.req_id:
            entry["req_id"] = c.req_id
        if c.plan_id:
            entry["plan_id"] = c.plan_id
        if c.section_number:
            entry["section_number"] = c.section_number
        if c.spec:
            entry["spec"] = c.spec
        if c.spec_section:
            entry["spec_section"] = c.spec_section
        if not entry:
            continue
        entry["llm_cited"] = bool(c.llm_cited)
        citations.append(entry)
        if c.llm_cited:
            llm_citations.append(entry)

    # Full RAG retrieval — every chunk that came back from Stage 4
    # (post-rerank top-K). The Test page renders these collapsed and
    # expands the text on click.
    rag_chunks = []
    for ch in response.retrieved_chunks:
        meta = ch.metadata or {}
        rag_chunks.append({
            "chunk_id": ch.chunk_id,
            "req_id": meta.get("req_id", ""),
            "plan_id": meta.get("plan_id", ""),
            "section_number": meta.get("section_number", ""),
            "similarity_score": round(float(ch.similarity_score), 3),
            "text": ch.text,
        })

    result = {
        "answer": response.answer,
        "citations": citations,
        "llm_citations": llm_citations,
        "rag_chunks": rag_chunks,
        "rag_chunk_count": len(rag_chunks),
        "timing": f"{elapsed:.1f}",
    }

    # Attach LLM metrics for the background task to record
    if llm and llm_calls_after > llm_calls_before:
        llm_stats = getattr(llm, "last_call_stats", {})
        result["_llm_metrics"] = {
            "model": llm.model,
            "calls": llm_calls_after - llm_calls_before,
            "elapsed_s": llm_elapsed,
            "eval_count": llm_stats.get("eval_count", 0),
            "tokens_per_second": llm_stats.get("tokens_per_second", 0),
        }

    return result


async def run_query_background(
    job_id: str,
    query_text: str,
    job_queue: JobQueue,
    request_app=None,
) -> None:
    """Execute query in a background task."""
    try:
        await job_queue.update_status(job_id, "running")
        await job_queue.append_log(job_id, f"Query: {query_text}")

        result = await asyncio.to_thread(_run_query_sync, query_text, request_app)

        if "error" in result:
            await job_queue.update_status(
                job_id, "failed",
                error_message=result["error"],
            )
            await job_queue.append_log(job_id, f"Error: {result['error']}")
            return

        # Record LLM metrics if available
        llm_metrics = result.pop("_llm_metrics", None)
        if llm_metrics:
            await _record_llm_metrics(request_app, llm_metrics)

        await job_queue.update_status(
            job_id, "completed",
            progress=100,
            result_summary=json.dumps(result),
        )
        await job_queue.append_log(
            job_id, f"Completed in {result.get('timing', '?')}s"
        )

    except Exception as exc:
        logger.exception("Query background task failed for job %s", job_id)
        try:
            await job_queue.update_status(
                job_id, "failed",
                error_message=f"Unexpected error: {exc}",
            )
            await job_queue.append_log(job_id, f"FATAL: {traceback.format_exc()}")
        except Exception:
            logger.exception("Failed to record error for job %s", job_id)


async def _record_llm_metrics(app, llm_data: dict) -> None:
    """Record LLM call metrics to MetricsStore (fire-and-forget safe)."""
    try:
        metrics_store = getattr(app.state, "metrics", None) if app else None
        if metrics_store is None:
            return

        from core.src.web.metrics import MetricRecord, _now_iso
        ts = _now_iso()
        model = llm_data.get("model", "unknown")
        elapsed = llm_data.get("elapsed_s", 0)

        records = [
            MetricRecord(
                timestamp=ts,
                category="llm",
                name="latency",
                value=elapsed,
                unit="seconds",
                tags={"model": model, "source": "query"},
            ),
        ]

        eval_count = llm_data.get("eval_count", 0)
        tok_per_s = llm_data.get("tokens_per_second", 0)

        if eval_count > 0:
            records.append(MetricRecord(
                timestamp=ts,
                category="llm",
                name="eval_count",
                value=float(eval_count),
                unit="count",
                tags={"model": model, "source": "query"},
            ))
        if tok_per_s > 0:
            records.append(MetricRecord(
                timestamp=ts,
                category="llm",
                name="tokens_per_second",
                value=tok_per_s,
                unit="tok/s",
                tags={"model": model, "source": "query"},
            ))

        await metrics_store.record_batch(records)
    except Exception as exc:
        logger.debug("Failed to record LLM metrics: %s", exc)
