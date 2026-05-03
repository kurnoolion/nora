"""CLI entry point for the pipeline runner.

Usage:
    # Run full pipeline against an env_dir (no env config required)
    python -m core.src.pipeline.run_cli --env-dir /data/vzw-feb2026

    # Run specific stages
    python -m core.src.pipeline.run_cli --env-dir /data/vzw-feb2026 --start extract --end parse
    python -m core.src.pipeline.run_cli --env-dir /data/vzw-feb2026 --start 1 --end 3

    # Run using an environment config
    python -m core.src.pipeline.run_cli --env profiler-review

    # Show available stages
    python -m core.src.pipeline.run_cli --list-stages

    # Show QC templates
    python -m core.src.pipeline.run_cli --qc-template profile
    python -m core.src.pipeline.run_cli --fix-template taxonomy

    # Auto-detect hardware and recommend model
    python -m core.src.pipeline.run_cli --detect-hw
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from core.src.env.config import (
    EnvironmentConfig,
    PIPELINE_STAGES,
    STAGE_NAMES,
    resolve_stage,
)
from core.src.pipeline.runner import PipelineContext, PipelineRunner
from core.src.pipeline.report import (
    format_compact_report,
    format_verbose_report,
    print_fix_template,
    print_qc_template,
)

logger = logging.getLogger(__name__)


def _list_stages() -> None:
    print("Pipeline stages (use name or number with --start / --end):\n")
    print(f"  {'#':<4} {'Name':<14} Description")
    print(f"  {'─'*4} {'─'*14} {'─'*40}")
    for i, (name, desc) in enumerate(PIPELINE_STAGES, 1):
        print(f"  {i:<4} {name:<14} {desc}")


def _detect_hw() -> None:
    from core.src.llm.model_picker import detect_hardware, pick_model, MODEL_CATALOG

    hw = detect_hardware()
    print("Hardware detected:")
    print(f"  CPU:  {hw.cpu_model} ({hw.cpu_cores} cores)")
    print(f"  RAM:  {hw.ram_total_gb:.1f} GB total, {hw.ram_available_gb:.1f} GB available")
    if hw.has_gpu:
        print(f"  GPU:  {hw.gpu_name} ({hw.gpu_vram_gb:.1f} GB VRAM)")
    else:
        print("  GPU:  none detected")

    print("\nModel ranking (best to worst):")
    for spec in MODEL_CATALOG:
        fits = spec.fits(hw)
        marker = " <-- selected" if fits else ""
        fit_str = "FITS" if fits else "NO"
        print(f"  [{fit_str:3s}] {spec.name:<24s} ~{spec.ram_gb:.1f}GB  {spec.description}{marker}")
        if fits and marker:
            break  # Only mark the first one that fits

    choice = pick_model(hw)
    print(f"\nRecommendation: {choice.model}")
    print(f"  {choice.reason}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NORA — Run the network operator requirements pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --env-dir /data/vzw-feb2026 --start extract --end parse\n"
            "  %(prog)s --env profiler-review\n"
            "  ENV_DIR=/data/vzw-feb2026 %(prog)s --start parse --end vectorstore\n"
            "  %(prog)s --list-stages\n"
            "  %(prog)s --detect-hw\n"
        ),
    )

    # Mode selection
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--env", "-e", help="Environment name (from environments/ dir)")
    mode.add_argument("--env-dir", type=Path, help="Path to env_dir (standalone mode, no env config required)")
    mode.add_argument("--list-stages", action="store_true", help="Show available stages and exit")
    mode.add_argument("--detect-hw", action="store_true", help="Detect hardware and recommend model")
    mode.add_argument("--qc-template", metavar="STAGE", help="Show quality check template for a stage")
    mode.add_argument("--fix-template", metavar="ARTIFACT", help="Show correction feedback template")

    # Stage selection
    parser.add_argument("--start", default=None, help="Start stage (name or number)")
    parser.add_argument("--end", default=None, help="End stage (name or number)")

    # Pipeline options
    parser.add_argument("--profile", type=Path, default=None, help="Explicit profile path (standalone mode)")
    parser.add_argument("--model", default="auto", help="LLM model name (default: auto)")
    parser.add_argument("--model-timeout", type=int, default=600, help="LLM timeout in seconds")
    parser.add_argument(
        "--llm-provider", default=None, choices=["ollama", "openai-compatible", "mock"],
        help="LLM provider (default: env config or 'ollama'; overrides NORA_LLM_PROVIDER env var). "
             "openai-compatible reads NORA_LLM_BASE_URL / NORA_LLM_API_KEY and requires explicit --model.",
    )
    parser.add_argument(
        "--embedding-provider", default=None,
        choices=["sentence-transformers", "huggingface", "ollama"],
        help="Embedding provider (default: env config or 'sentence-transformers'; "
             "overrides NORA_EMBEDDING_PROVIDER env var). "
             "'huggingface' is an alias for 'sentence-transformers'.",
    )
    parser.add_argument(
        "--embedding-model", default=None,
        help="Embedding model name (default: env config or 'all-MiniLM-L6-v2'; "
             "overrides NORA_EMBEDDING_MODEL env var). For ollama, use names like "
             "'nomic-embed-text' or 'mxbai-embed-large' (must be `ollama pull`-ed first).",
    )
    parser.add_argument(
        "--standards-source", default=None, choices=["huggingface", "3gpp"],
        help="3GPP spec source for the standards stage (default: env config or 'huggingface'; "
             "overrides NORA_STANDARDS_SOURCE env var).",
    )
    parser.add_argument("--continue-on-error", action="store_true", help="Continue past failed stages")
    parser.add_argument(
        "--skip-taxonomy", action="store_true",
        help="Skip the taxonomy stage entirely (no LLM call, no feature/maps_to "
             "edges in the graph). Downstream stages tolerate the missing "
             "taxonomy.json. Useful when taxonomy LLM output is noisy or "
             "non-deterministic; trades feature-aware retrieval for a "
             "reproducible graph topology.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    # --- Info commands ---
    if args.list_stages:
        _list_stages()
        return

    if args.detect_hw:
        _detect_hw()
        return

    if args.qc_template:
        print(print_qc_template(args.qc_template))
        return

    if args.fix_template:
        print(print_fix_template(args.fix_template))
        return

    # --- Set up logging ---
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- Create context ---
    env_name = ""

    if args.env:
        env_path = Path("environments") / f"{args.env}.json"
        if not env_path.exists():
            print(f"Error: Environment '{args.env}' not found at {env_path}")
            sys.exit(1)
        env = EnvironmentConfig.load_json(env_path)
        errors = env.validate()
        if errors:
            print("Environment validation errors:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)

        ctx = PipelineContext.from_env(env)
        env_name = env.name

        # Use env's stage range as default, allow CLI override
        start = resolve_stage(args.start) if args.start else env.stage_start
        end = resolve_stage(args.end) if args.end else env.stage_end
    else:
        # Resolve standalone env_dir from --env-dir or, as a fallback,
        # the $ENV_DIR environment variable. Same priority chain the
        # web UI uses (web.json > --env-dir > $ENV_DIR), letting a
        # single `export ENV_DIR=...` work for both surfaces.
        env_dir = args.env_dir
        env_dir_source = "--env-dir"
        if env_dir is None:
            env_var = os.environ.get("ENV_DIR", "").strip()
            if env_var:
                env_dir = Path(env_var)
                env_dir_source = "$ENV_DIR"
        if env_dir is None:
            parser.print_help()
            print(
                "\nError: specify --env, --env-dir, or set $ENV_DIR "
                "environment variable."
            )
            sys.exit(1)
        if env_dir_source == "$ENV_DIR":
            print(f"Using env_dir from $ENV_DIR: {env_dir}")

        from core.src.env.config import (
            resolve_embedding_model,
            resolve_embedding_provider,
            resolve_llm_provider,
            resolve_standards_source,
        )
        ctx = PipelineContext.standalone(
            env_dir=env_dir,
            profile_path=args.profile,
            model_provider=resolve_llm_provider(args.llm_provider),
            model_name=args.model,
            model_timeout=args.model_timeout,
            embedding_provider=resolve_embedding_provider(args.embedding_provider),
            embedding_model=resolve_embedding_model(args.embedding_model),
            standards_source=resolve_standards_source(args.standards_source),
        )
        start = resolve_stage(args.start) if args.start else "extract"
        end = resolve_stage(args.end) if args.end else "eval"

    ctx.verbose = args.verbose
    if args.model != "auto":
        ctx.model_name = args.model
    # CLI / env-var overrides for env-config mode
    if args.env:
        from core.src.env.config import (
            resolve_embedding_model,
            resolve_embedding_provider,
            resolve_llm_provider,
            resolve_standards_source,
        )
        ctx.model_provider = resolve_llm_provider(
            args.llm_provider, env.model_provider
        )
        ctx.embedding_provider = resolve_embedding_provider(
            args.embedding_provider, env.embedding_provider
        )
        ctx.embedding_model = resolve_embedding_model(
            args.embedding_model, env.embedding_model
        )
        ctx.standards_source = resolve_standards_source(
            args.standards_source, env.standards_source
        )

    # --- Resolve stage range ---
    from core.src.env.config import STAGE_NUM
    start_idx = STAGE_NUM[start] - 1
    end_idx = STAGE_NUM[end]
    stages = STAGE_NAMES[start_idx:end_idx]

    # `--skip-taxonomy` (or env config `skip_taxonomy=True`) drops the
    # taxonomy stage from the run list. Graph and vectorstore stages
    # already tolerate a missing taxonomy.json (return None / log a
    # warning); the resulting graph just lacks feature: nodes and
    # maps_to edges. Used when taxonomy LLM output is noisy or non-
    # deterministic.
    skip_taxonomy = bool(args.skip_taxonomy) or bool(
        args.env and getattr(env, "skip_taxonomy", False)
    )
    if skip_taxonomy and "taxonomy" in stages:
        stages = [s for s in stages if s != "taxonomy"]
        print("Note: taxonomy stage skipped (--skip-taxonomy)")

    if not stages:
        print(f"Error: No stages in range {start} -> {end}")
        sys.exit(1)

    print(f"Pipeline: {start} -> {end} ({len(stages)} stages)")
    if env_name:
        print(f"Environment: {env_name}")

    # --- Detect hardware for report ---
    hw_summary = ""
    model_display = ctx.model_name
    try:
        from core.src.llm.model_picker import detect_hardware
        hw = detect_hardware()
        hw_summary = hw.compact()
    except Exception:
        pass

    # --- Run ---
    runner = PipelineRunner(ctx)
    results = runner.run(stages, continue_on_error=args.continue_on_error)

    # --- Report ---
    verbose_report = format_verbose_report(results, hw_summary, model_display, env_name)
    print(verbose_report)

    # Save report to <env_dir>/reports/ (documents_dir is <env_dir>/input, so parent = env_dir)
    report_dir = Path(ctx.documents_dir).parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"report_{ts}.txt"
    with open(report_path, "w") as f:
        f.write(verbose_report)
    print(f"\nReport saved: {report_path}")

    # Exit code
    all_ok = all(r.ok for r in results)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
