"""Pure-Python tests for the converter's overlay fold (no bm25x needed):
store-backed reads and the fingerprint short-circuit (strand corp-db-layer
S3). Synthetic, content-free data only."""

from __future__ import annotations

import json
from pathlib import Path

from core.src.stores.overlay import SqliteOverlayStore
from sandbox.cell_db_convert import CellData, _fold_and_enrich


class FakeBM25:
    def __init__(self):
        self.enriched = []

    def tokenize(self, text):
        return text.lower().split()

    def enrich_batch(self, items):
        self.enriched.extend(items)


def _cell(release, corpus, llm, fingerprints=None):
    ids = list(corpus)
    return CellData(
        cell=("mnoa", release), base=Path("."), doc_ids=ids,
        doc_id_to_idx={r: i for i, r in enumerate(ids)},
        rows=[(i, r, r, corpus[r]) for i, r in enumerate(ids)],
        bm25=FakeBM25(), llm_words=dict(llm), enrich_source=None,
        enrich_run_id="", enrich_model="",
        fingerprints=dict(fingerprints or {}))


def _cells(fp_cur):
    cur = _cell("Jan2031", {"R1": "alpha beta gamma"}, {"R1": ["w1", "w2"]},
                {"R1": fp_cur})
    old = _cell("Oct2030", {"R1": "totally other words"}, {})
    return cur, {cur.cell: cur, old.cell: old}


ENTRY = {"remove": [{"word": "w1", "at": "t1", "fingerprint": "fp-1",
                     "origin": {"release": "Oct2030"}}]}


def _json_root(tmp_path):
    d = tmp_path / "corr" / "sira-enrich"
    d.mkdir(parents=True)
    (d / "mnoa.json").write_text(json.dumps({"R1": ENTRY}))
    return str(tmp_path / "corr")


def test_matching_fingerprint_applies_despite_changed_text(tmp_path):
    cur, cells = _cells("fp-1")
    _fold_and_enrich(cur, cells, _json_root(tmp_path))
    assert cur.effective == {"R1": ["w2"]}
    assert cur.overlay_counts["removes"] == 1 and cur.overlay_counts["held"] == 0


def test_mismatched_fingerprint_is_held_by_jaccard(tmp_path):
    cur, cells = _cells("fp-other")
    _fold_and_enrich(cur, cells, _json_root(tmp_path))
    assert cur.effective == {"R1": ["w1", "w2"]}
    assert cur.overlay_counts["held"] == 1


def test_overlay_db_matches_json_root(tmp_path):
    db = tmp_path / "overlay.db"
    SqliteOverlayStore(db).put_entry("mnoa", "R1", ENTRY)
    a, cells_a = _cells("fp-1")
    _fold_and_enrich(a, cells_a, _json_root(tmp_path))
    b, cells_b = _cells("fp-1")
    _fold_and_enrich(b, cells_b, "", overlay_db=str(db))
    assert (a.effective, a.overlay_counts, a.overlay_digest) == \
        (b.effective, b.overlay_counts, b.overlay_digest)


def test_no_overlay_source_is_vanilla(tmp_path, monkeypatch):
    monkeypatch.setenv("NORA_OVERLAY_DB", str(tmp_path / "ignored.db"))
    cur, cells = _cells("fp-1")
    _fold_and_enrich(cur, cells, "")
    assert cur.effective == {"R1": ["w1", "w2"]}
