"""DoclingProvider — tables + figures via Docling (IBM), behind LayoutProvider.

Docling is an OPTIONAL heavy dependency: everything is lazy-imported so importing
this module never requires Docling, and `available()` gates a missing install.
Selected per-corpus via ``DocumentProfile.layout_provider = "docling"``.

For born-digital PDFs OCR is OFF (the pymupdf text layer owns requirement text);
Docling supplies only table structure (lossless HTML + a best-effort flat grid)
and figure crops + captions. Bboxes are returned in TOP-LEFT points so the
extractor can fuse them into pymupdf's frame by position.

Env: DOCLING_OCR=1 forces OCR on (scanned pages); DOCLING_ARTIFACTS points at a
locally-provisioned models dir for offline/proxied hosts (pair with
HF_HUB_OFFLINE=1).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from core.src.extraction.layout_provider import (
    LayoutFigure,
    LayoutStructures,
    LayoutTable,
)

logger = logging.getLogger(__name__)

# Docling DocItemLabel -> ("table" | "figure" | ""); "" is ignored (text comes
# from pymupdf, not Docling).
_TABLE_LABELS = {"table", "document_index"}
_FIGURE_LABELS = {"picture", "figure", "image"}


class DoclingProvider:
    name = "docling"

    def __init__(self) -> None:
        self._do_ocr = os.getenv("DOCLING_OCR", "").strip().lower() in (
            "1", "true", "yes", "on")
        self._artifacts = os.getenv("DOCLING_ARTIFACTS", "").strip()

    def available(self) -> tuple[bool, str]:
        try:
            import docling  # noqa: F401
            return True, "docling importable"
        except Exception as e:  # noqa: BLE001
            return False, f"docling not installed: {e}"

    # -- converter -----------------------------------------------------------

    def _converter(self):
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        opts = PdfPipelineOptions()
        opts.do_ocr = self._do_ocr
        opts.do_table_structure = True
        if hasattr(opts, "generate_picture_images"):
            opts.generate_picture_images = True
        if hasattr(opts, "images_scale"):
            opts.images_scale = 2.0
        if self._artifacts:
            opts.artifacts_path = self._artifacts
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})

    # -- parse ---------------------------------------------------------------

    def extract_layout(
        self, pdf_path: Path, image_dir: Path | None = None,
        want_table_grid: bool = True,
    ) -> LayoutStructures:
        out = LayoutStructures()
        try:
            doc = self._converter().convert(str(pdf_path)).document
        except Exception as e:  # noqa: BLE001 — fall back to the geometric path
            logger.warning("docling parse failed for %s: %s", Path(pdf_path).name, e)
            return out

        out.page_sizes = _page_sizes(doc)
        order = 0
        try:
            items = list(doc.iterate_items())
        except Exception as e:  # noqa: BLE001
            logger.warning("docling iterate_items failed: %s", e)
            return out

        for entry in items:
            item = entry[0] if isinstance(entry, tuple) else entry
            label = str(getattr(item, "label", "") or "").lower()
            if label in _TABLE_LABELS:
                page, bbox = _prov(item, doc)
                headers, rows = _table_grid(item, doc) if want_table_grid else ([], [])
                out.tables.append(LayoutTable(
                    page=page, bbox=bbox, html=_table_html(item, doc),
                    headers=headers, rows=rows))
            elif label in _FIGURE_LABELS:
                page, bbox = _prov(item, doc)
                out.figures.append(LayoutFigure(
                    page=page, bbox=bbox,
                    image_path=_save_picture(
                        item, doc, image_dir,
                        f"{Path(pdf_path).stem}_p{page}_{order}"),
                    caption=_caption(item, doc)))
            order += 1
        return out


# -- helpers (module-level, testable in isolation) --------------------------

def _prov(item, doc):
    """Page (1-based) + bbox normalized to TOP-LEFT origin, PDF points."""
    prov = getattr(item, "prov", None) or []
    if not prov:
        return 0, None
    p0 = prov[0]
    page = int(getattr(p0, "page_no", 0) or 0)
    bb = getattr(p0, "bbox", None)
    if bb is None:
        return page, None
    try:
        ph = float(doc.pages[page].size.height)
        tl = bb.to_top_left_origin(page_height=ph)
        return page, (float(tl.l), float(tl.t), float(tl.r), float(tl.b))
    except Exception:
        try:
            return page, (float(bb.l), float(bb.t), float(bb.r), float(bb.b))
        except Exception:
            return page, None


def _page_sizes(doc) -> dict:
    out: dict = {}
    try:
        for pno, pitem in (doc.pages or {}).items():
            sz = getattr(pitem, "size", None)
            if sz is not None:
                out[int(pno)] = (float(sz.width), float(sz.height))
    except Exception:
        pass
    return out


def _table_html(item, doc) -> str:
    fn = getattr(item, "export_to_html", None)
    if not callable(fn):
        return ""
    for args in ((doc,), ()):
        try:
            return fn(*args)
        except Exception:
            continue
    return ""


def _table_grid(item, doc):
    """Best-effort flat (headers, rows) for the table-anchored req-id path."""
    fn = getattr(item, "export_to_dataframe", None)
    if not callable(fn):
        return [], []
    for args in ((doc,), ()):
        try:
            df = fn(*args)
            headers = [str(c) for c in df.columns]
            rows = [[str(v) for v in row] for row in df.values.tolist()]
            return headers, rows
        except TypeError:
            continue
        except Exception:
            return [], []
    return [], []


def _caption(item, doc) -> str:
    fn = getattr(item, "caption_text", None)
    if callable(fn):
        try:
            return (fn(doc) or "").strip()
        except Exception:
            return ""
    return ""


def _save_picture(item, doc, image_dir: Path | None, name: str) -> str:
    """Save a figure crop; return its absolute path (extractor relativizes it)."""
    get = getattr(item, "get_image", None)
    if image_dir is None or not callable(get):
        return ""
    try:
        img = get(doc)
    except Exception:
        img = None
    if img is None:
        return ""
    try:
        image_dir.mkdir(parents=True, exist_ok=True)
        p = image_dir / f"{name}.png"
        img.save(p)
        return str(p)
    except Exception:
        return ""
