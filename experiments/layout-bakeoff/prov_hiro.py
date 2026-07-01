"""Hiro-Smart-Doc adapter. Hiro is a FastAPI SERVICE (not a library), so this is
an HTTP client. It also needs the separate MOSS-OCR vLLM endpoint running.

The streamed-region JSON schema is NOT documented precisely in the repo, so the
field mapping below is best-effort — CONFIRM it against your running service (open
its /docs Swagger UI) and adjust the `# CONFIRM:` spots.

Config via env:
  HIRO_BASE_URL   base of the HIRO FASTAPI SERVICE (default http://127.0.0.1:8000).
                  This is the Hiro app itself — do NOT put a /v1 here.
  HIRO_ENDPOINT   PDF route (default /pdf/smart-doc; /image/smart-doc for images).
  HIRO_FILE_FIELD multipart field name for the upload (default "file").

NOT set here (set on the Hiro SERVER instead): MOSS_VLLM_OCR_API — the separate
OpenAI-compatible MOSS-OCR/vLLM endpoint the Hiro service calls internally, which
DOES end in /v1 (e.g. http://127.0.0.1:8088/v1). That's Hiro's config, not ours.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from layout_provider import LayoutBlock, LayoutResult, normalize_kind

# Hiro/RT-DETR category -> normalized kind (25 categories; best-effort subset).
_MAP = {
    "body_text": "text", "text": "text", "title": "title", "heading": "title",
    "header": "header", "footer": "footer", "caption": "caption",
    "table": "table", "equation": "formula", "formula": "formula",
    "figure": "figure", "graph": "figure", "drawing": "figure",
    "photograph": "figure", "chemical_formula": "formula",
    "table_of_contents": "text", "bibliography": "text", "line_number": "other",
    "noise": "other",
}


class HiroProvider:
    name = "hiro"

    def __init__(self) -> None:
        # Base of the Hiro FastAPI app — NOT the /v1 MOSS-OCR endpoint.
        self.base_url = os.getenv("HIRO_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.endpoint = os.getenv("HIRO_ENDPOINT", "/pdf/smart-doc")
        self.file_field = os.getenv("HIRO_FILE_FIELD", "file")

    def available(self) -> tuple[bool, str]:
        try:
            import requests  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return False, f"requests not installed: {e}"
        import requests
        if "/v1" in self.base_url:
            return False, (f"HIRO_BASE_URL={self.base_url} looks like the MOSS-OCR "
                           "/v1 endpoint. Point it at the Hiro FastAPI app "
                           "(no /v1); the /v1 URL is Hiro's own MOSS_VLLM_OCR_API.")
        try:
            r = requests.get(f"{self.base_url}/health", timeout=3)
            if r.status_code < 500:
                return True, f"service reachable at {self.base_url}"
            return False, f"service HTTP {r.status_code} at {self.base_url}"
        except Exception as e:  # noqa: BLE001
            return False, f"service not reachable at {self.base_url}: {e}"

    def parse(self, pdf_path: Path) -> LayoutResult:
        t0 = time.time()
        res = LayoutResult(provider=self.name, source=Path(pdf_path).name)
        import requests
        url = f"{self.base_url}{self.endpoint}"
        try:
            with open(pdf_path, "rb") as fh:
                # CONFIRM: multipart field name at /docs (HIRO_FILE_FIELD if it
                # isn't "file"). Endpoint defaults to /pdf/smart-doc.
                resp = requests.post(
                    url,
                    files={self.file_field: (
                        Path(pdf_path).name, fh, "application/pdf")},
                    stream=True, timeout=900)
        except Exception as e:  # noqa: BLE001
            res.ok, res.error = False, f"request failed: {e}"
            res.seconds = time.time() - t0
            return res
        if resp.status_code >= 400:
            res.ok, res.error = False, f"HTTP {resp.status_code}: {resp.text[:200]}"
            res.seconds = time.time() - t0
            return res

        pages: set[int] = set()
        order = 0
        for line in resp.iter_lines():  # NDJSON: one region per line
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            # CONFIRM: the actual field names in your service's region objects.
            raw_kind = str(obj.get("type") or obj.get("category")
                           or obj.get("label") or "")
            kind = normalize_kind(raw_kind, _MAP)
            page = int(obj.get("page") or obj.get("page_no") or 0)
            if page:
                pages.add(page)
            bbox = obj.get("bbox") or obj.get("box")
            content = str(obj.get("content") or obj.get("text")
                          or obj.get("html") or "")
            blk = LayoutBlock(
                kind=kind, page=page, order=order,
                bbox=tuple(bbox) if isinstance(bbox, (list, tuple)) else None,
                meta={"raw_kind": raw_kind})
            if kind == "table" or "<table" in content.lower():
                blk.kind = "table"
                blk.html = content
            else:
                blk.text = content
            res.blocks.append(blk)
            order += 1

        res.page_count = len(pages)
        res.seconds = time.time() - t0
        return res
