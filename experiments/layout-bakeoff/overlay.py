"""Overlay a provider's detected bboxes onto the rendered PDF pages, to VERIFY
that its normalized boxes actually land on the tables/figures — i.e. that its
coordinate frame agrees with pymupdf's. This turns the fusion coordinate
assumption into a measurement before we commit to it.

    python overlay.py doc.pdf out/doc__docling.json --out overlays --kinds table,figure

Renders each page with pymupdf and draws the provider's boxes (from the result
JSON, already normalized to top-left points) on top. If a box doesn't sit on its
table/figure — or a page prints a FRAME MISMATCH note — the two engines disagree
on geometry (rotation / CropBox), which must be handled before fusion.

Needs pymupdf + Pillow:  pip install pymupdf pillow
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

_COLORS = {
    "table": (220, 30, 30), "figure": (30, 120, 220),
    "title": (30, 160, 30), "section_header": (30, 160, 30),
    "caption": (200, 140, 0), "formula": (140, 0, 160),
    "text": (150, 150, 150), "list": (150, 150, 150),
}
_DEFAULT = (160, 90, 200)


def main() -> None:
    ap = argparse.ArgumentParser(description="Overlay provider bboxes on PDF pages.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("result_json", type=Path, help="out/<stem>__<provider>.json")
    ap.add_argument("--out", type=Path, default=Path("./overlays"))
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--kinds", default="table,figure",
                    help="comma-separated kinds to draw, or 'all'")
    args = ap.parse_args()

    try:
        import fitz
        from PIL import Image, ImageDraw
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"overlay needs pymupdf + Pillow: {e}")

    data = json.loads(args.result_json.read_text(encoding="utf-8"))
    provider = data.get("provider", "?")
    blocks = data.get("blocks", [])
    page_sizes = {int(k): v for k, v in (data.get("page_sizes") or {}).items()}
    want = (None if args.kinds.strip() == "all"
            else {k.strip() for k in args.kinds.split(",") if k.strip()})
    scale = args.dpi / 72.0

    by_page: dict[int, list] = {}
    for b in blocks:
        if b.get("bbox") and (want is None or b.get("kind") in want):
            by_page.setdefault(int(b["page"]), []).append(b)

    args.out.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(args.pdf))
    mismatches = 0
    for pno in range(len(doc)):
        page = doc[pno]
        pageno = pno + 1
        pix = page.get_pixmap(dpi=args.dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        draw = ImageDraw.Draw(img)

        fw, fh = page.rect.width, page.rect.height
        ps = page_sizes.get(pageno)
        note = ""
        if ps and (abs(ps[0] - fw) > 1 or abs(ps[1] - fh) > 1):
            note = (f"  [FRAME MISMATCH: pymupdf {fw:.0f}x{fh:.0f} vs "
                    f"{provider} {ps[0]:.0f}x{ps[1]:.0f}]")
            mismatches += 1

        for b in by_page.get(pageno, []):
            l, t, r, bt = b["bbox"]
            color = _COLORS.get(b.get("kind", ""), _DEFAULT)
            draw.rectangle([l * scale, t * scale, r * scale, bt * scale],
                           outline=color, width=3)
            draw.text((l * scale + 2, t * scale + 2), b.get("kind", "?"), fill=color)

        outp = args.out / f"{args.pdf.stem}_p{pageno:03d}_{provider}.png"
        img.save(outp)
        print(f"page {pageno}: {len(by_page.get(pageno, []))} boxes -> "
              f"{outp.name}{note}")
    doc.close()

    print(f"\nWrote overlays to {args.out}.")
    print("Boxes should sit EXACTLY on the tables/figures. Any consistent offset,"
          " axis flip, or FRAME MISMATCH note = a coordinate-frame issue "
          "(rotation / CropBox) to handle before fusion.")
    if mismatches:
        print(f"⚠ {mismatches} page(s) had a page-size mismatch — inspect those first.")


if __name__ == "__main__":
    main()
