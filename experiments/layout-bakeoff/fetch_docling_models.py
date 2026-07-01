"""Pre-download Docling models on a NETWORK-CONNECTED machine, to copy to a
proxy-blocked / air-gapped work PC.

Skips OCR models (we run born-digital PDFs with OCR off), so it won't pull the
PP-OCRv4 files that fail behind a proxy — only layout + TableFormer (+ the small
code/formula & picture classifiers Docling loads by default).

    # on a machine that CAN reach huggingface.co:
    python fetch_docling_models.py ./docling-models

    # copy ./docling-models to the work PC, then run the bake-off offline:
    DOCLING_ARTIFACTS=/abs/path/docling-models HF_HUB_OFFLINE=1 \
      python run_bakeoff.py doc.pdf --provider docling --out ./out
"""

from __future__ import annotations

import sys
from pathlib import Path

from docling.utils.model_downloader import download_models


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "./docling-models")
    path = download_models(
        output_dir=out,
        progress=True,
        with_layout=True,
        with_tableformer=True,
        with_rapidocr=False,   # OCR off — skip the models that fail behind a proxy
        with_easyocr=False,
    )
    print(f"\nDownloaded Docling models to: {path}")
    print("Copy that directory to the work PC and set DOCLING_ARTIFACTS to it.")


if __name__ == "__main__":
    main()
