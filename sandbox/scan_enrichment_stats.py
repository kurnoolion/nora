#!/usr/bin/env python3
"""Sizing scan for the sira-enrichment-pe batch/prompt design.

Walks every cell (<db_root>/<MNO__Rel>/) and reports, per cell and
overall: requirement text sizes, phrases per requirement, per-req
response size (as strict JSON), and per-plan grouping stats — the
numbers needed to calibrate the batch creator's token budget (prompt
cap, response reserve) and predict batches per plan.

Prints counts and sizes only — never requirement body text or phrase
text (D-012 report posture). Paste the output back into chat.

Usage (work PC):
    python3 sandbox/scan_enrichment_stats.py <db_root> \
        [--run <doc-enrich run name>] [--chars-per-token 3.5] \
        [--prompt-cap-tokens 50000] [--overhead-tokens 4000]

Enrichments resolve like sira-query: --run pins runs/doc-enrich/<name>,
else the most-recently-modified run dir, else enrichments/doc/best.jsonl.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as stats
from pathlib import Path

PLAN_RE = re.compile(r"^\*\*plan\*\*: *(.+?) *$", re.M)
ID_KEYS = ("doc_id", "_id", "req_id", "id")
PHRASE_KEYS = ("phrases", "keywords", "words", "enrichments", "terms")


def pick_key(d: dict, keys: tuple) -> str | None:
    for k in keys:
        if k in d:
            return k
    return None


def pct(xs: list, p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


def dist(xs: list, unit: str = "") -> str:
    if not xs:
        return "n=0"
    return (f"n={len(xs)} mean={stats.mean(xs):.0f} p50={pct(xs, 50):.0f} "
            f"p95={pct(xs, 95):.0f} max={max(xs):.0f}{unit}")


def resolve_enrichments(cell_dir: Path, run_name: str) -> Path | None:
    runs = cell_dir / "runs" / "doc-enrich"
    if run_name:
        cand = runs / run_name / "enrichments.kept.jsonl"
        return cand if cand.exists() else None
    if runs.is_dir():
        for rd in sorted((p for p in runs.iterdir() if p.is_dir()),
                         key=lambda p: p.stat().st_mtime, reverse=True):
            cand = rd / "enrichments.kept.jsonl"
            if cand.exists():
                return cand
    fallback = cell_dir / "enrichments" / "doc" / "best.jsonl"
    return fallback if fallback.exists() and fallback.stat().st_size else None


def scan_cell(cell_dir: Path, args) -> dict | None:
    corpus_path = cell_dir / "raw" / "corpus.jsonl"
    if not corpus_path.exists():
        return None
    text_chars: dict[str, int] = {}
    plan_of: dict[str, str] = {}
    composites = 0
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            rid = str(obj.get("_id") or obj.get("id") or "")
            if not rid:
                continue
            if rid.startswith(("doc:", "section:")):
                composites += 1
                continue
            text = str(obj.get("text") or "")
            text_chars[rid] = len(text)
            m = PLAN_RE.search(text)
            plan_of[rid] = m.group(1) if m else "(no plan stamp)"

    enr_path = resolve_enrichments(cell_dir, args.run)
    phrases_per_req: list[int] = []
    phrase_chars: list[int] = []
    resp_chars: list[int] = []   # strict-JSON {req_id: [phrases]} per req
    enriched_ids: set[str] = set()
    bad_rows = 0
    if enr_path is not None:
        with open(enr_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except ValueError:
                    bad_rows += 1
                    continue
                ik, pk = pick_key(obj, ID_KEYS), pick_key(obj, PHRASE_KEYS)
                if ik is None or pk is None:
                    bad_rows += 1
                    continue
                rid, phrases = str(obj[ik]), obj[pk]
                if not isinstance(phrases, list):
                    bad_rows += 1
                    continue
                if rid.startswith(("doc:", "section:")):
                    continue
                enriched_ids.add(rid)
                phrases_per_req.append(len(phrases))
                phrase_chars.extend(len(str(p)) for p in phrases)
                resp_chars.append(len(json.dumps(
                    {rid: [str(p) for p in phrases]}, ensure_ascii=False)))

    plans: dict[str, list[int]] = {}
    for rid, n in text_chars.items():
        plans.setdefault(plan_of[rid], []).append(n)

    cpt = args.chars_per_token
    budget_tokens = args.prompt_cap_tokens - args.overhead_tokens
    batch_report = []
    total_batches = 0
    for plan, sizes in sorted(plans.items()):
        plan_tokens = sum(sizes) / cpt
        batches = max(1, -(-int(plan_tokens) // budget_tokens))  # ceil
        total_batches += batches
        batch_report.append(
            f"    {plan}: reqs={len(sizes)} "
            f"text_tokens≈{plan_tokens:,.0f} → batches≈{batches}")

    return {
        "cell": cell_dir.name,
        "reqs": len(text_chars), "composites": composites,
        "text_chars": list(text_chars.values()),
        "enr_source": str(enr_path.relative_to(cell_dir)) if enr_path else "(none)",
        "enriched": len(enriched_ids), "bad_rows": bad_rows,
        "phrases_per_req": phrases_per_req, "phrase_chars": phrase_chars,
        "resp_chars": resp_chars,
        "plans": len(plans), "batch_report": batch_report,
        "total_batches": total_batches,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db_root")
    ap.add_argument("--run", default="", help="pinned doc-enrich run name")
    ap.add_argument("--chars-per-token", type=float, default=3.5)
    ap.add_argument("--prompt-cap-tokens", type=int, default=50_000)
    ap.add_argument("--overhead-tokens", type=int, default=4_000,
                    help="corpus overview + taxonomy + instructions estimate")
    args = ap.parse_args()

    cells = [c for c in sorted(Path(args.db_root).iterdir())
             if c.is_dir() and (c / "raw" / "corpus.jsonl").exists()]
    if not cells:
        print(f"no cells (dirs with raw/corpus.jsonl) under {args.db_root}")
        return

    cpt = args.chars_per_token
    all_ppr: list[int] = []
    all_resp: list[int] = []
    print(f"ENRICHMENT SIZING SCAN  db_root={args.db_root}")
    print(f"assumptions: {cpt} chars/token · prompt cap "
          f"{args.prompt_cap_tokens:,} tokens · overhead "
          f"{args.overhead_tokens:,} tokens (overview+taxonomy+instructions)")
    for cell_dir in cells:
        r = scan_cell(cell_dir, args)
        if r is None:
            continue
        all_ppr.extend(r["phrases_per_req"])
        all_resp.extend(r["resp_chars"])
        print(f"\n== {r['cell']} ==")
        print(f"  reqs={r['reqs']} (+{r['composites']} doc:/section: composites) "
              f"plans={r['plans']} enriched={r['enriched']} "
              f"[{r['enr_source']}"
              + (f", {r['bad_rows']} unparsed rows]" if r["bad_rows"] else "]"))
        print(f"  req text chars:   {dist(r['text_chars'])}  "
              f"(≈tokens: {dist([c / cpt for c in r['text_chars']])})")
        print(f"  phrases/req:      {dist(r['phrases_per_req'])}")
        print(f"  phrase chars:     {dist(r['phrase_chars'])}")
        print(f"  resp JSON chars/req: {dist(r['resp_chars'])}  "
              f"(≈tokens: {dist([c / cpt for c in r['resp_chars']])})")
        print(f"  per-plan packing (text only, cap−overhead budget):")
        for line in r["batch_report"]:
            print(line)
        print(f"  est. batches for cell ≈ {r['total_batches']}")

    if all_resp:
        rt = [c / cpt for c in all_resp]
        print(f"\n== OVERALL ==")
        print(f"  phrases/req:         {dist(all_ppr)}")
        print(f"  resp tokens/req:     {dist(rt)}")
        budget = args.prompt_cap_tokens - args.overhead_tokens
        print(f"  reserve check: a full batch ({budget:,}-token budget) at "
              f"p95 req size implies max reqs/batch and response "
              f"≈ reqs/batch × p95 resp tokens — compare against the "
              f"response reserve (context − prompt cap).")


if __name__ == "__main__":
    main()
