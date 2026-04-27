"""CLI entry point for the pipeline runner.

Usage:
    # Run full pipeline in standalone mode
    python -m src.pipeline.run_cli --docs /path/to/pdfs

    # Run specific stages
    python -m src.pipeline.run_cli --docs /path/to/pdfs --start extract --end parse
    python -m src.pipeline.run_cli --docs /path/to/pdfs --start 1 --end 3

    # Run using an environment config
    python -m src.pipeline.run_cli --env profiler-review

    # Show available stages
    python -m src.pipeline.run_cli --list-stages

    # Show QC templates
    python -m src.pipeline.run_cli --qc-template profile
    python -m src.pipeline.run_cli --fix-template taxonomy

    # Auto-detect hardware and recommend model
    python -m src.pipeline.run_cli --detect-hw
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.env.config import (
    EnvironmentConfig,
    PIPELINE_STAGES,
    STAGE_NAMES,
    resolve_stage,
)
from src.pipeline.runner import PipelineContext, PipelineRunner
from src.pipeline.report import (
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
    from src.llm.model_picker import detect_hardware, pick_model, MODEL_CATALOG

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
            "  %(prog)s --docs ./pdfs --start extract --end parse\n"
            "  %(prog)s --env profiler-review\n"
            "  %(prog)s --list-stages\n"
            "  %(prog)s --detect-hw\n"
        ),
    )

    # Mode selection
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--env", "-e", help="Environment name (from environments/ dir)")
    mode.add_argument("--docs", type=Path, help="Document directory (standalone mode)")
    mode.add_argument("--list-stages", action="store_true", help="Show available stages and exit")
    mode.add_argument("--detect-hw", action="store_true", help="Detect hardware and recommend model")
    mode.add_argument("--qc-template", metavar="STAGE", help="Show quality check template for a stage")
    mode.add_argument("--fix-template", metavar="ARTIFACT", help="Show correction feedback template")

    # Stage selection
    parser.add_argument("--start", default=None, help="Start stage (name or number)")
    parser.add_argument("--end", default=None, help="End stage (name or number)")

    # Pipeline options
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output base directory")
    parser.add_argument("--profile", type=Path, default=None, help="Explicit profile path (standalone mode)")
    parser.add_argument("--model", default="auto", help="LLM model name (default: auto)")
    parser.add_argument("--model-timeout", type=int, default=600, help="LLM timeout in seconds")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue past failed stages")
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
    elif args.docs:
        if not args.docs.exists():
            print(f"Error: Document directory not found: {args.docs}")
            sys.exit(1)
        ctx = PipelineContext.standalone(
            documents_dir=args.docs,
            output_base=args.output or Path("data"),
            profile_path=args.profile,
            model_name=args.model,
            model_timeout=args.model_timeout,
        )
        start = resolve_stage(args.start) if args.start else "extract"
        end = resolve_stage(args.end) if args.end else "eval"
    else:
        parser.print_help()
        print("\nError: specify --env or --docs")
        sys.exit(1)

    ctx.verbose = args.verbose
    if args.model != "auto":
        ctx.model_name = args.model

    # --- Resolve stage range ---
    from src.env.config import STAGE_NUM
    start_idx = STAGE_NUM[start] - 1
    end_idx = STAGE_NUM[end]
    stages = STAGE_NAMES[start_idx:end_idx]

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
        from src.llm.model_picker import detect_hardware
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

    # Save report to file
    report_dir = ctx.stage_dirs.get("eval", Path("data/eval"))
    if args.env:
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
