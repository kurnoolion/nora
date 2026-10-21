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

    # the decision arm, offline mock — exercises the seam, measures nothing
    python -m core.src.eval.classify_cli --arm decision

    # the decision arm against a real decider. The server is probed for a
    # loaded adapter and calibration temperature before anything is
    # measured; --weights-dir adds the local file check when the weights
    # happen to sit on this host too.
    python -m core.src.eval.classify_cli --arm decision \
        --decision-provider systemone \
        --decision-base-url http://127.0.0.1:PORT/v1 \
        --decision-model kev-latest \
        --weights-dir /models/kev-4b

    # the confidence-routed arm: the decision model where its calibrated
    # confidence clears the floor, the rules everywhere else. Same
    # provider and same gate as --arm decision; the floor is the only
    # difference, and it is printed with the result because a routed
    # number cannot be read without it.
    python -m core.src.eval.classify_cli --arm confidence-routed \
        --decision-provider systemone \
        --decision-base-url http://127.0.0.1:PORT/v1 \
        --decision-model kev-latest \
        --confidence-floor 0.5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from core.src.decision.base import DecisionError
from core.src.decision.head_check import (
    assert_decision_head_present,
    assert_remote_head,
)
from core.src.env.config import (
    DECISION_API_KEY_ENV_VAR,
    DECISION_BASE_URL_ENV_VAR,
    DECISION_MODEL_ENV_VAR,
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
from core.src.query.analyzer import (
    LLMQueryAnalyzer,
    MockQueryAnalyzer,
    _build_decision_provider,
)

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


def _build_decision_arm(args):
    """Build the decision analyzer, refusing to proceed unless the server
    is verified to be serving a head.

    Design §7: "Decision heads must be asserted loaded before any
    accuracy number is recorded." A GGUF or backbone-only build loads and
    answers without erroring, so a number recorded against one would
    measure the backbone and look like a successful run.

    The authority is the **remote** probe, not the local file check. The
    first version required `--weights-dir` and trusted it, which is how
    D-DRAFT-20 happened: verified local weights, unreachable server, the
    rule-based baseline reported under `arm=decision`, exit 0. The weights
    a client can see are not the weights the server loaded. `--weights-dir`
    is now an optional extra check for when the serving host is this host.

    The mock is exempt — it measures nothing and claims nothing.

    Serves both decision arms: `decision` builds `DecisionQueryAnalyzer`
    (every answer kept) and `confidence-routed` builds
    `ConfidenceRoutedQueryAnalyzer` (answers below the floor served by the
    rules). The verification gate is identical — routing changes which
    answers are used, not whether the server is serving a head.
    """
    from core.src.decision.analyzer import (
        ConfidenceRoutedQueryAnalyzer,
        DecisionQueryAnalyzer,
    )
    from core.src.env.config import (
        resolve_decision_api_key,
        resolve_decision_confidence_floor,
        resolve_decision_provider,
    )

    # The harness keeps `mock` as its own default, unlike a served path:
    # here the mock is exempt from the head check, measures nothing and
    # claims nothing, so `--arm decision` with no provider stays an offline
    # smoke test of the seam.
    name = (
        resolve_decision_provider(cli_value=args.decision_provider) or "mock"
    )

    if name != "mock" and args.weights_dir:
        report = assert_decision_head_present(args.weights_dir)
        print(
            f"local weights verified: {report['file_count']} files, "
            f"adapter present, {', '.join(report['present'])}"
        )

    if args.decision_base_url:
        os.environ[DECISION_BASE_URL_ENV_VAR] = args.decision_base_url
    if args.decision_model:
        os.environ[DECISION_MODEL_ENV_VAR] = args.decision_model
    if args.decision_api_key:
        os.environ[DECISION_API_KEY_ENV_VAR] = args.decision_api_key

    provider = _build_decision_provider(name)
    if provider is None:
        raise SystemExit(
            f"could not build the {name!r} decision provider — see the "
            "warning above. Refusing to silently measure the rule-based "
            "analyzer under the 'decision' label."
        )

    if name != "mock":
        # The probe carries the same credentials the decisions will, or it
        # fails on exactly the servers that are configured correctly.
        card = assert_remote_head(
            provider.base_url, api_key=resolve_decision_api_key(
                cli_value=args.decision_api_key
            ), model=getattr(provider, "model", "") or "",
        )
        print(
            "decision server verified: "
            f"model={card.get('name')} base={card.get('base')} "
            f"backend={card.get('backend')} dtype={card.get('dtype')} "
            f"T={float(card.get('temperature')):.2f}"
        )

    if args.arm == "confidence-routed":
        floor = resolve_decision_confidence_floor(
            cli_value=args.confidence_floor
        )
        return ConfidenceRoutedQueryAnalyzer(provider, floor), provider

    return DecisionQueryAnalyzer(provider), provider


def _routing_summary(analyzer) -> dict:
    """Coverage per field, plus the floor it was measured at.

    Coverage is what makes a routed accuracy figure readable. An arm that
    routes 2 % of fields to the model and scores well is reporting the
    rules under the model's name, and the accuracy alone cannot show
    that.
    """
    fields = sorted(
        set(analyzer.routed_to_model) | set(analyzer.routed_to_rules)
    )
    return {
        "confidence_floor": analyzer.confidence_floor,
        "coverage": {f: analyzer.coverage(f) for f in fields},
        "routed_to_model": dict(analyzer.routed_to_model),
        "routed_to_rules": dict(analyzer.routed_to_rules),
    }


def _routing_text(summary: dict) -> str:
    """The routing block for stdout, caveat included.

    The caveat travels with the number by construction rather than by
    anyone remembering to repeat it: the default floor was selected on
    the same 50 samples it was scored on, so a routed accuracy printed
    bare invites exactly the over-claim phase-3-measurement.md warns
    about.
    """
    lines = [f"confidence_floor={summary['confidence_floor']:.2f}"]
    for field, cov in summary["coverage"].items():
        model = summary["routed_to_model"].get(field, 0)
        rules = summary["routed_to_rules"].get(field, 0)
        lines.append(
            f"  {field}: model answered {cov:.1%} "
            f"({model} of {model + rules} fields), rules answered {rules}"
        )
    lines.append(
        "  note: a floor chosen on the same samples it is scored on does "
        "not transfer. Refit on held-out labelled traffic before reading "
        "this accuracy as the routed arm's accuracy."
    )
    return "\n".join(lines)


def _build_analyzer(args):
    """Returns (analyzer, model_identity)."""
    if args.arm == "rules":
        return MockQueryAnalyzer(), "rules"
    if args.arm in ("decision", "confidence-routed"):
        analyzer, provider = _build_decision_arm(args)
        ident = (
            getattr(provider, "model", "")
            or getattr(provider, "base_url", "")
            or "?"
        )
        if args.arm == "confidence-routed":
            return analyzer, (
                f"decision:{ident}@floor={analyzer.confidence_floor:.2f}"
            )
        return analyzer, f"decision:{ident}"
    provider = _build_llm_provider(args)
    return (
        LLMQueryAnalyzer(provider),
        f"llm:{getattr(provider, 'model', '') or '?'}",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure a query analyzer against a labelled set."
    )
    parser.add_argument(
        "--arm",
        choices=["rules", "llm", "decision", "confidence-routed"],
        default="rules",
        help=(
            "rules = MockQueryAnalyzer (default); llm = LLMQueryAnalyzer; "
            "decision = DecisionQueryAnalyzer; confidence-routed = "
            "ConfidenceRoutedQueryAnalyzer (see --confidence-floor)"
        ),
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
    parser.add_argument(
        "--decision-provider", default=None,
        help="mock (default) | systemone",
    )
    parser.add_argument("--decision-model", default=None)
    parser.add_argument("--decision-base-url", default=None)
    parser.add_argument(
        "--decision-api-key", default=None,
        help=(
            "bearer token for the decision endpoint "
            "(NORA_DECISION_API_KEY). Omit for an open in-corp server; the "
            "reference server is open unless KEV_API_KEY is set"
        ),
    )
    parser.add_argument(
        "--weights-dir", default=None,
        help=(
            "weights directory of a real decision model; required for "
            "--arm decision with a non-mock provider, so the decision "
            "head is verified before any number is recorded (design §7)"
        ),
    )
    parser.add_argument(
        "--confidence-floor", type=float, default=None,
        help=(
            "--arm confidence-routed only: keep the model's answer when "
            "its calibrated confidence is at or above this, else use the "
            "rule-based answer. Default 0.50 "
            "(NORA_DECISION_CONFIDENCE_FLOOR). 0.0 keeps every answer, "
            "which reproduces --arm decision"
        ),
    )
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
    try:
        analyzer, model_identity = _build_analyzer(args)
    except DecisionError as e:
        # Exit 2 with the diagnosis, matching golden_cli's convention —
        # a head-verification refusal is an administrator-facing message, not
        # a stack trace.
        print(f"error: {e}", file=sys.stderr)
        return 2
    report = run_classify(
        analyzer,
        samples,
        repeats=args.repeat,
        arm=args.arm,
        set_id=payload.get("set_id", ""),
    )

    fallbacks = getattr(analyzer, "fallback_count", 0)
    if fallbacks and args.arm in ("decision", "confidence-routed"):
        total = getattr(analyzer, "call_count", len(samples))
        print(
            f"error: the decision provider failed on {fallbacks} of "
            f"{total} queries, so those queries were answered by the "
            "rule-based analyzer. Reporting this as the decision arm "
            "would record the rules baseline under a 'decision' label — "
            "the same silent-degradation failure design §7 guards "
            "against, arriving by transport rather than by a hollow "
            "build. Fix the provider and re-run; see the warnings above "
            "for the cause.",
            file=sys.stderr,
        )
        return 2

    print(
        f"arm={report.arm} model={model_identity} set={report.set_id} "
        f"profile={report.profile} n={len(report.results)} "
        f"repeats={report.repeats}"
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

    routing = None
    if args.arm == "confidence-routed":
        routing = _routing_summary(analyzer)
        print()
        print(_routing_text(routing))

    if args.out:
        payload_out = report.to_dict()
        if routing is not None:
            # Written here rather than added to ClassifyReport: routing is
            # one arm's provenance, and the report type is shared by four.
            payload_out["routing"] = routing
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload_out, indent=2), encoding="utf-8"
        )
        print(f"\nreport written to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
