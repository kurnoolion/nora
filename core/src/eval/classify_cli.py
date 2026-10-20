"""CLI for the query-classification eval harness.

Strand jev-experimentation, phase 1.

    # the live rule-based baseline — no model, no corpus, no network
    python -m core.src.eval.classify_cli --arm rules

    # the LLM arm against a local Ollama model
    python -m core.src.eval.classify_cli --arm llm \
        --llm-provider ollama --model qwen3:8b --repeat 3

    # the LLM arm against an in-corp OpenAI-compatible endpoint
    python -m core.src.eval.classify_cli --arm llm \
        --llm-provider openai-compatible \
        --base-url http://<host>:<port>/v1 --model <tag>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from core.src.env.config import (
    resolve_llm_api_key,
    resolve_llm_base_url,
    resolve_llm_model,
    resolve_llm_provider,
    resolve_llm_timeout,
)
from core.src.eval.classify import (
    DEFAULT_SET_PATH,
    load_classify_set,
    run_classify,
)
from core.src.query.analyzer import LLMQueryAnalyzer, MockQueryAnalyzer

logger = logging.getLogger(__name__)


def _build_llm_provider(args):
    """Construct the LLM provider for the ``llm`` arm.

    Built here rather than through ``PipelineContext.create_llm_provider``,
    which requires a ``documents_dir`` this corpus-free harness has no use
    for. Mirrors golden_cli's own builder.
    """
    provider = resolve_llm_provider(cli_value=args.llm_provider)
    model = resolve_llm_model(cli_value=args.model)
    timeout = resolve_llm_timeout(cli_value=args.timeout)

    if provider == "mock" or model == "mock":
        from core.src.llm.mock_provider import MockLLMProvider

        return MockLLMProvider()

    if provider == "ollama":
        from core.src.llm.ollama_provider import OllamaProvider

        return OllamaProvider(model=model, timeout=timeout)

    if provider == "openai-compatible":
        from core.src.llm.openai_provider import OpenAICompatibleProvider

        if model == "auto":
            raise SystemExit(
                "--llm-provider openai-compatible requires an explicit "
                "--model (or NORA_LLM_MODEL)."
            )
        return OpenAICompatibleProvider(
            base_url=resolve_llm_base_url(cli_value=args.base_url),
            api_key=resolve_llm_api_key(cli_value=args.api_key),
            model=model,
            timeout=timeout,
        )

    raise SystemExit(f"unsupported --llm-provider: {provider}")


def _build_analyzer(args):
    if args.arm == "rules":
        return MockQueryAnalyzer()
    return LLMQueryAnalyzer(_build_llm_provider(args))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure a query analyzer against a labelled set."
    )
    parser.add_argument(
        "--arm", choices=["rules", "llm"], default="rules",
        help="rules = MockQueryAnalyzer (default); llm = LLMQueryAnalyzer",
    )
    parser.add_argument(
        "--set", dest="set_path", type=Path, default=None,
        help=f"labelled set JSON (default: {DEFAULT_SET_PATH})",
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="runs per sample; >1 probes determinism (default 1)",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="write the full report as JSON to this path",
    )
    parser.add_argument("--llm-provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    samples = load_classify_set(args.set_path)
    payload = json.loads(
        (args.set_path or DEFAULT_SET_PATH).read_text(encoding="utf-8")
    )
    report = run_classify(
        _build_analyzer(args),
        samples,
        repeats=args.repeat,
        arm=args.arm,
        set_id=payload.get("set_id", ""),
    )

    print(
        f"arm={report.arm} set={report.set_id} profile={report.profile} "
        f"n={len(report.results)} repeats={report.repeats}"
    )
    print(
        f"accuracy={report.accuracy:.3f} "
        f"weighted_cost={report.weighted_cost:.3f} "
        f"scope_accuracy={report.scope_accuracy:.3f} "
        f"deterministic={report.deterministic} "
        f"p50={report.latency_p50_ms:.1f}ms "
        f"p95={report.latency_p95_ms:.1f}ms"
    )
    print()
    print(report.matrix_text())

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report.to_dict(), indent=2), encoding="utf-8"
        )
        print(f"\nreport written to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
