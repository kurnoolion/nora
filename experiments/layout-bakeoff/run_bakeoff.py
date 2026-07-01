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

from layout_provider import LayoutResult, save_result
from prov_docling import DoclingProvider
from prov_hiro import HiroProvider
from prov_paddle import PaddleProvider

PROVIDERS = {
    "docling": DoclingProvider,
    "paddle": PaddleProvider,
    "hiro": HiroProvider,
}


def _dump_bboxes(result: LayoutResult, out_dir: Path) -> None:
    """Compact per-page bbox listing (normalized top-left points) for eyeballing
    and for feeding overlay.py."""
    stem = f"{Path(result.source).stem}__{result.provider}"
    lines = [f"# {result.provider}  {result.source}  (top-left points)"]
    pages = sorted({b.page for b in result.blocks if b.bbox})
    for pno in pages:
        w, h = result.page_sizes.get(pno, [0, 0])
        lines.append(f"page {pno}  size={w:.0f}x{h:.0f}")
        for b in result.blocks:
            if b.page == pno and b.bbox:
                l, t, r, bt = b.bbox
                lines.append(f"  {b.kind:14} ({l:.0f},{t:.0f},{r:.0f},{bt:.0f})")
    (out_dir / f"{stem}.bboxes.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"   dumped bboxes -> {stem}.bboxes.txt")


def main() -> None:
    ap = argparse.ArgumentParser(description="Layout-parser bake-off (one provider).")
    ap.add_argument("pdfs", nargs="+", type=Path, help="PDF file(s) to parse.")
    ap.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    ap.add_argument("--out", type=Path, default=Path("./out"),
                    help="Output dir (shared across providers). Default ./out")
    ap.add_argument("--dump-bboxes", action="store_true",
                    help="Also write <stem>__<provider>.bboxes.txt (page/kind/bbox, "
                         "normalized top-left points) for the overlay check.")
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
        if args.dump_bboxes:
            _dump_bboxes(result, args.out)
        if not result.ok:
            print(f"   error: {result.error}")
            failures += 1

    print(f"\nDone. Now run:  python summarize.py {args.out}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
