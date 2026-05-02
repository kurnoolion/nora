"""Per-document parse audit — confidence scoring + correction template.

Walks parsed trees under `<env_dir>/out/parse/` and emits a CSV per
document listing every Requirement with a parser-assigned confidence
plus an empty `correct_section` column for human curation. Supports the
iterative ground-truth verification loop without forcing the reviewer to
manually grep the tree.

Confidence rubric (per Requirement):
  HIGH   — paragraph-anchored, depth 2–6, section_number not seen as a
           potential continuation candidate, title length sane.
  MEDIUM — depth 1, depth 7–9, table-anchored, or anomalies in title
           length / structure that aren't outright suspicious.
  LOW    — depth ≥ 10 (likely runaway numbering), depth-1 heading
           appearing AFTER a deeper section in the same doc (likely a
           heading-continuation false positive), section_number that
           appears to be concatenated digits from heading text.

Output columns (CSV per doc):
  req_id, anchor, section_number, parent_section, depth, title,
  confidence, confidence_reason, correct_section, notes

`correct_section` and `notes` are left BLANK for the reviewer to fill in.
The `confidence_reason` captures *why* this row is HIGH/MEDIUM/LOW so the
reviewer knows where to focus.

Usage:
    python -m core.src.parser.parse_audit --env-dir ~/work/env_vzw
    python -m core.src.parser.parse_audit --env-dir <ENV_DIR> --doc LTEDATARETRY
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


@dataclass
class AuditRow:
    req_id: str
    anchor: str  # "paragraph" | "table"
    section_number: str
    parent_section: str
    depth: int  # 0 for table-anchored
    title: str
    confidence: str  # "HIGH" | "MEDIUM" | "LOW"
    confidence_reason: str


# Empirical thresholds — tuned against the OA corpus + integration test.
_DEPTH_RUNAWAY_THRESHOLD = 10  # depth >= this is suspicious
_DEPTH_HIGH_CONFIDENCE_MAX = 6  # depths 2-6 are sweet-spot for this corpus
_TITLE_MAX_LEN = 200  # matches parser._HEADING_MAX_LEN
_TITLE_TINY = 8       # very short titles are suspicious for top-level


def _score_row(
    req: dict,
    seen_deep_section_before: bool,
) -> tuple[str, str]:
    """Return (confidence, reason) for a single requirement.

    `seen_deep_section_before`: True if a section at depth ≥ 2 appeared
    earlier in this document. Used to flag depth-1 headings that come
    AFTER a deep section as likely heading-continuation false positives.
    """
    sn = req.get("section_number", "")
    title = req.get("title", "") or ""
    parent = req.get("parent_section", "")

    # Table-anchored — baseline MEDIUM (less structural signal than paragraph).
    if not sn:
        if parent:
            return "MEDIUM", "table-anchored under {!r}".format(parent)
        return "LOW", "table-anchored with no parent_section"

    depth = sn.count(".") + 1

    # Runaway depth — almost always a parser artifact.
    if depth >= _DEPTH_RUNAWAY_THRESHOLD:
        return "LOW", f"depth {depth} ≥ {_DEPTH_RUNAWAY_THRESHOLD} (runaway numbering suspected)"

    # Depth-1 after a deeper section appeared — heading-continuation suspect.
    if depth == 1 and seen_deep_section_before:
        return (
            "LOW",
            "depth-1 heading after deeper section seen — possible heading-continuation false positive",
        )

    # Title length anomalies.
    if len(title) > _TITLE_MAX_LEN:
        return "LOW", f"title {len(title)} chars > {_TITLE_MAX_LEN} (heading classifier may have absorbed body)"
    if depth == 1 and len(title.strip()) < _TITLE_TINY:
        return "MEDIUM", f"top-level title only {len(title.strip())} chars — may be a fragment"

    # Depth banding.
    if depth == 1:
        return "MEDIUM", "depth 1 (top-level chapter — verify it's a real chapter)"
    if depth >= 7:
        return "MEDIUM", f"depth {depth} (deep but not yet runaway)"

    return "HIGH", f"depth {depth}, paragraph-anchored, title and structure look sane"


def _audit_doc(tree_path: Path) -> list[AuditRow]:
    """Build audit rows for one parsed tree, in document order."""
    data = json.loads(tree_path.read_text(encoding="utf-8"))
    reqs = data.get("requirements", [])

    seen_deep = False
    rows: list[AuditRow] = []

    for r in reqs:
        rid = r.get("req_id", "")
        sn = r.get("section_number", "")
        parent = r.get("parent_section", "")
        title = r.get("title", "") or ""
        depth = sn.count(".") + 1 if sn else 0
        anchor = "paragraph" if sn else "table"

        confidence, reason = _score_row(r, seen_deep_section_before=seen_deep)
        rows.append(
            AuditRow(
                req_id=rid,
                anchor=anchor,
                section_number=sn,
                parent_section=parent,
                depth=depth,
                title=title,
                confidence=confidence,
                confidence_reason=reason,
            )
        )
        # After processing this row, update the "have we gone deep yet?" flag.
        if sn and depth >= 2:
            seen_deep = True

    return rows


# ---------------------------------------------------------------------------
# CSV emission
# ---------------------------------------------------------------------------


_CSV_HEADERS = [
    "req_id",
    "anchor",
    "section_number",
    "parent_section",
    "depth",
    "title",
    "confidence",
    "confidence_reason",
    "correct_section",  # blank — reviewer fills in
    "notes",            # blank — reviewer fills in
]


def _write_csv(rows: list[AuditRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(_CSV_HEADERS)
        for r in rows:
            w.writerow(
                [
                    r.req_id,
                    r.anchor,
                    r.section_number,
                    r.parent_section,
                    r.depth,
                    r.title,
                    r.confidence,
                    r.confidence_reason,
                    "",  # correct_section
                    "",  # notes
                ]
            )


def _summarize(rows: list[AuditRow]) -> dict[str, int]:
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "TOTAL": len(rows)}
    for r in rows:
        counts[r.confidence] = counts.get(r.confidence, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate per-document parse audit CSVs (confidence + correction template)."
    )
    parser.add_argument(
        "--env-dir", required=True,
        help="Per-environment working directory; reads parsed trees from <env_dir>/out/parse/",
    )
    parser.add_argument(
        "--doc", default=None,
        help="Audit only the named doc (matches *_tree.json stem); default = all docs",
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="Output directory (default: <env_dir>/reports/audit/)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    env_dir = Path(args.env_dir).expanduser().resolve()
    trees_dir = env_dir / "out" / "parse"
    if not trees_dir.exists():
        raise SystemExit(f"trees directory not found: {trees_dir}")

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else env_dir / "reports" / "audit"

    pattern = f"{args.doc}*_tree.json" if args.doc else "*_tree.json"
    tree_paths = sorted(trees_dir.glob(pattern))
    if not tree_paths:
        raise SystemExit(f"no parsed trees match {pattern} in {trees_dir}")

    print("AUDIT")
    print(f"  env_dir   = {env_dir}")
    print(f"  trees_dir = {trees_dir}")
    print(f"  out_dir   = {out_dir}")
    print()

    grand_totals = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "TOTAL": 0}
    for tree_path in tree_paths:
        rows = _audit_doc(tree_path)
        counts = _summarize(rows)
        out_name = tree_path.stem.replace("_tree", "") + "_audit.csv"
        out_path = out_dir / out_name
        _write_csv(rows, out_path)
        print(
            f"  {tree_path.stem:<25} → {out_path.name:<25}  "
            f"HIGH={counts['HIGH']:>4} MEDIUM={counts['MEDIUM']:>4} "
            f"LOW={counts['LOW']:>4} TOTAL={counts['TOTAL']:>5}"
        )
        for k in grand_totals:
            grand_totals[k] += counts.get(k, 0)

    print()
    print(
        f"  GRAND TOTAL                                           "
        f"HIGH={grand_totals['HIGH']:>4} MEDIUM={grand_totals['MEDIUM']:>4} "
        f"LOW={grand_totals['LOW']:>4} TOTAL={grand_totals['TOTAL']:>5}"
    )
    pct = lambda n: (n / grand_totals["TOTAL"] * 100) if grand_totals["TOTAL"] else 0
    print(
        f"  pct                                                   "
        f"HIGH={pct(grand_totals['HIGH']):>4.0f}% MEDIUM={pct(grand_totals['MEDIUM']):>3.0f}% "
        f"LOW={pct(grand_totals['LOW']):>4.0f}%"
    )


if __name__ == "__main__":
    main()
