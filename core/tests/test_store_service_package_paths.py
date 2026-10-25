"""Paths the service derives from its own location must survive the package
moves (field finding: after sandbox/sira_query -> nora_store, the clone root
and the git root pointed one level off and the prompts silently failed to
load; the package now lives at core/src/store_service, three levels down)."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

REPO = Path(__file__).resolve().parents[2]


def test_default_clone_root_is_the_sandbox_clone(monkeypatch):
    monkeypatch.delenv("NORA_SIRA_CLONE_ROOT", raising=False)
    import core.src.store_service.service as service
    service = importlib.reload(service)
    assert service._SIRA_CLONE_ROOT == REPO / "sandbox" / "sira"


def test_code_version_reads_this_repo(monkeypatch):
    monkeypatch.delenv("NORA_CODE_VERSION", raising=False)
    import core.src.store_service.service as service
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    assert service._code_version()


def test_sira_debug_default_clone_root():
    src = (REPO / "core" / "src" / "store_service" / "sira_debug.py").read_text(encoding="utf-8")
    assert 'parents[3] / "sandbox" / "sira"' in src
