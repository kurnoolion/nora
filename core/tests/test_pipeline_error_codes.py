"""Every error code the source raises or prints is registered in CODES."""

from __future__ import annotations

import re
from pathlib import Path

from core.src.pipeline.error_codes import CODES

_SRC = Path(__file__).resolve().parents[1] / "src"
_CODE_RE = re.compile(r"\b[A-Z]{3}-[EW]\d{3}\b")


def test_every_code_in_source_is_registered():
    missing: dict[str, str] = {}
    for path in _SRC.rglob("*.py"):
        for code in _CODE_RE.findall(path.read_text(encoding="utf-8")):
            if code not in CODES:
                missing.setdefault(code, str(path.relative_to(_SRC)))
    assert not missing, f"unregistered error codes: {missing}"
