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
    """Return the threshold to pass to QueryPipeline. None disables it.

    Resolution: env var > ConfigStore (pipeline.max_distance_threshold)
    > built-in default. Empty / "off" / "none" disables the filter.
    """
    import os
    raw = os.environ.get(_MAX_DISTANCE_THRESHOLD_ENV_VAR)
    if raw is None:
        # Try ConfigStore next.
        cs_value = _config_store_get("pipeline", "max_distance_threshold")
        if cs_value is not None:
            try:
                return float(cs_value)
            except (TypeError, ValueError):
                pass
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


def _config_store_get(module: str, key: str):
    """Best-effort read from app.state.config_store. Returns None if
    the store isn't attached (DB layer disabled) or the key is absent."""
    try:
        from core.src.web import app as web_app
        cs = getattr(web_app.app.state, "config_store", None)
        if cs is None:
            return None
        return cs.get(module, key)
    except Exception:
        return None


def _resolve_top_k_cap() -> int | None:
    """Resolve the user-configured Top-K cap from the ConfigStore.

    The cap is a HARD CEILING applied after per-type widening:
    setting top_k_cap=25 means every query retrieves at most 25 chunks
    regardless of intent (SUMMARIZE / CROSS_DOC etc that would
    otherwise widen to 50). None / 0 / unset = no cap, per-type
    widening behaves as before.

    Resolves from ConfigStore only; no env-var equivalent yet.
    """
    val = _config_store_get("pipeline", "top_k_cap")
    if val is None:
        return None
    try:
        n = int(val)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _resolve_reranker():
    """Resolve and instantiate the reranker from the 3-tier config chain.

    Two backends supported (selected via `reranker_provider`):

      - "huggingface" (default): CrossEncoderReranker wrapping
        sentence_transformers.CrossEncoder. Loads from HF cache.

      - "ollama": OllamaReranker calling a local Ollama server. For
        deployments without HF access but with Ollama running locally.
        Model is whatever was pulled via `ollama pull <name>` (e.g.
        `bbjson/bge-reranker-base:latest`).

    Returns ``None`` when reranking is disabled or when the chosen
    backend fails to initialize (missing model, unreachable server,
    incompatible response shape) — caller passes ``None`` to
    ``QueryPipeline`` and ``RAGRetriever`` falls back to MockReranker
    passthrough. Same graceful-degradation contract regardless of
    provider."""
    from core.src.env.config import (
        resolve_reranker_api_key,
        resolve_reranker_base_url,
        resolve_reranker_batch_size,
        resolve_reranker_enabled,
        resolve_reranker_model,
        resolve_reranker_ollama_url,
        resolve_reranker_provider,
    )
    db_enabled = _config_store_get("llm", "reranker_enabled")
    if db_enabled is not None:
        # Stored as a string by ConfigStore; coerce to bool.
        db_enabled = str(db_enabled).strip().lower() in {
            "1", "true", "yes", "on",
        }
    enabled = resolve_reranker_enabled(config_store_value=db_enabled)
    if not enabled:
        logger.info("Reranker: disabled (MockReranker passthrough)")
        return None

    db_model = _config_store_get("llm", "reranker_model")
    model_name = resolve_reranker_model(config_store_value=db_model)
    db_provider = _config_store_get("llm", "reranker_provider")
    provider = resolve_reranker_provider(config_store_value=db_provider)
    logger.info(
        "Reranker: ENABLED provider=%s model=%s", provider, model_name,
    )

    try:
        if provider == "ollama":
            from core.src.query.reranker import OllamaReranker
            db_url = _config_store_get("llm", "reranker_ollama_url")
            base_url = resolve_reranker_ollama_url(config_store_value=db_url)
            reranker = OllamaReranker(
                model_name=model_name, base_url=base_url,
            )
        elif provider == "openai-rerank-chat":
            from core.src.query.reranker import OpenAIRerankChat
            db_url = _config_store_get("llm", "reranker_base_url")
            base_url = resolve_reranker_base_url(config_store_value=db_url)
            db_key = _config_store_get("llm", "reranker_api_key")
            api_key = resolve_reranker_api_key(config_store_value=db_key)
            db_batch = _config_store_get("llm", "reranker_batch_size")
            batch_size = resolve_reranker_batch_size(
                config_store_value=db_batch,
            )
            reranker = OpenAIRerankChat(
                model_name=model_name, base_url=base_url, api_key=api_key,
                batch_size=batch_size,
            )
        elif provider == "openai-rerank-dedicated":
            from core.src.query.reranker import OpenAIRerankDedicated
            db_url = _config_store_get("llm", "reranker_base_url")
            base_url = resolve_reranker_base_url(config_store_value=db_url)
            db_key = _config_store_get("llm", "reranker_api_key")
            api_key = resolve_reranker_api_key(config_store_value=db_key)
            reranker = OpenAIRerankDedicated(
                model_name=model_name, base_url=base_url, api_key=api_key,
            )
        elif provider == "tei":
            from core.src.query.reranker import TEIReranker
            db_url = _config_store_get("llm", "reranker_base_url")
            base_url = resolve_reranker_base_url(config_store_value=db_url)
            db_key = _config_store_get("llm", "reranker_api_key")
            api_key = resolve_reranker_api_key(config_store_value=db_key)
            reranker = TEIReranker(
                model_name=model_name, base_url=base_url, api_key=api_key,
            )
        else:
            from core.src.query.reranker import CrossEncoderReranker
            reranker = CrossEncoderReranker(model_name=model_name)
        if not getattr(reranker, "available", True):
            logger.warning(
                "Reranker (%s) for %r not available — falling back to "
                "MockReranker passthrough for this query session.",
                provider, model_name,
            )
            return None
        return reranker
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "CrossEncoderReranker init failed (%s) — falling back to "
            "MockReranker passthrough.", e,
        )
        return None


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


