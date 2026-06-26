"""verify_tables — confirm parsed tables are inlined into NORA parse text and
reached the SIRA BEIR corpus.

The table fix inlines each requirement's tables into its ``text`` at the table's
document position. This checks that invariant end to end:

  * NORA parse — every requirement that has a ``tables`` field must have the
    table inline in its ``text`` (``MISSING_inline`` must be 0, else the parser
    inline regressed).
  * SIRA corpus — per-cell count of ``corpus.jsonl`` rows that carry a markdown
    table.
  * Cross-check — tables actually reached the corpus (not silently dropped at
    the adapter boundary).

Usage:
    python -m sandbox.verify_tables --parse <env_dir>/out/parse
    python -m sandbox.verify_tables --db-root $NORA_SIRA_DB_ROOT
    python -m sandbox.verify_tables --parse <...>/out/parse --db-root <...>  # both + cross-check

Exit codes: 0 ok · 1 MISSING_inline>0 or tables absent from a non-empty corpus · 2 usage.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path


def has_inline_table(text: str) -> bool:
    """True iff `text` carries a markdown table — a line that starts with a pipe
    (`| col | col |`), inline or at the very start."""
    if not text:
        return False
    return "\n|" in text or text.lstrip().startswith("|")


def scan_parse(parse_dir: Path) -> dict:
    """Walk ``<parse>/<mno>/<rel>/*_tree.json``; count reqs, tables-field reqs,
    and how many of those have the table inlined into text. Returns totals plus
    a per-(mno/rel) breakdown."""
    per: dict[str, dict[str, int]] = {}
    total = with_field = inlined = missing = 0
    for f in sorted(glob.glob(str(parse_dir / "*" / "*" / "*_tree.json"))):
        key = "/".join(Path(f).parts[-3:-1])   # mno/rel
        d = per.setdefault(key, {"reqs": 0, "tables_field": 0, "inlined": 0, "missing": 0})
        try:
            tree = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for r in tree.get("requirements", []):
            total += 1
            d["reqs"] += 1
            if r.get("tables"):
                with_field += 1
                d["tables_field"] += 1
                if has_inline_table(r.get("text") or ""):
                    inlined += 1
                    d["inlined"] += 1
                else:
                    missing += 1
                    d["missing"] += 1
    return {
        "per": per, "reqs": total, "tables_field": with_field,
        "inlined": inlined, "missing": missing,
    }


def scan_corpus(db_root: Path) -> dict:
    """Per-cell count of ``corpus.jsonl`` rows whose text carries a markdown
    table. Returns totals plus a per-cell breakdown."""
    per: dict[str, dict[str, int]] = {}
    total_rows = total_tbl = 0
    for c in sorted(glob.glob(str(db_root / "*" / "raw" / "corpus.jsonl"))):
        cell = Path(c).parts[-3]
        rows = tbl = 0
        with open(c, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    text = json.loads(line).get("text", "")
                except json.JSONDecodeError:
                    continue
                rows += 1
                tbl += has_inline_table(text)
        per[cell] = {"rows": rows, "with_tables": tbl}
        total_rows += rows
        total_tbl += tbl
    return {"per": per, "rows": total_rows, "with_tables": total_tbl}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify_tables",
        description="Verify parsed tables are inlined into NORA text and the SIRA corpus.",
    )
    ap.add_argument("--parse", default=None,
                    help="NORA parse dir (<env_dir>/out/parse)")
    ap.add_argument("--db-root", default=os.getenv("NORA_SIRA_DB_ROOT", ""),
                    help="SIRA db_root (defaults to $NORA_SIRA_DB_ROOT)")
    args = ap.parse_args(argv)

    if not args.parse and not args.db_root:
        print("error: pass --parse and/or --db-root (or set $NORA_SIRA_DB_ROOT)",
              file=sys.stderr)
        return 2

    rc = 0
    parse_res = corpus_res = None

    if args.parse:
        pd = Path(args.parse)
        if not pd.is_dir():
            print(f"error: parse dir not found: {pd}", file=sys.stderr)
            return 2
        parse_res = scan_parse(pd)
        print("== NORA parse ==")
        for key, d in parse_res["per"].items():
            print(f"  {key}: reqs={d['reqs']} tables_field={d['tables_field']} "
                  f"inlined={d['inlined']} MISSING={d['missing']}")
        print(f"  TOTAL: reqs={parse_res['reqs']} tables_field={parse_res['tables_field']} "
              f"inlined={parse_res['inlined']} MISSING_inline={parse_res['missing']}")
        if parse_res["missing"]:
            print(f"  [FAIL] {parse_res['missing']} req(s) have a tables field but NO "
                  f"inline table — the parser inline regressed.")
            rc = 1
        elif parse_res["tables_field"]:
            print("  [ok] every req with tables has them inline in text.")
        else:
            print("  [note] no reqs carry a tables field in this parse output.")

    if args.db_root:
        dr = Path(args.db_root)
        if not dr.is_dir():
            print(f"error: db_root not found: {dr}", file=sys.stderr)
            return 2
        corpus_res = scan_corpus(dr)
        print("\n== SIRA corpus ==")
        if not corpus_res["per"]:
            print(f"  (no cells with raw/corpus.jsonl under {dr})")
        for cell, d in corpus_res["per"].items():
            print(f"  {cell}: rows={d['rows']} with_tables={d['with_tables']}")
        if corpus_res["per"]:
            print(f"  TOTAL: rows={corpus_res['rows']} with_tables={corpus_res['with_tables']}")

    if parse_res and corpus_res:
        print("\n== cross-check ==")
        pf, ct = parse_res["inlined"], corpus_res["with_tables"]
        if pf > 0 and ct == 0:
            print(f"  [FAIL] parse has {pf} table-bearing reqs but the corpus has 0 — "
                  f"tables did not reach the BEIR corpus (re-emit needed?).")
            rc = 1
        else:
            print(f"  parse inlined={pf}  ->  corpus with_tables={ct}  "
                  f"(exact match not expected — the adapter filters some reqs)")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
