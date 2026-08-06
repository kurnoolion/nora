"""Golden eval batch CLI (FR-38, strand golden-eval).

Runs the golden sample set against one serving stack — Stage 1 (retrieval
recall over ``POST /sira-query``) and optionally Stage 2 (pinned
regeneration + LLM judge). One invocation per stack; run twice with
different ``--stack-url``/``--stack-label`` for a release A/B, then compare
the two run reports.

Usage:
    python -m core.src.eval.golden_cli \\
        --env-dir <env_dir> --stack-url http://127.0.0.1:PORT \\
        --stack-label v2 [--stage all|1] [--top-k N] [--label OVERLAY] \\
        [--judge-model NAME] [--judge-prompt-version vN] [--env-name NAME]

Designed to run standalone as a build step or as a final pipeline stage.
The compact GEV block printed at the end is chat-pasteable (counts only);
full detail lands under ``<env_dir>/eval/golden/runs/<run_id>/``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .golden import GoldenEvalError, load_samples
from .golden_runner import load_judge_prompt, run_all, write_run

logger = logging.getLogger(__name__)


def _build_llm(model: str | None = None, timeout: int | None = None):
    """Construct the LLM provider via the public env resolver chain
    (CLI > env var > config/llm.json > default). The Config-page DB tier
    doesn't apply headless — this CLI runs outside the web process.
    """
    from core.src.env.config import (
        resolve_llm_api_key,
        resolve_llm_base_url,
        resolve_llm_model,
        resolve_llm_provider,
        resolve_llm_timeout,
    )

    from core.src.llm.refusal import maybe_wrap_with_refusal_fallback

    provider = resolve_llm_provider()
    model = resolve_llm_model(cli_value=model)
    timeout = resolve_llm_timeout(cli_value=timeout)
    if provider == "openai":
        from core.src.llm.openai_provider import OpenAICompatibleProvider

        llm = OpenAICompatibleProvider(
            model=model,
            base_url=resolve_llm_base_url(),
            api_key=resolve_llm_api_key(),
            timeout=timeout,
        )
    else:
        from core.src.llm.ollama_provider import OllamaProvider

        llm = OllamaProvider(model=model, timeout=timeout)
    # Same refusal-fallback wrap as the web lane — keeps Stage-2
    # synthesis on the identical provider chain (D-DRAFT-3).
    return maybe_wrap_with_refusal_fallback(llm, timeout=timeout)


def _build_query_pipeline(graph_path: Path, vectorstore_dir: Path, llm):
    """Mirror the web pipeline construction (per-cell stores, stub-graph
    RAG-only fallback) so Stage-2 candidates go through the same synthesis
    path production serves (D-DRAFT-3). Web-only Config-page knobs don't
    apply headless; env-var / config-file tiers do.
    """
    from core.src.env.config import resolve_grouping_enabled
    from core.src.query.pipeline import (
        QueryPipeline,
        build_stub_graph_from_store,
        load_graph,
    )
    from core.src.query.synthesizer import LLMSynthesizer
    from core.src.vectorstore import make_embedder
    from core.src.vectorstore.cell_loader import (
        embedder_config_path,
        load_cell_stores,
    )
    from core.src.vectorstore.config import VectorStoreConfig

    vs_config_path = embedder_config_path(vectorstore_dir)
    if vs_config_path is not None:
        vs_config = VectorStoreConfig.load_json(vs_config_path)
    else:
        vs_config = VectorStoreConfig(persist_directory=str(vectorstore_dir))
    embedder = make_embedder(vs_config)

    cell_stores = load_cell_stores(vectorstore_dir)
    if not cell_stores or sum(s.count for s in cell_stores.values()) == 0:
        raise GoldenEvalError(
            "GEV-E003",
            f"vector store empty under {vectorstore_dir} — run the "
            "vectorstore pipeline stage first",
        )
    store = next(iter(cell_stores.values()))

    if graph_path.exists():
        graph = load_graph(graph_path)
        rag_only = False
    else:
        logger.info("Graph missing — RAG-only mode (stub graph)")
        graph = build_stub_graph_from_store(store)
        rag_only = True

    pipeline = QueryPipeline(
        graph=graph,
        embedder=embedder,
        store=store,
        cell_stores=cell_stores,
        synthesizer=LLMSynthesizer(llm, max_tokens=30000 // 4),
        top_k=10,
        max_context_chars=30000,
        enable_grouping=resolve_grouping_enabled(),
    )
    if rag_only:
        pipeline._bypass_graph = True
    return pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Golden eval batch run (FR-38)"
    )
    parser.add_argument("--env-dir", required=True, type=Path)
    parser.add_argument("--stack-url", required=True)
    parser.add_argument("--stack-label", default="stack")
    parser.add_argument("--stage", choices=["1", "all"], default="all")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--label", default=None,
                        help="overlay label view for /sira-query")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--graph", type=Path, default=None)
    parser.add_argument("--vectorstore-dir", type=Path, default=None)
    parser.add_argument("--llm-model", default=None,
                        help="synthesis model override")
    parser.add_argument("--judge-model", default=None,
                        help="judge model override (default: synthesis model)")
    parser.add_argument("--judge-prompt-version", default=None)
    parser.add_argument("--env-name", default="",
                        help="environment name for the compact report header")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        samples = load_samples(args.env_dir)
    except GoldenEvalError as exc:
        print(exc)
        return 2
    if not samples:
        print(f"No golden samples under {args.env_dir}/eval/golden/samples/")
        return 2
    print(f"Loaded {len(samples)} golden sample(s)")

    query_pipeline = judge = judge_prompt = None
    if args.stage == "all":
        try:
            judge_prompt = load_judge_prompt(args.judge_prompt_version)
            synth_llm = _build_llm(model=args.llm_model)
            judge = (
                _build_llm(model=args.judge_model)
                if args.judge_model else synth_llm
            )
            graph_path = args.graph or (
                args.env_dir / "out" / "graph" / "knowledge_graph.json"
            )
            vs_dir = args.vectorstore_dir or (
                args.env_dir / "out" / "vectorstore"
            )
            query_pipeline = _build_query_pipeline(graph_path, vs_dir, synth_llm)
            print(f"Stage 2 enabled — judge prompt {judge_prompt[0]}")
        except (GoldenEvalError, ConnectionError, ValueError) as exc:
            print(f"Stage 2 setup failed, running Stage 1 only: {exc}")
            query_pipeline = judge = judge_prompt = None

    started_at = datetime.now().isoformat(timespec="seconds")
    report = run_all(
        samples,
        args.stack_url,
        args.stack_label,
        started_at,
        query_pipeline=query_pipeline,
        judge=judge,
        judge_prompt=judge_prompt,
        top_k=args.top_k,
        label=args.label,
        timeout=args.timeout,
    )

    run_dir = write_run(args.env_dir, report, env_name=args.env_name)
    print()
    print(report.compact_report(args.env_name))
    print()
    print(f"Run artifacts: {run_dir}")

    # CI signal: hard errors fail the invocation; W-level skips don't.
    if any(e["code"].startswith("GEV-E") for e in report.errors):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
