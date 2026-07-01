"""Combine all `*.json` bake-off outputs in a dir into one comparison report.

Produces `<out>/summary.md`:
  1. a counts+timing table (per doc x provider), and
  2. the extracted TABLES rendered side-by-side per document (HTML tables render
     inline in a Markdown preview) so you can eyeball fidelity directly.

    python summarize.py ./out
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _count(blocks: list[dict], kind: str) -> int:
    return sum(1 for b in blocks if b.get("kind") == kind)


def main() -> None:
    ap = argparse.ArgumentParser(description="Summarize layout bake-off outputs.")
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()

    results = []
    for jf in sorted(args.out_dir.glob("*.json")):
        if jf.name == "summary.json":
            continue
        try:
            results.append(json.loads(jf.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            print(f"skip {jf.name}: {e}")

    if not results:
        print(f"no *.json results in {args.out_dir}")
        return

    by_doc: dict[str, dict[str, dict]] = {}
    provs: set[str] = set()
    for r in results:
        by_doc.setdefault(r["source"], {})[r["provider"]] = r
        provs.add(r["provider"])
    provs = sorted(provs)

    L: list[str] = ["# Layout bake-off summary", ""]

    L += ["## Counts & timing", "",
          "| doc | provider | ok | pages | blocks | tables | figures | seconds |",
          "|---|---|---|---:|---:|---:|---:|---:|"]
    for doc in sorted(by_doc):
        for p in provs:
            r = by_doc[doc].get(p)
            if not r:
                L.append(f"| {doc} | {p} | – | | | | | |")
                continue
            b = r["blocks"]
            L.append(f"| {doc} | {p} | {r['ok']} | {r['page_count']} | "
                     f"{len(b)} | {_count(b, 'table')} | {_count(b, 'figure')} | "
                     f"{r['seconds']:.1f} |")

    L += ["", "## Extracted tables — eyeball comparison", "",
          "_HTML tables render inline in a Markdown preview (VS Code / GitHub)._", ""]
    for doc in sorted(by_doc):
        L.append(f"### {doc}")
        for p in provs:
            r = by_doc[doc].get(p)
            L.append(f"#### {p}")
            if not r:
                L.append("_not run_\n")
                continue
            if not r["ok"]:
                L.append(f"_failed: {r.get('error', '')}_\n")
                continue
            tables = [b for b in r["blocks"] if b.get("kind") == "table"]
            if not tables:
                L.append("_no tables detected_\n")
                continue
            for i, b in enumerate(tables, 1):
                L.append(f"**table {i} (p{b.get('page', '?')})**\n")
                if b.get("html"):
                    L.append(b["html"])
                elif b.get("text"):
                    L.append("```\n" + b["text"] + "\n```")
                else:
                    L.append("_(empty)_")
                L.append("")

    out = args.out_dir / "summary.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
