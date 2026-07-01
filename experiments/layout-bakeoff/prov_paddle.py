"""PaddleOCR PP-Structure adapter. Renders each page to an image (pymupdf) and
runs layout+table recognition per page.

Targets PaddleOCR 2.7-ish `PPStructure` (image input). PaddleOCR 3.x renamed this
to `PPStructureV3` with direct PDF input — if you're on 3.x, swap the marked
`# API:` init/call. pymupdf (fitz) is already a NORA dependency.
"""

from __future__ import annotations

import time
from pathlib import Path

from layout_provider import LayoutBlock, LayoutResult, normalize_kind

# PP-Structure region type -> normalized kind.
_MAP = {
    "title": "title", "text": "text", "list": "list", "table": "table",
    "figure": "figure", "image": "figure", "figure_caption": "caption",
    "table_caption": "caption", "header": "header", "footer": "footer",
    "reference": "text", "equation": "formula",
}


def _region_text(res) -> str:
    """PP-Structure text region `res` is a list of {'text':..,'confidence':..}."""
    if isinstance(res, list):
        return "\n".join(str(r.get("text", "")) for r in res if isinstance(r, dict))
    return str(res or "")


class PaddleProvider:
    name = "paddle"

    def available(self) -> tuple[bool, str]:
        try:
            import paddleocr  # noqa: F401
            import fitz  # noqa: F401
            return True, "paddleocr + pymupdf importable"
        except Exception as e:  # noqa: BLE001
            return False, f"paddleocr/pymupdf not installed: {e}"

    def parse(self, pdf_path: Path) -> LayoutResult:
        t0 = time.time()
        res = LayoutResult(provider=self.name, source=Path(pdf_path).name)
        try:
            import fitz
            import numpy as np
            from paddleocr import PPStructure  # API: 3.x -> PPStructureV3
            engine = PPStructure(show_log=False)  # API: 3.x signature differs
        except Exception as e:  # noqa: BLE001
            res.ok, res.error = False, f"init failed: {e}"
            res.seconds = time.time() - t0
            return res

        try:
            pdf = fitz.open(str(pdf_path))
        except Exception as e:  # noqa: BLE001
            res.ok, res.error = False, f"open failed: {e}"
            res.seconds = time.time() - t0
            return res

        order = 0
        for pno in range(len(pdf)):
            pix = pdf[pno].get_pixmap(dpi=200)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 4:      # RGBA -> RGB
                img = img[:, :, :3]
            if img.shape[2] == 3:  # RGB -> BGR (paddle/cv2 convention)
                img = img[:, :, ::-1]
            try:
                regions = engine(img)  # API: 3.x -> engine.predict(...)
            except Exception as e:  # noqa: BLE001
                res.blocks.append(LayoutBlock(
                    kind="other", page=pno + 1, order=order,
                    text=f"[paddle page {pno + 1} error: {e}]"))
                order += 1
                continue
            # Approximate reading order: top-to-bottom, then left-to-right.
            regions = sorted(
                regions, key=lambda r: (r.get("bbox", [0, 0, 0, 0])[1],
                                        r.get("bbox", [0, 0, 0, 0])[0]))
            for r in regions:
                kind = normalize_kind(str(r.get("type", "")), _MAP)
                bbox = tuple(r["bbox"]) if r.get("bbox") else None
                blk = LayoutBlock(kind=kind, page=pno + 1, order=order, bbox=bbox,
                                  meta={"type": r.get("type", "")})
                rdata = r.get("res")
                if kind == "table":
                    blk.html = rdata.get("html", "") if isinstance(rdata, dict) else ""
                else:
                    blk.text = _region_text(rdata)
                res.blocks.append(blk)
                order += 1

        res.page_count = len(pdf)
        pdf.close()
        res.seconds = time.time() - t0
        return res
