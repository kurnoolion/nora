"""Run ONE layout provider over one or more PDFs and write normalized outputs.

One provider per invocation on purpose: Docling and PaddleOCR pull conflicting
heavy deps (torch vs paddle), and Hiro needs an external GPU service — so you run
each in its own venv, all writing to the SAME --out dir, then `summarize.py`
combines them.

    python run_bakeoff.py doc1.pdf doc2.pdf --provider docling --out ./out
    python run_bakeoff.py doc1.pdf doc2.pdf --provider paddle  --out ./out
    HIRO_BASE_URL=http://host:8000 python run_bakeoff.py doc1.pdf --provider hiro --out ./out
    python summarize.py ./out
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from layout_provider import save_result
from prov_docling import DoclingProvider
from prov_hiro import HiroProvider
from prov_paddle import PaddleProvider

PROVIDERS = {
    "docling": DoclingProvider,
    "paddle": PaddleProvider,
    "hiro": HiroProvider,
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Layout-parser bake-off (one provider).")
    ap.add_argument("pdfs", nargs="+", type=Path, help="PDF file(s) to parse.")
    ap.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    ap.add_argument("--out", type=Path, default=Path("./out"),
                    help="Output dir (shared across providers). Default ./out")
    args = ap.parse_args()

    provider = PROVIDERS[args.provider]()
    ok, why = provider.available()
    print(f"[{provider.name}] available={ok} — {why}")
    if not ok:
        print("  -> install its deps / start its service (see README). Skipping.")
        sys.exit(2)

    failures = 0
    for pdf in args.pdfs:
        if not pdf.exists():
            print(f"  !! missing: {pdf}")
            failures += 1
            continue
        print(f"[{provider.name}] parsing {pdf.name} ...", flush=True)
        result = provider.parse(pdf, image_dir=args.out / "images")
        path = save_result(result, args.out)
        print(f"   ok={result.ok} blocks={len(result.blocks)} "
              f"tables={len(result.tables)} figures={len(result.figures)} "
              f"{result.seconds:.1f}s -> {path.name}")
        if not result.ok:
            print(f"   error: {result.error}")
            failures += 1

    print(f"\nDone. Now run:  python summarize.py {args.out}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
