"""Hiro-Smart-Doc adapter. Hiro is a FastAPI SERVICE (not a library), so this is
an HTTP client. It also needs the separate MOSS-OCR vLLM endpoint running.

The endpoint path and streamed-region JSON schema are NOT documented precisely in
the repo, so the field mapping below is best-effort — CONFIRM it against your
running service (open its /docs Swagger UI) and adjust the `# CONFIRM:` spots.

Config via env: HIRO_BASE_URL (default http://127.0.0.1:8000), HIRO_ENDPOINT
(default /parse).
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
        self.base_url = os.getenv("HIRO_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.endpoint = os.getenv("HIRO_ENDPOINT", "/parse")

    def available(self) -> tuple[bool, str]:
        try:
            import requests  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return False, f"requests not installed: {e}"
        import requests
        try:
            r = requests.get(f"{self.base_url}/docs", timeout=3)
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
                # CONFIRM: multipart field name ("file") + endpoint path.
                resp = requests.post(
                    url, files={"file": (Path(pdf_path).name, fh, "application/pdf")},
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