def _build_llm_from_env_or_default(
    provider_id: str | None = None,
    mode: str | None = None,
):
    """Construct the LLM provider for /query and /test.

    `provider_id` picks a named entry from the `config/llm.json` roster;
    empty or unknown falls back to the roster's first entry. When no roster
    is configured — the normal case today — this is ignored entirely and the
    single-provider chain below runs unchanged.

    `mode` is the asker's Fast/Think choice:
      * "fast"  -> `reasoning_effort: "none"` (skip thinking)
      * "think" -> send NO reasoning field, so the model does whatever the
                   deployment already does. We never invent an effort level
                   on its behalf.
    A mode on a provider that declares `supports_reasoning_control: false` is
    ignored with a warning — the field would be silently dropped anyway, and
    pretending otherwise would let the UI lie about what was sent.

    A roster-built provider is deliberately NOT refusal-wrapped: the roster
    names WHICH endpoint answers, so silently rerouting to a different one
    would defeat the choice the asker just made.

    Resolves provider / model / timeout / base_url / api_key via the
    unified resolver chain: CLI flag (n/a here) > **Config-page DB
    (``llm.*``)** > NORA_LLM_* env var > config/llm.json >
    EnvironmentConfig (legacy back-compat) > default. The DB tier sits
    above env vars (deviation from D-053's documented ordering)
    specifically for LLM params so values saved through the Config
    page take effect at query time — otherwise stale shell-set env
    vars would permanently mask UI edits.

    Reuses PipelineContext.create_llm_provider so the dispatch matches
    the eval pipeline exactly. Returns the provider, or a mock on
    failure (web path is non-fail-loud — falls back to mock so the
    UI keeps responding).
    """
    from core.src.env.config import resolve_llm_timeout, resolve_provider

    entry = resolve_provider(provider_id)
    if entry is not None:
        reasoning = _reasoning_for(entry, mode)
        from core.src.llm.openai_provider import OpenAICompatibleProvider

        timeout = resolve_llm_timeout(
            config_store_value=_config_store_get("llm", "llm_timeout"),
        )
        logger.info(
            "Web LLM resolved: provider=%s (%s) model=%s mode=%s reasoning=%s",
            entry.id, entry.name, entry.model, mode or entry.default_mode,
            reasoning or "<none sent>",
        )
        return OpenAICompatibleProvider(
            model=entry.model,
            base_url=entry.base_url,
            api_key=entry.api_key or None,
            timeout=timeout,
            reasoning=reasoning,
        )

    from core.src.env.config import (
        resolve_llm_provider,
        resolve_llm_model,
        resolve_llm_timeout,
        resolve_llm_base_url,
        resolve_llm_api_key,
    )
    from core.src.pipeline.runner import PipelineContext

    env_cfg = _find_env_config_for_web()
    provider = resolve_llm_provider(
        config_store_value=_config_store_get("llm", "llm_provider"),
        env_config_value=env_cfg.model_provider if env_cfg else None,
    )
    model = resolve_llm_model(
        config_store_value=_config_store_get("llm", "llm_model"),
        env_config_value=env_cfg.model_name if env_cfg else None,
    )
    timeout = resolve_llm_timeout(
        config_store_value=_config_store_get("llm", "llm_timeout"),
        env_config_value=env_cfg.model_timeout if env_cfg else None,
    )
    base_url = resolve_llm_base_url(
        config_store_value=_config_store_get("llm", "llm_base_url"),
    )
    api_key = resolve_llm_api_key(
        config_store_value=_config_store_get("llm", "llm_api_key"),
    )
    logger.info(
        "Web LLM resolved: provider=%s model=%s timeout=%ds base_url=%s api_key=%s",
        provider, model, timeout,
        base_url or "<unset>",
        "<set>" if api_key else "<unset>",
    )
    ctx = PipelineContext(
        documents_dir=Path("."),
        corrections_dir=None,
        eval_dir=None,
        verbose=False,
        model_provider=provider,
        model_name=model,
        model_timeout=timeout,
        llm_base_url=base_url,
        llm_api_key=api_key,
        # No roster entry here, so no declared reasoning capability — never
        # send a reasoning field on the single-provider chain.
        llm_reasoning="",
    )
    # create_llm_provider applies the permanent-refusal fallback wrap
    # (NORA_LLM_FALLBACK_* + NORA_LLM_REFUSAL_MARKERS) — synthesis, the
    # /test lanes, and the Eval Studio curation chat inherit it.
    return ctx.create_llm_provider(require_real=False)

