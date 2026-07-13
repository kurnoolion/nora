"""images_root redirect — extracted-image artifacts belong in the BUILD
output, not next to the source document.

The input corpus may be a read-only mount (the nora-pipeline container mounts
/data/requirements:ro); the legacy next-to-source `extracted_images/` layout
failed there with Errno 30 and silently polluted writable input trees. The
pipeline extract stage passes `images_root=<cell out>/images`; paths recorded
on blocks/metadata are then relative to the cell out dir.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from core.src.extraction.registry import extract_document
from core.src.models.document import BlockType


@pytest.fixture()
def image_pdf(tmp_path: Path) -> Path:
    """A one-page PDF carrying one embedded raster image (40x40 > tiny-filter)."""
    src_dir = tmp_path / "corpus" / "GP" / "Feb2026"
    src_dir.mkdir(parents=True)
    pdf_path = src_dir / "doc_with_image.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    pix.clear_with(90)
    page.insert_image(fitz.Rect(50, 50, 120, 120), pixmap=pix)
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


class TestImagesRootRedirect:
    def test_images_land_under_images_root(self, image_pdf: Path, tmp_path: Path):
        out_cell = tmp_path / "out" / "extract" / "GP" / "Feb2026"
        ir = extract_document(image_pdf, images_root=out_cell / "images")

        img_blocks = [b for b in ir.content_blocks if b.type == BlockType.IMAGE]
        assert img_blocks, "expected the embedded image to be extracted"
        # path recorded relative to the cell out dir, resolvable against it
        rel = Path(img_blocks[0].image_path)
        assert rel.parts[0] == "images"
        assert (out_cell / rel).is_file()
        assert ir.extraction_metadata["images_dir"] == str(
            Path("images") / image_pdf.stem)
        # nothing written next to the source document
        assert not (image_pdf.parent / "extracted_images").exists()

    def test_read_only_corpus_is_never_written(self, image_pdf: Path, tmp_path: Path):
        # simulate the container's :ro corpus mount
        ro_dir = image_pdf.parent
        ro_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            out_cell = tmp_path / "out" / "extract" / "GP" / "Feb2026"
            ir = extract_document(image_pdf, images_root=out_cell / "images")
            img_blocks = [b for b in ir.content_blocks if b.type == BlockType.IMAGE]
            assert img_blocks and (out_cell / img_blocks[0].image_path).is_file()
        finally:
            ro_dir.chmod(stat.S_IRWXU)

    def test_legacy_layout_without_images_root(self, image_pdf: Path):
        ir = extract_document(image_pdf)
        img_blocks = [b for b in ir.content_blocks if b.type == BlockType.IMAGE]
        assert img_blocks
        # legacy: extracted_images/<stem>/ next to the source, path relative
        # to the doc dir
        rel = Path(img_blocks[0].image_path)
        assert rel.parts[0] == "extracted_images"
        assert (image_pdf.parent / rel).is_file()
