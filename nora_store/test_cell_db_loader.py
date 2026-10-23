"""Tests for the cell-db serving mode (corp-db-layer phase 2).

The identity gate in miniature: a service loading schema-2 cell DBs
(vanilla index + raw SIRA phrases, composed with the Overlay at load) must
return retrieval result sets IDENTICAL to the same service loading the flat
files — with and without review edits. Plus: healthz identity keys,
flag-gating (flat default untouched), Apply (reload) and label variants on
schema-2 cells, and the fts5 engine refusing a composed cell.

Synthetic data only. Requires bm25x + fastapi (sandbox venv); skips
cleanly where unavailable.

Run from the repo root:  pytest nora_store/test_cell_db_loader.py
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


def load_service(monkeypatch, db_root: Path, *, index="flat", engine="bm25x",
                 corrections: Path | None = None, expect_cells: bool = True):
    monkeypatch.setenv("NORA_STORE_ROOT", str(db_root))
    monkeypatch.setenv("NORA_STORE_INDEX", index)
    monkeypatch.setenv("NORA_STORE_ENGINE", engine)
    if corrections is not None:
        monkeypatch.setenv("NORA_STORE_CORRECTIONS_ROOT", str(corrections))
    else:
        monkeypatch.delenv("NORA_STORE_CORRECTIONS_ROOT", raising=False)
    monkeypatch.delenv("NORA_SIRA_USE_LATEST_RUNS", raising=False)
    import nora_store.service as service
    service = importlib.reload(service)
    service._load_cells()
    if expect_cells:
        assert service._cells, service._cells_load_error
    return service


def write_overlay(root: Path, mno: str, overlay: dict) -> Path:
    (root / "sira-enrich").mkdir(parents=True, exist_ok=True)
    (root / "sira-enrich" / f"{mno}.json").write_text(json.dumps(overlay), encoding="utf-8")
    return root


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
    assert health["cell_db_schema_version"] == "2"
    assert flat_health["index_mode"] == "flat"
    assert flat_health["engine"] == "flat"
    # identity parity: per-cell fingerprints equal the flat stack's
    # (same formula, recorded at convert time)
    assert svc._data_fingerprint_cells == flat_fp
    assert svc._data_fingerprint == svc_flat._data_fingerprint


def test_index_mode_and_engine_are_in_the_knob_tuple(monkeypatch, converted):
    """The eval StackStamp builds its comparability key from the healthz
    retrieval_knobs sub-dict, so a cell-db stack and a flat stack must not
    share a knob set."""
    _flat, out = converted
    knobs_db = load_service(
        monkeypatch, out, index="cell-db", engine="bm25x").healthz()["retrieval_knobs"]
    flat_knobs = load_service(monkeypatch, _flat, index="flat").healthz()["retrieval_knobs"]
    assert knobs_db["index_mode"] == "cell-db" and knobs_db["engine"] == "bm25x"
    assert flat_knobs["index_mode"] == "flat" and flat_knobs["engine"] == "flat"
    assert knobs_db != flat_knobs


def test_fts5_engine_refuses_a_composed_cell(monkeypatch, converted):
    """fts5 scores a baked FTS table; a schema-2 cell composes at load, so
    the cell fails to load loudly instead of serving vanilla-only scores."""
    _flat, out = converted
    svc = load_service(monkeypatch, out, index="cell-db", engine="fts5",
                       expect_cells=False)
    assert not svc._cells
    assert "fts5" in (svc._cells_load_error or "")


def test_identity_with_review_edits(tmp_path, monkeypatch, converted):
    """Flat and schema-2 cell-db stacks reading the SAME Overlay serve the
    same results: the edits are composed at load, not baked."""
    flat, out = converted
    corr = write_overlay(tmp_path / "corr", "mnoa", {
        "REQ-0": {"remove": [{"word": "cbm", "at": "t1"}],
                  "add": [{"word": "ecall", "at": "t1"}]}})
    ref = retrieve_all(load_service(monkeypatch, flat, index="flat", corrections=corr))
    svc = load_service(monkeypatch, out, index="cell-db", corrections=corr)
    got = retrieve_all(svc)
    for key in ref:
        assert [d for d, _ in ref[key]] == [d for d, _ in got[key]], key
    cell = ("mnoa", "Apr2098")
    assert svc._cells[cell].effective_words["REQ-0"] == ["callback service", "ecall"]
    assert svc._cells[cell].llm_words["REQ-0"] == ENRICH["REQ-0"]   # raw SIRA kept


def test_flat_default_untouched_and_bad_engine_loud(monkeypatch, converted):
    flat, out = converted
    # No env set beyond DB root: mode defaults to flat and loads flat dirs.
    monkeypatch.setenv("NORA_STORE_ROOT", str(flat))
    monkeypatch.delenv("NORA_STORE_INDEX", raising=False)
    monkeypatch.delenv("NORA_STORE_ENGINE", raising=False)
    import nora_store.service as service
    service = importlib.reload(service)
    assert service._INDEX_MODE == "flat"
    service._load_cells()
    assert service._cells

    # Unsupported engine value fails loudly, serving nothing quietly.
    svc = None
    monkeypatch.setenv("NORA_STORE_ROOT", str(out))
    monkeypatch.setenv("NORA_STORE_INDEX", "cell-db")
    monkeypatch.setenv("NORA_STORE_ENGINE", "memory")
    svc = importlib.reload(service)
    svc._load_cells()
    assert not svc._cells
    assert "unsupported" in (svc._cells_load_error or "")


def test_apply_recomposes_a_schema2_cell(tmp_path, monkeypatch, converted):
    """Apply (reload) on a schema-2 cell re-reads the same DB file and folds
    in the CURRENT Overlay — corrections go live without a republish."""
    _flat, out = converted
    corr = write_overlay(tmp_path / "corr", "mnoa", {})
    svc = load_service(monkeypatch, out, index="cell-db", corrections=corr)
    cell = ("mnoa", "Apr2098")
    assert svc._cells[cell].effective_words["REQ-0"] == ENRICH["REQ-0"]

    write_overlay(corr, "mnoa", {"REQ-0": {"remove": [{"word": "cbm", "at": "t1"}]}})
    resp = svc.cell_reload(svc.cell_dirname(cell))
    assert resp["corrections"]["removes"] == 1
    assert svc._cells[cell].effective_words["REQ-0"] == ["callback service"]
    assert svc._cells[cell].composed and svc._cells[cell].db_path


def test_label_variant_builds_on_a_schema2_cell(tmp_path, monkeypatch, converted):
    _flat, out = converted
    corr = write_overlay(tmp_path / "corr", "mnoa", {
        "REQ-0": {"remove": [{"word": "cbm", "at": "t1", "label": "expert-x"}]}})
    svc = load_service(monkeypatch, out, index="cell-db", corrections=corr)
    cell = ("mnoa", "Apr2098")
    variant = svc._get_variant(cell, "expert-x", build=True)
    assert variant is not svc._cells[cell]
    assert variant.effective_words["REQ-0"] == ["callback service"]   # label view
    assert svc._cells[cell].effective_words["REQ-0"] == ENRICH["REQ-0"]  # main view