def _reasoning_for(entry, mode: str | None) -> str | None:
    """Map the asker's Fast/Think choice onto a reasoning_effort value.

    Falls back to the entry's own `default_mode` when the asker expressed
    no preference, so a provider that should think by default does.
    """
    if not entry.supports_reasoning_control:
        if mode:
            logger.warning(
                "Provider %r declares no reasoning support — ignoring "
                "mode=%r.", entry.id, mode,
            )
        return None
    effective = (mode or entry.default_mode or "think").lower()
    # "think" sends nothing: the model does what the deployment configured.
    return "none" if effective == "fast" else None


# Context budget for the answer synthesizer. Named because both the cached
# build and the per-query reasoning path construct an LLMSynthesizer.
_SYNTH_MAX_TOKENS = 30000 // 4

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

    # D-DRAFT-11: read the embedder config from a per-cell config when the flat
    # one is absent (else we default to sentence-transformers → HuggingFace).
    from core.src.vectorstore.cell_loader import embedder_config_path
    vs_config_path = embedder_config_path(vectorstore_dir)
    if vs_config_path is not None:
        vs_config = VectorStoreConfig.load_json(vs_config_path)
    else:
        vs_config = VectorStoreConfig(persist_directory=str(vectorstore_dir))

    # Use the provider factory so vectorstores built with Ollama
    # embeddings (e.g. qwen3-embedding:4b) load via OllamaEmbedder
    # rather than HuggingFace — HF rejects the `:` in model names
    # when prefixing them with "sentence-transformers/".
    embedder = make_embedder(vs_config)

    # D-DRAFT-11: load per-cell stores (flat fallback covers a legacy single
    # store). The representative store seeds the rag-only stub graph + count check.
    from core.src.vectorstore.cell_loader import load_cell_stores

    cell_stores = load_cell_stores(vectorstore_dir)
    if not cell_stores or sum(s.count for s in cell_stores.values()) == 0:
        raise _PipelineBuildError(
            "Vector store is empty. Run the vectorstore pipeline stage "
            "first (Pipeline page, or: "
            "python -m core.src.vectorstore.vectorstore_cli)."
        )
    store = next(iter(cell_stores.values()))

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
        synthesizer = LLMSynthesizer(llm, max_tokens=_SYNTH_MAX_TOKENS)
    else:
        logger.info("No real LLM configured, falling back to mock synthesizer")

    threshold = _resolve_max_distance_threshold()
    if threshold is None:
        logger.info("Relevance threshold filter: DISABLED")
    else:
        logger.info("Relevance threshold filter: max_distance=%.3f", threshold)

    from core.src.env.config import resolve_grouping_enabled
    enable_grouping = resolve_grouping_enabled()
    logger.info(
        "Stage 4.7 grouping: %s",
        "ENABLED" if enable_grouping else "disabled",
    )

    top_k_cap = _resolve_top_k_cap()
    if top_k_cap:
        logger.info("Top-K cap: %d (user-configured)", top_k_cap)
    else:
        logger.info("Top-K cap: NONE (per-type widening unconstrained)")

    # Cross-encoder reranker — resolved via the unified 3-tier chain
    # (env var > Config-page DB > config/llm.json > default). False
    # default preserves the MockReranker passthrough behavior.
    reranker = _resolve_reranker()
    pipeline = QueryPipeline(
        graph=graph,
        embedder=embedder,
        store=store,
        cell_stores=cell_stores,
        synthesizer=synthesizer,
        reranker=reranker,
        top_k=10,            # floor; per-type widening lifts breadth queries
        top_k_cap=top_k_cap,  # ceiling; user-set, applied AFTER widening
        max_context_chars=30000,
        max_distance_threshold=threshold,
        enable_grouping=enable_grouping,
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


def _run_query_sync(
    query_text: str,
    app=None,
    pinned_chunk_ids: list[str] | None = None,
    provider_id: str | None = None,
    mode: str | None = None,
) -> dict:
    """Run the query pipeline synchronously (called via asyncio.to_thread).

    Pass `app` (the FastAPI instance) to reuse the cached pipeline
    across requests. Without it, every call rebuilds — only used in
    legacy tests.

    `pinned_chunk_ids` (Step 3c) drives the disambiguation-resolution
    flow: when the user picks a group from a prior disambiguation
    response, the IDs of that group's chunks come back here and the
    pipeline skips retrieval, synthesizing only from those chunks.
    """
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

    # Per-question reasoning: the cached pipeline holds a provider built at
    # cold start, so a request-scoped level gets its own provider + synthesizer
    # for this query only. The expensive parts of the pipeline (graph, embedder,
    # Chroma, BM25) stay cached; provider construction costs no network call.
    # Mutating the cached provider instead would race across concurrent queries.
    query_synthesizer = None
    if provider_id or mode:
        per_query_llm = _build_llm_from_env_or_default(
            provider_id=provider_id, mode=mode,
        )
        if per_query_llm is not None and not getattr(per_query_llm, "_is_mock", False):
            from core.src.query.synthesizer import LLMSynthesizer
            query_synthesizer = LLMSynthesizer(
                per_query_llm, max_tokens=_SYNTH_MAX_TOKENS
            )
            llm = per_query_llm
        else:
            logger.warning(
                "Per-question override (provider=%r mode=%r) requested but "
                "no real LLM resolved — answering with the cached provider.",
                provider_id, mode,
            )

    llm_calls_before = llm.call_count if llm else 0
    llm_start = time.time()
    response = pipeline.query(
        query_text,
        pinned_chunk_ids=pinned_chunk_ids,
        synthesizer=query_synthesizer,
    )
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
    # Stage 3 → Stage 4 bridge: tag each retrieved chunk with the graph
    # scoper's source for that req_id (entity / feature / plan / title /
    # traversal). The retrieval-meta on the chunk only knows about
    # dense / BM25 / rerank; this side-channel tells the Test page
    # which path through Stage 3 *gated* the chunk for retrieval.
    req_id_to_graph_source: dict[str, str] = {}
    if response.graph_candidates is not None:
        for n in response.graph_candidates.requirement_nodes:
            rid = n.attributes.get("req_id") or ""
            if rid:
                req_id_to_graph_source[rid] = n.source

    rag_chunks = []
    for ch in response.retrieved_chunks:
        meta = ch.metadata or {}
        rm = ch.retrieval_meta or {}
        rid = meta.get("req_id", "")
        rag_chunks.append({
            "chunk_id": ch.chunk_id,
            "req_id": rid,
            "plan_id": meta.get("plan_id", ""),
            "section_number": meta.get("section_number", ""),
            "similarity_score": round(float(ch.similarity_score), 3),
            "text": ch.text,
            # Per-chunk retrieval provenance — surfaces dense / BM25 /
            # RRF / reranker / glossary-pin info on the Test page so
            # the user can see *why* each chunk landed in the top-K.
            "dense_rank": rm.get("dense_rank"),
            "bm25_rank": rm.get("bm25_rank"),
            "rrf_score": rm.get("rrf_score"),
            "reranker_rank_in": rm.get("reranker_rank_in"),
            "reranker_rank_out": rm.get("reranker_rank_out"),
            "source": rm.get("source"),
            # Stage 3 (graph scoping) source for this chunk's req_id.
            # ``None`` when graph was bypassed (--rag-only) OR when
            # the chunk's req_id wasn't in the candidate set (only
            # possible via metadata fallback or glossary pin).
            "graph_source": req_id_to_graph_source.get(rid),
        })

    # Stage 4.7 disambiguation. When the pipeline short-circuits
    # because top groups scored too closely, surface the groups so
    # the UI can render user-pickable cards. Groups are empty list /
    # disambiguation_required is False on the normal path.
    groups_payload = []
    for g in response.groups:
        groups_payload.append({
            "common_prefix": list(g.common_prefix),
            "representative_titles": list(g.representative_titles),
            "score": round(float(g.score), 4),
            "chunk_count": len(g.chunks),
            "chunk_ids": [c.chunk_id for c in g.chunks],
        })

    # LLM prompt debug view — exact strings sent to the synthesizer.
    # None when synthesis was skipped (not-found / disambiguation).
    llm_system_prompt = ""
    llm_context_text = ""
    if response.assembled_context is not None:
        llm_system_prompt = response.assembled_context.system_prompt or ""
        llm_context_text = response.assembled_context.context_text or ""

    # Stage 6.5 citation audit — surface per-sentence breakdown +
    # summary stats. None on disambiguation / not-found paths.
    citation_audit_payload = None
    if response.citation_audit is not None:
        ca = response.citation_audit
        citation_audit_payload = {
            "cited_sentence_count": ca.cited_sentence_count,
            "factual_sentence_count": ca.factual_sentence_count,
            "cited_percent": round(ca.cited_percent, 1),
            "fabricated_count": ca.fabricated_count,
            "uncited_sentences": [
                {"text": s.text} for s in ca.uncited_sentences
            ],
            "fabricated": [
                {"text": s.text, "fabricated": list(s.fabricated_citations)}
                for s in ca.sentences if s.fabricated_citations
            ],
        }

    # Stage 1 + Stage 3 surfacing — the Test page's "Graph & Taxonomy"
    # panel renders these so the user can see what the analyzer
    # (taxonomy-aware) and graph scoper produced before retrieval ran.
    query_intent_payload = None
    if response.query_intent is not None:
        qi = response.query_intent
        # ``QueryIntent`` exposes ``likely_features`` (the analyzer's
        # taxonomy-derived match list); the legacy log line that says
        # "features=..." is just logging that same list under a
        # different label. There is no separate ``features`` attribute.
        query_intent_payload = {
            "query_type": qi.query_type.value,
            "mnos": list(qi.mnos),
            "releases": list(qi.releases),
            "plan_ids": list(qi.plan_ids),
            "entities": list(qi.entities),
            "concepts": list(qi.concepts),
            "likely_features": list(qi.likely_features),
        }
    graph_candidates_payload = None
    if response.graph_candidates is not None:
        gc = response.graph_candidates
        graph_candidates_payload = {
            "total": gc.total,
            "requirement_count": len(gc.requirement_nodes),
            "standards_count": len(gc.standards_nodes),
            "feature_count": len(gc.feature_nodes),
            "top_reqs": [
                {
                    "req_id": n.attributes.get("req_id", "") or n.node_id,
                    "source": n.source,
                    "score": round(n.score, 3),
                }
                for n in gc.requirement_nodes[:15]
            ],
            "features": [
                {"name": n.attributes.get("name", "") or n.node_id, "source": n.source}
                for n in gc.feature_nodes
            ],
            "standards": [
                {
                    "spec": n.attributes.get("spec", "") or n.node_id,
                    "source": n.source,
                }
                for n in gc.standards_nodes[:10]
            ],
        }

    result = {
        "answer": response.answer,
        "citations": citations,
        "llm_citations": llm_citations,
        "rag_chunks": rag_chunks,
        "rag_chunk_count": len(rag_chunks),
        "timing": f"{elapsed:.1f}",
        # Per-stage NORA timings (Stage 1..6.5). `total_ms` here covers
        # only QueryPipeline.query(); `elapsed` above additionally
        # includes pipeline construction, which is cold-start expensive.
        # The timeline renderer treats the difference as unaccounted
        # rather than folding it into a stage.
        "timings_ms": dict(response.timings_ms),
        "disambiguation_required": bool(response.disambiguation_required),
        "groups": groups_payload,
        "llm_system_prompt": llm_system_prompt,
        "llm_context_text": llm_context_text,
        "citation_audit": citation_audit_payload,
        "query_intent": query_intent_payload,
        "graph_candidates": graph_candidates_payload,
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
