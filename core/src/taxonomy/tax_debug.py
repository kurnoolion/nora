"""Taxonomy debug — replay extraction prompts for failed plans.

Rebuilds the exact prompt the taxonomy stage sends for a plan unit
(same code path: split_tree_by_plan → _build_toc → EXTRACTION_PROMPT_TEMPLATE
+ optional corpus-overview context + sentinel-aware SYSTEM_PROMPT), writes
it to a file, optionally sends it to the active LLM provider, and captures
the raw response verbatim. Use it when units stay `failed` across re-runs
in `out/taxonomy/extraction_state.json` and you need to see what the model
actually returned (refusal prose, truncation, endpoint error).

Modes:

  python -m core.src.taxonomy.tax_debug --env-dir <env_dir> --dry-run
      Write `<plan>_prompt.txt` for every failed unit in the ledger;
      no LLM calls. Sanity-check the prompts first.

  python -m core.src.taxonomy.tax_debug --env-dir <env_dir>
      Same, plus send each prompt through the active provider (resolved
      via the unified D-044 chain, same as the pipeline) and write the
      response — or the raised exception — to `<plan>_response.txt`.

  python -m core.src.taxonomy.tax_debug --env-dir <env_dir> --only P1,P2
      Restrict to specific plan_ids.

Outputs land under `<env_dir>/reports/tax_debug/`. They contain corpus
content (plan IDs, headings, vocabulary) — never commit them and redact
before pasting into chat; the stdout summary (counts, lengths, exception
types) is paste-safe. Set NORA_LLM_DEBUG_RAW=1 to additionally log each
response's pre-strip raw content at INFO (provider-level opt-in).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_active_provider():
    """Construct the LLM provider via the unified D-044 chain — the same
    path run_taxonomy uses. Returns (provider, info). Raises if the real
    provider can't be built (a mock replay would be meaningless here).
    """
    from core.src.env.config import (
        resolve_llm_provider, resolve_llm_model, resolve_llm_timeout,
    )
    from core.src.pipeline.runner import PipelineContext

    provider_name = resolve_llm_provider()
    model = resolve_llm_model()
    timeout = resolve_llm_timeout()
    ctx = PipelineContext(
        documents_dir=Path("."),
        corrections_dir=None,
        eval_dir=None,
        verbose=False,
        model_provider=provider_name,
        model_name=model,
        model_timeout=timeout,
    )
    provider = ctx.create_llm_provider(require_real=True)
    return provider, {"provider": provider_name, "model": model, "timeout": timeout}


def _failed_units(env_dir: Path):
    """Yield (ledger_key, plan_subtree) for every failed unit in the ledger.

    Reconstructs each unit exactly as run_taxonomy does: load the source
    tree named in the ledger key, split it per plan, and match the key's
    `#<plan_id>` suffix (absent for single-plan trees).
    """
    from core.src.parser.structural_parser import RequirementTree
    from core.src.taxonomy.extractor import split_tree_by_plan

    state_path = env_dir / "out" / "taxonomy" / "extraction_state.json"
    if not state_path.exists():
        raise SystemExit(f"No extraction ledger at {state_path} — run the taxonomy stage first.")
    ledger = json.loads(state_path.read_text(encoding="utf-8"))
    failed = {k: v for k, v in ledger.get("docs", {}).items()
              if v.get("status") == "failed"}
    print(f"failed units in ledger: {len(failed)}")

    parse_dir = env_dir / "out" / "parse"
    subs_by_file: dict[str, list] = {}
    for lkey in sorted(failed):
        relpath, _, plan = lkey.partition("#")
        f = parse_dir / relpath
        if not f.exists():
            print(f"SKIP {lkey}: tree file missing")
            continue
        if relpath not in subs_by_file:
            subs_by_file[relpath] = split_tree_by_plan(RequirementTree.load_json(f))
        subs = subs_by_file[relpath]
        sub = (next((s for s in subs if s.plan_id == plan), None) if plan
               else (subs[0] if len(subs) == 1 else None))
        if sub is None:
            print(f"SKIP {lkey}: no matching plan subtree after split")
            continue
        yield lkey, sub


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Replay taxonomy extraction prompts for failed plans."
    )
    ap.add_argument("--env-dir", required=True, help="environment directory (D-022 layout)")
    ap.add_argument("--only", default="",
                    help="comma-separated plan_ids (default: all failed units)")
    ap.add_argument("--dry-run", action="store_true",
                    help="write prompts only; no LLM calls")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    from core.src.taxonomy.extractor import (
        EXTRACTION_PROMPT_TEMPLATE, SYSTEM_PROMPT, FeatureExtractor,
    )

    env_dir = Path(args.env_dir).expanduser().resolve()
    only = {p.strip() for p in args.only.split(",") if p.strip()}
    out_dir = env_dir / "reports" / "tax_debug"
    out_dir.mkdir(parents=True, exist_ok=True)

    llm = None
    if not args.dry_run:
        llm, info = _resolve_active_provider()
        print(f"provider={info['provider']!r} model={info['model']!r} "
              f"timeout={info['timeout']}s")
    overview_dir = os.getenv("NORA_TAXONOMY_OVERVIEW_DIR", "").strip() or None
    extractor = FeatureExtractor(llm, overview_dir=overview_dir)
    print(f"sentinel={os.getenv('NORA_LLM_REASONING_SENTINEL', '')!r} "
          f"overview_dir={overview_dir}")

    n_sent = n_failed = 0
    for lkey, sub in _failed_units(env_dir):
        if only and sub.plan_id not in only:
            continue
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            mno=sub.mno or "Unknown", plan_id=sub.plan_id, plan_name=sub.plan_name,
            release=sub.release, version=sub.version,
            toc=FeatureExtractor._build_toc(sub),
            corpus_context=extractor._build_corpus_context(sub.mno),
        )
        (out_dir / f"{sub.plan_id}_prompt.txt").write_text(
            "===== SYSTEM =====\n" + SYSTEM_PROMPT
            + "\n\n===== USER =====\n" + prompt,
            encoding="utf-8",
        )
        if args.dry_run:
            print(f"{sub.plan_id}: prompt written ({len(prompt)} chars)")
            continue
        try:
            resp = llm.complete(prompt=prompt, system=SYSTEM_PROMPT,
                                temperature=0.0, max_tokens=4096)
            (out_dir / f"{sub.plan_id}_response.txt").write_text(resp, encoding="utf-8")
            print(f"{sub.plan_id}: prompt {len(prompt)} chars -> "
                  f"response {len(resp)} chars")
            n_sent += 1
        except Exception as e:
            (out_dir / f"{sub.plan_id}_response.txt").write_text(
                f"EXCEPTION: {e}\n\n{traceback.format_exc()}", encoding="utf-8")
            print(f"{sub.plan_id}: LLM call raised {type(e).__name__}: {str(e)[:150]}")
            n_failed += 1

    print(f"done: {n_sent} response(s), {n_failed} exception(s) -> {out_dir}")
    print("Files contain corpus content — do not commit; redact before pasting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
