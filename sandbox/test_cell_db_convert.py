"""Tests for sandbox/cell_db_convert.py — label flat files -> cell DBs.

Synthetic, content-free corpora only. Requires bm25x (sandbox venv);
every test skips cleanly where it is unavailable.

Run from the repo root:  pytest sandbox/test_cell_db_convert.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

bm25x = pytest.importorskip("bm25x")

from sandbox.cell_db_convert import (  # noqa: E402
    ConvertError,
    _sha256_files,
    _verify,
    convert,
)

DOCS = [
    ("REQ-0", "emergency callback", "the device shall support emergency callback mode"),
    ("REQ-1", "lock screen", "emergency calls shall bypass the device lock screen"),
    ("REQ-2", "registration", "the handset must register on the preferred network"),
    ("REQ-3", "roaming", "data roaming shall be disabled by default on first boot"),
]


def make_cell(db_root: Path, mno: str, release: str,
              docs=DOCS, enrich: dict[str, list[str]] | None = None) -> Path:
    base = db_root / f"{mno}__{release}"
    (base / "raw").mkdir(parents=True)
    (base / "raw" / "metadata.json").write_text("{}", encoding="utf-8")
    with open(base / "raw" / "corpus.jsonl", "w", encoding="utf-8") as f:
        for rid, title, text in docs:
            f.write(json.dumps({"_id": rid, "title": title, "text": text}) + "\n")
    index_dir = base / "index" / "best"
    index_dir.mkdir(parents=True)
    b = bm25x.BM25()
    b.disable_auto_save()
    b.add([text for _, _, text in docs])
    b.save(str(index_dir))
    if enrich is not None:
        enr = base / "enrichments" / "doc"
        enr.mkdir(parents=True)
        with open(enr / "best.jsonl", "w", encoding="utf-8") as f:
            for rid, phrases in enrich.items():
                f.write(json.dumps({"doc_id": rid, "phrases": phrases}) + "\n")
    return base


ENRICH = {"REQ-0": ["cbm", "callback service"], "REQ-2": ["attach", "registration"]}


def test_convert_end_to_end(tmp_path):
    db_root = tmp_path / "cells"
    make_cell(db_root, "mnoa", "Apr2098", enrich=ENRICH)
    out = tmp_path / "out"

    report = convert(db_root, out, verify_n=4, source_label="test-label")

    db_path = out / "cell-mnoa-Apr2098.db"
    assert db_path.exists()
    assert not list(out.glob(".tmp-*"))
    (cell_rep,) = report["cells"]
    assert cell_rep["verify"] == {"probes": 4, "k": 10, "mismatches": 0}
    assert cell_rep["n_docs"] == len(DOCS)
    assert cell_rep["enriched_docs"] == len(ENRICH)

    con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        rows = con.execute(
            "SELECT doc, req_id, title, text FROM corpus ORDER BY doc").fetchall()
        assert rows == [(i, rid, t, x) for i, (rid, t, x) in enumerate(DOCS)]
        enr = {rid: json.loads(ph) for rid, ph in
               con.execute("SELECT req_id, phrases FROM enrichment")}
        assert enr == ENRICH  # no overlay: effective == llm words
        meta = dict(con.execute("SELECT key, value FROM meta"))
        assert meta["schema_version"] == "1"
        assert meta["n_docs"] == str(len(DOCS))
        assert meta["source_label"] == "test-label"
        assert meta["enrich_run"] == "best"
        assert json.loads(meta["bm25x_ngrams"])
        # fingerprint must match the service's formula over the same files
        base = db_root / "mnoa__Apr2098"
        expect = _sha256_files([base / "raw" / "corpus.jsonl",
                                base / "enrichments" / "doc" / "best.jsonl"])
        assert meta["source_fingerprint"] == expect
        # FTS5 side: one row per doc; enrichment tokens searchable
        assert con.execute("SELECT count(*) FROM fts").fetchone()[0] == len(DOCS)
        hit = con.execute(
            "SELECT rowid FROM fts WHERE fts MATCH 'cbm'").fetchall()
        assert hit == [(0,)]
    finally:
        con.close()


def test_db_index_parity_with_flat_pipeline(tmp_path):
    """Acceptance sanity: an index loaded from DB blobs ranks identically
    to the flat pipeline (load index/best + enrich in memory) — built
    here independently of the converter's own code path."""
    db_root = tmp_path / "cells"
    base = make_cell(db_root, "mnoa", "Apr2098", enrich=ENRICH)
    out = tmp_path / "out"
    convert(db_root, out, verify_n=0)

    # Reference: the flat path, reconstructed by hand.
    ref = bm25x.BM25.load(str(base / "index" / "best"))
    ref.disable_auto_save()
    rid_to_idx = {rid: i for i, (rid, _, _) in enumerate(DOCS)}
    ref.enrich_batch([(rid_to_idx[rid], ph) for rid, ph in ENRICH.items()])

    # DB side: extract blobs (decoding the storage codec), load.
    import zlib
    con = sqlite3.connect(f"file:{out / 'cell-mnoa-Apr2098.db'}?mode=ro", uri=True)
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir()
    for fname, codec, data in con.execute(
            "SELECT filename, codec, data FROM index_blob"):
        raw = zlib.decompress(data) if codec == "zlib" else bytes(data)
        (blob_dir / fname).write_bytes(raw)
    con.close()
    got = bm25x.BM25.load(str(blob_dir))
    got.disable_auto_save()

    queries = [t for _, t, _ in DOCS] + ["cbm", "emergency callback"]
    expansions = [""] * len(DOCS) + ["callback service", ""]
    r = ref.search_with_expansion(queries, expansions, 4, 0.5)
    g = got.search_with_expansion(queries, expansions, 4, 0.5)
    assert [[d for d, _ in hits] for hits in r] == [[d for d, _ in hits] for hits in g]
    for rh, gh in zip(r, g):
        for (_, rs), (_, gs) in zip(rh, gh):
            assert rs == pytest.approx(gs, abs=1e-6)


