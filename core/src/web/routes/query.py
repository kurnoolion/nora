"""Query page and API routes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.src.web.jobs import JobQueue

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
GRAPH_PATH = PROJECT_ROOT / "data" / "graph" / "knowledge_graph.json"
VECTORSTORE_DIR = PROJECT_ROOT / "data" / "vectorstore"

router = APIRouter()


# -- Pages ------------------------------------------------------------------

@router.get("/query", response_class=HTMLResponse)
async def query_page(request: Request):
    from core.src.web.app import _template_response

    graph_exists = GRAPH_PATH.exists()
    vs_config_path = VECTORSTORE_DIR / "config.json"
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

def _run_query_sync(query_text: str) -> dict:
    """Run the query pipeline synchronously (called via asyncio.to_thread)."""
    start = time.time()

    if not GRAPH_PATH.exists():
        return {
            "error": (
                "Knowledge graph not found at data/graph/knowledge_graph.json. "
                "Run the graph-building pipeline stage first "
                "(Pipeline page, or: python -m src.graph.graph_cli)."
            ),
        }

    vs_config_path = VECTORSTORE_DIR / "config.json"

    from core.src.query.pipeline import QueryPipeline, load_graph

    graph = load_graph(GRAPH_PATH)

    if vs_config_path.exists():
        from core.src.vectorstore.config import VectorStoreConfig
        vs_config = VectorStoreConfig.load_json(vs_config_path)
    else:
        from core.src.vectorstore.config import VectorStoreConfig
        vs_config = VectorStoreConfig(persist_directory=str(VECTORSTORE_DIR))

    from core.src.vectorstore.embedding_st import SentenceTransformerEmbedder
    embedder = SentenceTransformerEmbedder(
        model_name=vs_config.embedding_model,
        device=vs_config.embedding_device,
        batch_size=vs_config.embedding_batch_size,
        normalize=vs_config.normalize_embeddings,
    )

    from core.src.vectorstore.store_chroma import ChromaDBStore
    store = ChromaDBStore(
        persist_directory=vs_config.persist_directory,
        collection_name=vs_config.collection_name,
        distance_metric=vs_config.distance_metric,
    )

    if store.count == 0:
        return {
            "error": (
                "Vector store is empty. Run the vectorstore pipeline stage first "
                "(Pipeline page, or: python -m src.vectorstore.vectorstore_cli)."
            ),
        }

    llm = None
    synthesizer = None
    try:
        from core.src.llm.ollama_provider import OllamaProvider
        from core.src.query.synthesizer import LLMSynthesizer
        llm = OllamaProvider(model="gemma4:e4b", timeout=300)
        synthesizer = LLMSynthesizer(llm, max_tokens=30000 // 4)
    except Exception:
        logger.info("Ollama not available, falling back to mock synthesizer")

    pipeline = QueryPipeline(
        graph=graph,
        embedder=embedder,
        store=store,
        synthesizer=synthesizer,
        top_k=10,
        max_context_chars=30000,
    )

    llm_calls_before = llm.call_count if llm else 0
    llm_start = time.time()
    response = pipeline.query(query_text)
    llm_elapsed = time.time() - llm_start
    elapsed = time.time() - start
    llm_calls_after = llm.call_count if llm else 0

    citations = []
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
        if entry:
            citations.append(entry)

    result = {
        "answer": response.answer,
        "citations": citations,
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

        result = await asyncio.to_thread(_run_query_sync, query_text)

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
