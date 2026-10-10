"""Regression tests: loading + enriching an index must never mutate it on disk.

bm25x semantics (verified against wheel 0.3.1 and the vendored Rust source):

  * ``BM25(index=path)`` — the CONSTRUCTOR form — sets ``index_path`` and
    AUTO-SAVES the whole index back to that path after every mutation,
    ``enrich_batch`` included. On a serve mount this would rewrite
    ``index/best`` (tf compounds across restarts); on a read-only mount it
    raises, and the service's catch would silently fall back to vanilla BM25.
  * ``BM25.load(path)`` — the static form the service uses — does NOT set
    ``index_path``; mutations stay in memory.

The service and sira_debug call ``disable_auto_save()`` right after every
``BM25.load`` anyway — same belt-and-suspenders SIRA's own batch scripts use
(add_doc_index_adapter.py:129) — so a future bm25x version or a switch to
the constructor form cannot quietly start mutating labels. These tests pin
both semantics and the service-level invariant.

Run from the repo root inside the sandbox venv:
    pytest sandbox/sira_query/test_no_autosave.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

bm25x = pytest.importorskip("bm25x")

DOCS = [
    "the device shall support emergency callback mode",
    "emergency calls shall bypass the device lock screen",
    "the handset must register on the preferred network",
]


def _dir_digest(d: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(d.iterdir()) if p.is_file()
    }


def _build_index(index_dir: Path) -> None:
    bm25 = bm25x.BM25()
    bm25.disable_auto_save()
    bm25.add(DOCS)
    bm25.save(str(index_dir))


def test_constructor_form_autosaves_on_enrich(tmp_path):
    """The hazard the guards exist for: the constructor form auto-saves.
    If this ever FAILS, bm25x dropped auto-save entirely and the
    disable_auto_save() calls are no longer load-bearing (still harmless)."""
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    _build_index(index_dir)
    before = _dir_digest(index_dir)

    opened = bm25x.BM25(index=str(index_dir))
    opened.enrich_batch([(0, ["callback", "cbm"])])

    assert _dir_digest(index_dir) != before


def test_load_form_does_not_autosave(tmp_path):
    """The form the service uses: BM25.load never writes back — with or
    without the guard. The guard pins this against future bm25x changes."""
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    _build_index(index_dir)
    before = _dir_digest(index_dir)

    loaded = bm25x.BM25.load(str(index_dir))
    loaded.disable_auto_save()
    loaded.enrich_batch([(0, ["callback", "cbm"])])

    assert _dir_digest(index_dir) == before


def test_repeated_enrich_compounds_tf(tmp_path):
    """Why mutation-on-disk would matter: re-enriching an already-enriched
    index accumulates tf (enrich merges, never dedups) — a second startup
    over a mutated index/best would drift the ranking."""
    bm25 = bm25x.BM25()
    bm25.disable_auto_save()
    bm25.add(DOCS)

    bm25.enrich_batch([(0, ["cbm"])])
    once = bm25.search("cbm", 3)
    bm25.enrich_batch([(0, ["cbm"])])
    twice = bm25.search("cbm", 3)

    assert once and twice
    # same winning doc, but the score moved — the index drifted
    assert twice[0][0] == once[0][0]
    assert twice[0][1] != pytest.approx(once[0][1]), (
        "double enrichment should change scoring; if equal, enrich dedups "
        "and the compounding concern is void"
    )


def _service(monkeypatch, db_root: Path):
    """Import the service with a controlled env (full-deps machines only)."""
    monkeypatch.setenv("NORA_SIRA_DB_ROOT", str(db_root))
    monkeypatch.delenv("NORA_SIRA_CORRECTIONS_ROOT", raising=False)
    monkeypatch.delenv("NORA_SIRA_USE_LATEST_RUNS", raising=False)
    import importlib
    import sandbox.sira_query.service as service
    return importlib.reload(service)


def test_load_one_cell_leaves_index_untouched(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    db_root = tmp_path
    cell_dir = db_root / "mnoa__Rel2099"
    (cell_dir / "raw").mkdir(parents=True)
    index_dir = cell_dir / "index" / "best"
    index_dir.mkdir(parents=True)
    with open(cell_dir / "raw" / "corpus.jsonl", "w", encoding="utf-8") as f:
        for i, text in enumerate(DOCS):
            f.write(json.dumps({"_id": f"REQ-{i}", "title": f"t{i}", "text": text}) + "\n")
    _build_index(index_dir)
    enr_dir = cell_dir / "enrichments" / "doc"
    enr_dir.mkdir(parents=True)
    with open(enr_dir / "best.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"doc_id": "REQ-0", "phrases": ["callback", "cbm"]}) + "\n")

    before = _dir_digest(index_dir)
    service = _service(monkeypatch, db_root)
    cstate = service._load_one_cell(cell_dir, ("mnoa", "Rel2099"))
    service._apply_overlay_and_enrich(cstate)

    assert cstate.doc_enrich_applied_docs == 1
    assert _dir_digest(index_dir) == before, (
        "service load+enrich mutated index/best on disk — "
        "disable_auto_save guard missing or ineffective"
    )
