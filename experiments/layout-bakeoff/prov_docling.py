"""Docling adapter (IBM). Library, born-digital-text-first, CPU-capable.

Docling's API moves between versions; the version-sensitive calls are marked
`# API:` — adjust those if your installed docling differs.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from layout_provider import LayoutBlock, LayoutResult, normalize_kind

# MNO-C PDFs are born-digital (real text layer), so OCR is OFF by default: it's
# unnecessary, avoids OCR-introduced text errors, and skips the OCR-model
# download (e.g. ch_PP-OCRv4_det_mobile.onnx) that fails on restricted networks.
# Set DOCLING_OCR=1 for scanned/image-only pages. Table STRUCTURE stays on.
_DO_OCR = os.getenv("DOCLING_OCR", "").strip().lower() in ("1", "true", "yes", "on")

# Offline model provisioning: point at a locally-copied models dir (produced by
# fetch_docling_models.py on a connected machine). When set, Docling loads models
# from here instead of Hugging Face — the fix for proxy-blocked / air-gapped PCs.
# Pair it with HF_HUB_OFFLINE=1 so no network call is even attempted.
_ARTIFACTS = os.getenv("DOCLING_ARTIFACTS", "").strip()


def _build_converter():
    """Converter with OCR gated by DOCLING_OCR, table structure enabled, and
    models loaded from DOCLING_ARTIFACTS when set. Falls back to a default
    converter if this docling version's options API differs."""
    try:
        from docling.datamodel.base_models import InputFormat  # API:
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        opts = PdfPipelineOptions()
        opts.do_ocr = _DO_OCR
        opts.do_table_structure = True
        # Extract picture crops (off by default) so figures/flow images are
        # actually captured, not just detected — needed for the deferred
        # figure/API-spec ingestion this bake-off is also scouting.
        if hasattr(opts, "generate_picture_images"):
            opts.generate_picture_images = True
        if hasattr(opts, "images_scale"):
            opts.images_scale = 2.0
        if _ARTIFACTS:
            opts.artifacts_path = _ARTIFACTS
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    except Exception:
        from docling.document_converter import DocumentConverter
        return DocumentConverter()

# Docling DocItemLabel -> normalized kind.
_MAP = {
    "title": "title", "section_header": "section_header", "text": "text",
    "paragraph": "text", "list_item": "list", "list": "list",
    "table": "table", "document_index": "table",
    "picture": "figure", "figure": "figure", "image": "figure",
    "caption": "caption", "formula": "formula", "code": "text",
    "page_header": "header", "page_footer": "footer", "footnote": "text",
}


def _prov(item, doc) -> tuple[int, tuple[float, float, float, float] | None]:
    """Page + bbox normalized to TOP-LEFT origin, PDF points (matches pymupdf).
    Docling bbox carries a coord_origin; convert via the page height so downstream
    fusion compares like-for-like."""
    prov = getattr(item, "prov", None) or []
    if not prov:
        return 0, None
    p0 = prov[0]
    page = int(getattr(p0, "page_no", 0) or 0)
    bb = getattr(p0, "bbox", None)
    if bb is None:
        return page, None
    try:
        page_h = float(doc.pages[page].size.height)
        tl = bb.to_top_left_origin(page_height=page_h)
        return page, (float(tl.l), float(tl.t), float(tl.r), float(tl.b))
    except Exception:
        try:  # last resort: raw l,t,r,b (origin unnormalized — overlay will show it)
            return page, (float(bb.l), float(bb.t), float(bb.r), float(bb.b))
        except Exception:
            return page, None


def _page_sizes(doc) -> dict:
    """{page_no: [width, height]} in Docling's points frame."""
    out: dict = {}
    try:
        for pno, pitem in (doc.pages or {}).items():
            sz = getattr(pitem, "size", None)
            if sz is not None:
                out[int(pno)] = [float(sz.width), float(sz.height)]
    except Exception:
        pass
    return out


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


def _caption(item, doc) -> str:
    fn = getattr(item, "caption_text", None)
    if callable(fn):
        try:
            return (fn(doc) or "").strip()
        except Exception:
            return ""
    return ""


def _save_picture(item, doc, image_dir: Path | None, name: str) -> str:
    """Save a picture crop into image_dir; return a path relative to image_dir's
    parent (so the sibling .md can render it), or '' if unavailable."""
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
        fpath = image_dir / f"{name}.png"
        img.save(fpath)
        return str(Path(image_dir.name) / fpath.name)
    except Exception:
        return ""


def _table_text(item, doc) -> str:
    fn = getattr(item, "export_to_dataframe", None)
    if not callable(fn):
        return ""
    for args in ((doc,), ()):  # API: newer docling wants the doc (no-arg deprecated)
        try:
            return fn(*args).to_string(index=False)
        except TypeError:
            continue
        except Exception:
            return ""
    return ""


class DoclingProvider:
    name = "docling"

    def available(self) -> tuple[bool, str]:
        try:
            import docling  # noqa: F401
            return True, "docling importable"
        except Exception as e:  # noqa: BLE001
            return False, f"docling not installed: {e}"

    def parse(self, pdf_path: Path, image_dir: Path | None = None) -> LayoutResult:
        t0 = time.time()
        res = LayoutResult(provider=self.name, source=Path(pdf_path).name)
        try:
            doc = _build_converter().convert(str(pdf_path)).document
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
            page, bbox = _prov(item, doc)
            if page:
                pages.add(page)
            blk = LayoutBlock(kind=kind, page=page, order=order, bbox=bbox,
                              meta={"label": str(getattr(item, "label", ""))})
            if kind == "table":
                blk.html = _table_html(item, doc)
                blk.text = _table_text(item, doc)
            elif kind == "figure":
                blk.image_path = _save_picture(
                    item, doc, image_dir, f"{Path(pdf_path).stem}_p{page}_{order}")
                blk.text = _caption(item, doc)
            else:
                blk.text = (getattr(item, "text", "") or "").strip()
            res.blocks.append(blk)
            order += 1

        res.page_sizes = _page_sizes(doc)
        npages = getattr(doc, "num_pages", None)
        try:
            res.page_count = npages() if callable(npages) else (npages or len(pages))
        except Exception:
            res.page_count = len(pages)
        res.seconds = time.time() - t0
        return res