def test_overlay_fold_baked(tmp_path):
    db_root = tmp_path / "cells"
    make_cell(db_root, "mnoa", "Apr2098", enrich=ENRICH)
    corr = tmp_path / "corrections"
    (corr / "sira-enrich").mkdir(parents=True)
    overlay = {
        "REQ-0": {"remove": [{"word": "cbm", "at": "t1"}],
                  "add": [{"word": "ecall", "at": "t1"}]},
    }
    (corr / "sira-enrich" / "mnoa.json").write_text(
        json.dumps(overlay), encoding="utf-8")

    out = tmp_path / "out"
    report = convert(db_root, out, verify_n=4, overlay_root=str(corr))

    con = sqlite3.connect(f"file:{out / 'cell-mnoa-Apr2098.db'}?mode=ro", uri=True)
    enr = {rid: json.loads(ph) for rid, ph in
           con.execute("SELECT req_id, phrases FROM enrichment")}
    meta = dict(con.execute("SELECT key, value FROM meta"))
    con.close()

    assert enr["REQ-0"] == ["callback service", "ecall"]  # cbm removed, ecall added
    assert enr["REQ-2"] == ENRICH["REQ-2"]                # untouched req unchanged
    assert meta["overlay_digest_baked"]
    counts = json.loads(meta["overlay_counts"])
    assert counts["removes"] == 1 and counts["adds"] == 1
    (cell_rep,) = report["cells"]
    assert cell_rep["overlay"] == counts


def test_cross_release_record_is_held(tmp_path):
    """An overlay record from another release whose req can't be verified
    there (req absent) is HELD — effective set unchanged. Mirrors the
    service's cross-release guard."""
    db_root = tmp_path / "cells"
    make_cell(db_root, "mnoa", "Apr2098", enrich=ENRICH)
    make_cell(db_root, "mnoa", "Aug2098", docs=DOCS[:2],
              enrich={"REQ-0": ["cbm", "callback service"]})
    corr = tmp_path / "corrections"
    (corr / "sira-enrich").mkdir(parents=True)
    overlay = {
        # origin release Aug2098, where REQ-2 does not exist: folding into
        # Apr2098 cannot verify the record there -> unknown -> HELD
        "REQ-2": {"remove": [{"word": "attach", "at": "t1",
                              "origin": {"release": "Aug2098"}}]},
        # Apr-origin record: same-release in Apr (applied); cross-release
        # in Aug where REQ-0 has identical text (jaccard 1.0 -> applied)
        "REQ-0": {"remove": [{"word": "cbm", "at": "t1",
                              "origin": {"release": "Apr2098"}}]},
    }
    (corr / "sira-enrich" / "mnoa.json").write_text(
        json.dumps(overlay), encoding="utf-8")

    out = tmp_path / "out"
    convert(db_root, out, verify_n=0, overlay_root=str(corr))

    con = sqlite3.connect(f"file:{out / 'cell-mnoa-Apr2098.db'}?mode=ro", uri=True)
    enr_apr = {rid: json.loads(ph) for rid, ph in
               con.execute("SELECT req_id, phrases FROM enrichment")}
    meta_apr = dict(con.execute("SELECT key, value FROM meta"))
    con.close()
    assert enr_apr["REQ-0"] == ["callback service"]   # same-release: applied
    assert enr_apr["REQ-2"] == ENRICH["REQ-2"]        # Aug-origin, unverifiable: held
    assert json.loads(meta_apr["overlay_counts"])["held"] == 1

    con = sqlite3.connect(f"file:{out / 'cell-mnoa-Aug2098.db'}?mode=ro", uri=True)
    enr_aug = {rid: json.loads(ph) for rid, ph in
               con.execute("SELECT req_id, phrases FROM enrichment")}
    con.close()
    # Apr-origin remove of cbm verifies OK in Aug (same text both releases)
    assert enr_aug["REQ-0"] == ["callback service"]


