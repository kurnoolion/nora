"""Golden run report inspector — expected vs retrieved req_ids per sample.

Reads a completed run's ``report.json`` (written by ``golden_cli`` under
``<golden>/runs/<run_id>/``) and prints, per sample, the ground-truth
req_ids (with hit rank or MISS) beside everything retrieval returned in
rank order. The two columns are independent lists, aligned for scanning
only — not row-paired.

Usage:
    python -m core.src.eval.golden_report_cli <run-dir> \\
        [--samples-dir DIR] [--misses]

``--samples-dir`` adds each sample's query text; when omitted, the tool
tries ``<run-dir>/../../samples`` (the layout ``write_run`` produces).
``--misses`` limits output to samples with at least one missed
ground-truth entry.

Output is run-dir material: it contains proprietary req_ids and query
text, so it is for local inspection only — never chat-pasteable (NFR-8;
the GEV compact block remains the shareable summary).

Deliberately stdlib-only with no package imports, so the file can be
copied to a field machine and run directly (``python3
golden_report_cli.py <run-dir>``) without the repo on sys.path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_queries(samples_dir: Path) -> dict[str, str]:
    """Map sample_id -> query text from per-sample JSON files. Missing or
    unreadable files are skipped — query text is annotation, not data.
    """
    queries: dict[str, str] = {}
    for path in sorted(samples_dir.glob("gs-*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid = str(d.get("sample_id", ""))
        if sid:
            queries[sid] = str(d.get("query", ""))
    return queries


def format_sample(s1: dict, query: str = "") -> list[str]:
    """Render one stage1 entry as side-by-side lines."""
    hits = s1.get("hits", [])
    misses = s1.get("misses", [])
    hit_ranks = {h["req_id"]: h.get("rank") for h in hits}
    expected = [h["req_id"] for h in hits] + [m["req_id"] for m in misses]
    actual = list(s1.get("retrieved_req_ids", []))
    gt = set(expected)

    lines = [
        f"=== {s1.get('sample_id', '?')}  recall={s1.get('recall', 0):.2f}  "
        f"({len(hits)}/{len(expected)} found, {len(actual)} retrieved)"
    ]
    if query:
        lines.append(f"    Q: {query}")
    width = max([len(e) for e in expected] + [10])
    lines.append(f"    {'EXPECTED':<{width}}  STATUS   ACTUAL (rank order)")
    for i in range(max(len(expected), len(actual))):
        exp = expected[i] if i < len(expected) else ""
        if exp:
            rank = hit_ranks.get(exp)
            status = f"hit r{rank}" if rank is not None else "MISS"
        else:
            status = ""
        act = actual[i] if i < len(actual) else ""
        marker = " *" if act and act in gt else ""
        lines.append(f"    {exp:<{width}}  {status:<7}  {act}{marker}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Side-by-side expected vs retrieved req_ids per sample "
        "from a golden run's report.json (local inspection only)."
    )
    parser.add_argument("run_dir", type=Path, help="run directory containing report.json")
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=None,
        help="samples dir for query text (default: <run-dir>/../../samples)",
    )
    parser.add_argument(
        "--misses",
        action="store_true",
        help="only show samples with >=1 missed ground-truth entry",
    )
    args = parser.parse_args(argv)

    report_path = args.run_dir / "report.json"
    if not report_path.is_file():
        print(f"ERROR: {report_path} not found", file=sys.stderr)
        return 2
    report = json.loads(report_path.read_text(encoding="utf-8"))

    samples_dir = args.samples_dir or (args.run_dir / ".." / ".." / "samples")
    queries = load_queries(samples_dir) if samples_dir.is_dir() else {}

    stage1 = report.get("stage1", [])
    shown = 0
    for s1 in stage1:
        if args.misses and not s1.get("misses"):
            continue
        shown += 1
        print()
        print("\n".join(format_sample(s1, queries.get(s1.get("sample_id", ""), ""))))
    print(
        f"\n{shown}/{len(stage1)} samples shown"
        + (" (misses only)" if args.misses else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
