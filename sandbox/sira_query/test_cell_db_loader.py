"""Tests for the cell-db serving mode (corp-db-layer phase 2).

The identity gate in miniature: a service loading cells from converted
SQLite cell DBs (engine bm25x) must return retrieval result sets
IDENTICAL to the same service loading the flat files. Plus: fts5 engine
shape, healthz identity keys, flag-gating (flat default untouched),
reload refusal, and label-variant fallback.

Synthetic data only. Requires bm25x + fastapi (sandbox venv); skips
cleanly where unavailable.

Run from the repo root:  pytest sandbox/sira_query/test_cell_db_loader.py
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

bm25x = pytest.importorskip("bm25x")
pytest.importorskip("fastapi")

from sandbox.cell_db_convert import convert  # noqa: E402

DOCS = [
    ("REQ-0", "emergency callback", "the device shall support emergency callback mode"),
    ("REQ-1", "lock screen", "emergency calls shall bypass the device lock screen"),
    ("REQ-2", "registration", "the handset must register on the preferred network"),
    ("REQ-3", "roaming", "data roaming shall be disabled by default on first boot"),
    ("section:planx:1", "s1", "plan section about emergency behavior"),
]
ENRICH = {"REQ-0": ["cbm", "callback service"], "REQ-2": ["attach", "registration"]}
QUERIES = [t for _, t, _ in DOCS] + ["emergency callback mode", "network attach"]
PHRASES = [["cbm"], [], [], ["attach"], [], ["callback service"], ["attach", "registration"]]


def make_flat_cells(db_root: Path) -> None:
    for mno, release in (("mnoa", "Apr2098"), ("mnob", "Apr2098")):
        base = db_root / f"{mno}__{release}"
        (base / "raw").mkdir(parents=True)
        (base / "raw" / "metadata.json").write_text("{}", encoding="utf-8")
        with open(base / "raw" / "corpus.jsonl", "w", encoding="utf-8") as f:
            for rid, title, text in DOCS:
                f.write(json.dumps({"_id": rid, "title": title, "text": text}) + "\n")
        index_dir = base / "index" / "best"
        index_dir.mkdir(parents=True)
        b = bm25x.BM25()
        b.disable_auto_save()
        b.add([text for _, _, text in DOCS])
        b.save(str(index_dir))
        enr = base / "enrichments" / "doc"
        enr.mkdir(parents=True)
        with open(enr / "best.jsonl", "w", encoding="utf-8") as f:
            for rid, phrases in ENRICH.items():
                f.write(json.dumps({"doc_id": rid, "phrases": phrases}) + "\n")


def load_service(monkeypatch, db_root: Path, *, index="flat", engine="bm25x"):
    monkeypatch.setenv("NORA_SIRA_DB_ROOT", str(db_root))
    monkeypatch.setenv("NORA_SIRA_INDEX", index)
    monkeypatch.setenv("NORA_SIRA_ENGINE", engine)
    monkeypatch.delenv("NORA_SIRA_CORRECTIONS_ROOT", raising=False)
    monkeypatch.delenv("NORA_SIRA_USE_LATEST_RUNS", raising=False)
    import sandbox.sira_query.service as service
    service = importlib.reload(service)
    service._load_cells()
    assert service._cells, service._cells_load_error
    return service


def retrieve_all(service, k: int = 5):
    """[(cell, query) -> result list] across both cells and all queries,
    exercising the expansion path where phrases exist."""
    out = {}
    for cell in sorted(service._cells):
        for q, ph in zip(QUERIES, PHRASES):
            fn = service._build_retrieve_fn(q, ph)
            out[(cell, q)] = fn(cell, k)
    return out


@pytest.fixture()
def converted(tmp_path):
    flat = tmp_path / "flat"
    make_flat_cells(flat)
    out = tmp_path / "cell-dbs"
    convert(flat, out, verify_n=0)
    return flat, out


def test_bm25x_engine_result_set_identity(tmp_path, monkeypatch, converted):
    """THE phase-2 sanity gate: cell-db/bm25x results == flat results,
    ids and scores, every query, every cell."""
    flat, out = converted
    ref = retrieve_all(load_service(monkeypatch, flat, index="flat"))
    got = retrieve_all(load_service(monkeypatch, out, index="cell-db", engine="bm25x"))
    assert set(ref) == set(got)
    for key in ref:
        assert [d for d, _ in ref[key]] == [d for d, _ in got[key]], key
        for (_, rs), (_, gs) in zip(ref[key], got[key]):
            assert rs == pytest.approx(gs, abs=1e-6)


def test_cell_db_healthz_identity(monkeypatch, converted):
    flat, out = converted
    svc_flat = load_service(monkeypatch, flat, index="flat")
    flat_health = svc_flat.healthz()
    flat_fp = dict(svc_flat._data_fingerprint_cells)

    svc = load_service(monkeypatch, out, index="cell-db", engine="bm25x")
    health = svc.healthz()
    assert health["index_mode"] == "cell-db"
    assert health["engine"] == "bm25x"
    assert health["cell_db_schema_version"] == "1"
    assert flat_health["index_mode"] == "flat"
    assert flat_health["engine"] == "flat"
    # identity parity: per-cell fingerprints equal the flat stack's
    # (same formula, recorded at convert time)
    assert svc._data_fingerprint_cells == flat_fp
    assert svc._data_fingerprint == svc_flat._data_fingerprint


def test_fts5_engine_serves_and_ranks(monkeypatch, converted):
    _flat, out = converted
    svc = load_service(monkeypatch, out, index="cell-db", engine="fts5")
    for cell in sorted(svc._cells):
        assert svc._cells[cell].engine == "fts5"
        hits = svc._build_retrieve_fn("emergency callback mode", [])(cell, 3)
        assert hits and hits[0][0] == "REQ-0"      # exact-title query wins
        assert all(s > 0 for _, s in hits)          # normalized positive scores
        # expansion path: phrase tokens reach the second MATCH pass
        hits_exp = svc._build_retrieve_fn("network attach", ["attach"])(cell, 3)
        assert hits_exp


def test_flat_default_untouched_and_bad_engine_loud(monkeypatch, converted):
    flat, out = converted
    # No env set beyond DB root: mode defaults to flat and loads flat dirs.
    monkeypatch.setenv("NORA_SIRA_DB_ROOT", str(flat))
    monkeypatch.delenv("NORA_SIRA_INDEX", raising=False)
    monkeypatch.delenv("NORA_SIRA_ENGINE", raising=False)
    import sandbox.sira_query.service as service
    service = importlib.reload(service)
    assert service._INDEX_MODE == "flat"
    service._load_cells()
    assert service._cells

    # Unsupported engine value fails loudly, serving nothing quietly.
    svc = None
    monkeypatch.setenv("NORA_SIRA_DB_ROOT", str(out))
    monkeypatch.setenv("NORA_SIRA_INDEX", "cell-db")
    monkeypatch.setenv("NORA_SIRA_ENGINE", "memory")
    svc = importlib.reload(service)
    svc._load_cells()
    assert not svc._cells
    assert "unsupported" in (svc._cells_load_error or "")


def test_cell_db_reload_refused_and_variant_falls_back(monkeypatch, converted):
    _flat, out = converted
    svc = load_service(monkeypatch, out, index="cell-db", engine="bm25x")
    cell = sorted(svc._cells)[0]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        svc.cell_reload(svc.cell_dirname(cell))
    assert ei.value.status_code == 409
    # label variants are flat-mode machinery: fall back to the default view
    assert svc._get_variant(cell, "expert-x", build=True) is svc._cells[cell]
