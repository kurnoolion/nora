"""Docling adapter (IBM). Library, born-digital-text-first, CPU-capable.

Docling's API moves between versions; the version-sensitive calls are marked
`# API:` — adjust those if your installed docling differs.
"""

from __future__ import annotations

import time
from pathlib import Path

from layout_provider import LayoutBlock, LayoutResult, normalize_kind

# Docling DocItemLabel -> normalized kind.
_MAP = {
    "title": "title", "section_header": "section_header", "text": "text",
    "paragraph": "text", "list_item": "list", "list": "list",
    "table": "table", "document_index": "table",
    "picture": "figure", "figure": "figure", "image": "figure",
    "caption": "caption", "formula": "formula", "code": "text",
    "page_header": "header", "page_footer": "footer", "footnote": "text",
}


def _prov(item) -> tuple[int, tuple[float, float, float, float] | None]:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return 0, None
    p0 = prov[0]
    page = int(getattr(p0, "page_no", 0) or 0)
    bb = getattr(p0, "bbox", None)
    bbox = None
    if bb is not None:
        try:
            bbox = (float(bb.l), float(bb.t), float(bb.r), float(bb.b))
        except Exception:
            bbox = None
    return page, bbox


def _table_html(item, doc) -> str:
    fn = getattr(item, "export_to_html", None)
    if not callable(fn):
        return ""
    for args in ((doc,), ()):  # API: newer docling wants the doc; older takes none
        try:
            return fn(*args)
        except Exception:
            continue
    return ""


def _table_text(item) -> str:
    fn = getattr(item, "export_to_dataframe", None)
    if callable(fn):
        try:
            return fn().to_string(index=False)
        except Exception:
            pass
    return ""


class DoclingProvider:
    name = "docling"

    def available(self) -> tuple[bool, str]:
        try:
            import docling  # noqa: F401
            return True, "docling importable"
        except Exception as e:  # noqa: BLE001
            return False, f"docling not installed: {e}"

    def parse(self, pdf_path: Path) -> LayoutResult:
        t0 = time.time()
        res = LayoutResult(provider=self.name, source=Path(pdf_path).name)
        try:
            from docling.document_converter import DocumentConverter  # API:
            doc = DocumentConverter().convert(str(pdf_path)).document
        except Exception as e:  # noqa: BLE001
            res.ok, res.error = False, f"convert failed: {e}"
            res.seconds = time.time() - t0
            return res

        pages: set[int] = set()
        order = 0
        try:
            items = list(doc.iterate_items())  # API: yields (item, level)
        except Exception as e:  # noqa: BLE001
            res.ok, res.error = False, f"iterate_items failed: {e}"
            res.seconds = time.time() - t0
            return res

        for entry in items:
            item = entry[0] if isinstance(entry, tuple) else entry
            kind = normalize_kind(str(getattr(item, "label", "") or ""), _MAP)
            page, bbox = _prov(item)
            if page:
                pages.add(page)
            blk = LayoutBlock(kind=kind, page=page, order=order, bbox=bbox,
                              meta={"label": str(getattr(item, "label", ""))})
            if kind == "table":
                blk.html = _table_html(item, doc)
                blk.text = _table_text(item)
            else:
                blk.text = (getattr(item, "text", "") or "").strip()
            res.blocks.append(blk)
            order += 1

        npages = getattr(doc, "num_pages", None)
        try:
            res.page_count = npages() if callable(npages) else (npages or len(pages))
        except Exception:
            res.page_count = len(pages)
        res.seconds = time.time() - t0
        return res