def test_existing_output_needs_force(tmp_path):
    db_root = tmp_path / "cells"
    make_cell(db_root, "mnoa", "Apr2098", enrich=ENRICH)
    out = tmp_path / "out"
    convert(db_root, out, verify_n=0)
    with pytest.raises(ConvertError) as ei:
        convert(db_root, out, verify_n=0)
    assert ei.value.code == "CNV-004"
    convert(db_root, out, verify_n=2, force=True)  # overwrite succeeds


def test_missing_corpus_is_cnv001(tmp_path):
    db_root = tmp_path / "cells"
    base = make_cell(db_root, "mnoa", "Apr2098")
    (base / "raw" / "corpus.jsonl").unlink()
    with pytest.raises(ConvertError) as ei:
        convert(db_root, tmp_path / "out", verify_n=0)
    assert ei.value.code == "CNV-001"


def test_no_cells_is_cnv009(tmp_path):
    (tmp_path / "cells").mkdir()
    with pytest.raises(ConvertError) as ei:
        convert(tmp_path / "cells", tmp_path / "out", verify_n=0)
    assert ei.value.code == "CNV-009"


def test_duplicate_doc_section_ids_convert(tmp_path):
    """Field finding (2026-10-10): real cells carry duplicate doc/section
    `_id` rows (`section:<plan>:<num>` emitted more than once). The flat
    stack serves them; the DB must keep every row positionally (the index
    blobs score all of them) with last-wins id-lookup semantics."""
    dup_docs = [
        ("doc:planx", "plan doc", "plan document preamble text"),
        ("section:planx:1", "s1", "first section body"),
        ("REQ-0", "emergency callback", "the device shall support emergency callback mode"),
        ("section:planx:1", "s1 again", "first section body repeated"),
        ("REQ-1", "lock screen", "emergency calls shall bypass the device lock screen"),
        ("doc:planx", "plan doc again", "plan document preamble repeated"),
    ]
    db_root = tmp_path / "cells"
    make_cell(db_root, "mnoa", "Apr2098", docs=dup_docs,
              enrich={"REQ-0": ["cbm"], "section:planx:1": ["marker phrase"]})
    out = tmp_path / "out"
    report = convert(db_root, out, verify_n=4)

    (cell_rep,) = report["cells"]
    assert cell_rep["n_docs"] == len(dup_docs)
    assert cell_rep["verify"]["mismatches"] == 0

    con = sqlite3.connect(f"file:{out / 'cell-mnoa-Apr2098.db'}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT doc, req_id, title, text FROM corpus ORDER BY doc").fetchall()
        assert rows == [(i, rid, t, x) for i, (rid, t, x) in enumerate(dup_docs)]
        # last-wins lookup semantics: max(doc) per req_id = the later row
        last = con.execute(
            "SELECT title FROM corpus WHERE req_id='section:planx:1' "
            "ORDER BY doc DESC LIMIT 1").fetchone()
        assert last == ("s1 again",)
    finally:
        con.close()


def test_verify_catches_blob_corruption(tmp_path):
    """Tampered blobs must fail verification, not serve quietly."""
    from sandbox.cell_db_convert import _load_cell, _fold_and_enrich

    db_root = tmp_path / "cells"
    base = make_cell(db_root, "mnoa", "Apr2098", enrich=ENRICH)
    out = tmp_path / "out"
    convert(db_root, out, verify_n=0)
    db_path = out / "cell-mnoa-Apr2098.db"

    con = sqlite3.connect(db_path)
    con.execute("UPDATE index_blob SET data = X'00' WHERE filename = "
                "(SELECT filename FROM index_blob ORDER BY length(data) DESC LIMIT 1)")
    con.commit()
    con.close()

    cd = _load_cell(base, ("mnoa", "Apr2098"), "", False)
    _fold_and_enrich(cd, {("mnoa", "Apr2098"): cd}, "")
    with pytest.raises(ConvertError) as ei:
        _verify(cd, db_path, [("emergency callback", "")])
    assert ei.value.code in ("CNV-006", "CNV-007")
