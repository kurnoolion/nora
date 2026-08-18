"""Serving-identity tests (strand golden-eval).

The /healthz identity keys — serve_label, data_fingerprint,
code_version, sira_prompt_scheme — feed the golden-eval StackStamp.
The fingerprint is computed at LOAD time from the bytes actually
served (per-cell corpus + applied enrichment phrases); label and
scheme come from the promote-time MANIFEST.json with env overrides.
"""

from __future__ import annotations

import json
from pathlib import Path

import sandbox.sira_query.service as svc
from sandbox.sira_cells import CellKey
from sandbox.sira_query.service import CellState

CELL_A: CellKey = ("AAA", "May2030")
CELL_B: CellKey = ("BBB", "Aug2031")


def _mk_cell_dir(db_root: Path, dirname: str, corpus_bytes: bytes,
                 phrases_bytes: bytes | None = None) -> Path | None:
    raw = db_root / dirname / "raw"
    raw.mkdir(parents=True)
    (raw / "corpus.jsonl").write_bytes(corpus_bytes)
    if phrases_bytes is None:
        return None
    run = db_root / dirname / "runs" / "doc-enrich" / "r1"
    run.mkdir(parents=True)
    p = run / "enrichments.kept.jsonl"
    p.write_bytes(phrases_bytes)
    return p


def _cell_state(cell: CellKey, phrases: Path | None) -> CellState:
    return CellState(
        cell=cell, bm25=object(), doc_ids=[], doc_id_to_idx={},
        corpus_by_id={}, max_df=1,
        doc_enrich_source=str(phrases) if phrases else None,
    )


def _identity_setup(monkeypatch, db_root: Path, cells: dict) -> None:
    monkeypatch.setattr(svc, "_DB_ROOT", str(db_root))
    monkeypatch.setattr(svc, "_cells", cells)


class TestFingerprint:
    def test_deterministic_and_per_cell(self, tmp_path, monkeypatch):
        pa = _mk_cell_dir(tmp_path, "AAA__May2030", b"row1\n", b"ph1\n")
        pb = _mk_cell_dir(tmp_path, "BBB__Aug2031", b"row2\n", None)
        cells = {CELL_A: _cell_state(CELL_A, pa),
                 CELL_B: _cell_state(CELL_B, pb)}
        _identity_setup(monkeypatch, tmp_path, cells)
        svc._compute_identity()
        first = svc._data_fingerprint
        first_cells = dict(svc._data_fingerprint_cells)
        assert set(first_cells) == {"AAA__May2030", "BBB__Aug2031"}
        assert first and len(first) == 64
        svc._compute_identity()
        assert svc._data_fingerprint == first
        assert svc._data_fingerprint_cells == first_cells

    def test_corpus_change_changes_fingerprint(self, tmp_path, monkeypatch):
        pa = _mk_cell_dir(tmp_path, "AAA__May2030", b"row1\n", b"ph1\n")
        cells = {CELL_A: _cell_state(CELL_A, pa)}
        _identity_setup(monkeypatch, tmp_path, cells)
        svc._compute_identity()
        before = svc._data_fingerprint
        (tmp_path / "AAA__May2030" / "raw" / "corpus.jsonl").write_bytes(
            b"row1-edited\n")
        svc._compute_identity()
        assert svc._data_fingerprint != before

    def test_enrichment_change_changes_fingerprint(self, tmp_path, monkeypatch):
        pa = _mk_cell_dir(tmp_path, "AAA__May2030", b"row1\n", b"ph1\n")
        cells = {CELL_A: _cell_state(CELL_A, pa)}
        _identity_setup(monkeypatch, tmp_path, cells)
        svc._compute_identity()
        with_phrases = svc._data_fingerprint
        pa.write_bytes(b"ph2\n")
        svc._compute_identity()
        assert svc._data_fingerprint != with_phrases
        # absent phrases hash distinctly too (vanilla BM25 is an identity)
        cells[CELL_A].doc_enrich_source = None
        svc._compute_identity()
        assert svc._data_fingerprint not in ("", with_phrases)


class TestManifestAndEnv:
    def test_manifest_read_from_label_root(self, tmp_path, monkeypatch):
        # promote.sh writes MANIFEST.json at the label root; the service
        # mounts the label's sira/ subdir as db_root.
        label_root = tmp_path / "labelX"
        db_root = label_root / "sira"
        db_root.mkdir(parents=True)
        (label_root / "MANIFEST.json").write_text(json.dumps({
            "label": "labelX", "sira_prompt_scheme": "scheme-v2",
            "repo_git_sha": "abc1234", "promoted_at": "2030-01-01T00:00:00Z",
        }), encoding="utf-8")
        monkeypatch.setattr(svc, "_DB_ROOT", str(db_root))
        m = svc._read_serve_manifest()
        assert m["label"] == "labelX"
        assert m["sira_prompt_scheme"] == "scheme-v2"

    def test_manifest_absent_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(svc, "_DB_ROOT", str(tmp_path))
        assert svc._read_serve_manifest() == {}

    def test_code_version_env_wins(self, monkeypatch):
        monkeypatch.setenv("NORA_CODE_VERSION", "img-sha-42")
        assert svc._code_version() == "img-sha-42"

    def test_healthz_carries_identity_keys(self, tmp_path, monkeypatch):
        pa = _mk_cell_dir(tmp_path, "AAA__May2030", b"row1\n", b"ph1\n")
        cells = {CELL_A: _cell_state(CELL_A, pa)}
        _identity_setup(monkeypatch, tmp_path, cells)
        monkeypatch.setattr(svc, "_serve_manifest", {})
        monkeypatch.setenv("NORA_CODE_VERSION", "img-sha-42")
        monkeypatch.setenv("SIRA_PROMPT_SCHEME", "scheme-v1")
        monkeypatch.setenv("NORA_SERVE_LABEL", "labelY")
        svc._compute_identity()   # no manifest on disk → env fallbacks
        body = svc.healthz()
        assert body["data_fingerprint"] == svc._data_fingerprint
        assert body["data_fingerprint_cells"] == svc._data_fingerprint_cells
        assert body["code_version"] == "img-sha-42"
        assert body["sira_prompt_scheme"] == "scheme-v1"
        assert body["serve_label"] == "labelY"
        assert body["serve_manifest"] == {}
        # The owned knob sub-dict is the stamp's comparability contract.
        knobs = body["retrieval_knobs"]
        assert isinstance(knobs, dict) and "default_top_k" in knobs
        assert "rerank_enabled" in knobs and "expansion_weight" in knobs

    def test_healthz_scheme_env_overrides_manifest(self, tmp_path, monkeypatch):
        _identity_setup(monkeypatch, tmp_path, {})
        monkeypatch.setattr(svc, "_serve_manifest", {
            "label": "labelZ", "sira_prompt_scheme": "scheme-v2",
            "repo_git_sha": "abc", "promoted_at": "t",
        })
        monkeypatch.setenv("SIRA_PROMPT_SCHEME", "scheme-override")
        monkeypatch.delenv("NORA_SERVE_LABEL", raising=False)
        body = svc.healthz()
        assert body["serve_label"] == "labelZ"
        assert body["sira_prompt_scheme"] == "scheme-override"
        assert body["serve_manifest"] == {
            "repo_git_sha": "abc", "promoted_at": "t"}
