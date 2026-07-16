"""Tests for the merged-tab helpers in core/src/web/routes/playground.py:
citation flatten + lane_config snapshot (NORA + SIRA-pure path).

The async SIRA-fetch wrapper (`_snapshot_sira_lane_config`) is a thin
httpx layer over `_pick_sira_snapshot` — the pure picker carries the
substance and is tested directly.

Run: python -m pytest core/tests/test_playground_helpers.py
"""

from __future__ import annotations

import pytest

from core.src.web.routes.playground import (
    _SIRA_HEALTHZ_SNAPSHOT_KEYS,
    _flatten_cited_ids,
    _pick_sira_snapshot,
    _snapshot_nora_lane_config,
)


# ── _flatten_cited_ids ────────────────────────────────────────────────


def test_flatten_cited_ids_empty_input():
    assert _flatten_cited_ids([]) == []
    assert _flatten_cited_ids(None) == []


def test_flatten_cited_ids_extracts_req_ids_dedup_sorted():
    citations = [
        {"req_id": "R-Z", "title": "z"},
        {"req_id": "R-A", "title": "a"},
        {"req_id": "R-Z", "title": "duplicate"},
        {"req_id": "R-M"},
    ]
    assert _flatten_cited_ids(citations) == ["R-A", "R-M", "R-Z"]


def test_flatten_cited_ids_skips_malformed_entries():
    """Tolerant of mixed shapes — a single bad item shouldn't break the
    Ask's row-insertion path."""
    citations = [
        {"req_id": "R-OK"},
        None,
        {"title": "no req_id field"},
        {"req_id": None},
        {"req_id": ""},
        {"req_id": 42},          # non-string
        "not a dict",
        {"req_id": "R-ALSO-OK"},
    ]
    assert _flatten_cited_ids(citations) == ["R-ALSO-OK", "R-OK"]


# ── _pick_sira_snapshot ───────────────────────────────────────────────


def test_pick_sira_snapshot_keys_subset():
    """All known snapshot keys are pulled when present."""
    healthz = {k: f"v-{k}" for k in _SIRA_HEALTHZ_SNAPSHOT_KEYS}
    healthz["other_field"] = "ignored"
    healthz["ok"] = True
    snap = _pick_sira_snapshot(healthz)
    assert set(snap) == set(_SIRA_HEALTHZ_SNAPSHOT_KEYS)
    assert "other_field" not in snap


def test_pick_sira_snapshot_drops_missing_keys():
    """Keys absent from healthz are skipped (don't appear as None)."""
    healthz = {
        "corpus_size": 13974,
        "rerank_enabled": False,
        "query_enrich_enabled": False,
        "fanout_enabled": False,
    }
    snap = _pick_sira_snapshot(healthz)
    assert snap == {
        "corpus_size": 13974,
        "rerank_enabled": False,
        "query_enrich_enabled": False,
        "fanout_enabled": False,
    }


def test_pick_sira_snapshot_empty_healthz():
    assert _pick_sira_snapshot({}) == {}


def test_pick_sira_snapshot_preserves_value_types():
    """Booleans + numbers + nulls + strings round-trip — no JSON coercion
    in the picker itself; serialization happens at DB insert time."""
    healthz = {
        "corpus_size": 13974,
        "rerank_enabled": False,
        "expansion_weight": 0.0,
        "doc_enrich_run_pinned": "enrich-stable",
        "rerank_run_pinned": None,
    }
    snap = _pick_sira_snapshot(healthz)
    assert snap["corpus_size"] == 13974
    assert snap["rerank_enabled"] is False
    assert snap["expansion_weight"] == 0.0
    assert snap["doc_enrich_run_pinned"] == "enrich-stable"
    assert snap["rerank_run_pinned"] is None


# ── _snapshot_nora_lane_config ────────────────────────────────────────


def test_nora_snapshot_pulls_from_result_dict(monkeypatch):
    monkeypatch.delenv("NORA_LLM_MODEL", raising=False)
    monkeypatch.delenv("NORA_QUERY_RERANK_ENABLED", raising=False)
    monkeypatch.delenv("NORA_QUERY_BROAD_TOP_K", raising=False)
    monkeypatch.delenv("NORA_QUERY_NARROW_TOP_K", raising=False)
    monkeypatch.delenv("NORA_INCLUDE_PARENT_BODY", raising=False)
    result = {
        "llm_model": "internal-llm",
        "query_intent": "general",
        "candidate_count": 12,
        "answer": "...",       # extra fields ignored
    }
    snap = _snapshot_nora_lane_config(result)
    assert snap == {
        "llm_model": "internal-llm",
        "query_intent": "general",
        "candidate_count": 12,
    }


def test_nora_snapshot_includes_set_env_vars(monkeypatch):
    monkeypatch.setenv("NORA_LLM_MODEL", "internal-llm-v2")
    monkeypatch.setenv("NORA_QUERY_BROAD_TOP_K", "25")
    monkeypatch.setenv("NORA_QUERY_NARROW_TOP_K", "10")
    monkeypatch.delenv("NORA_QUERY_RERANK_ENABLED", raising=False)
    monkeypatch.delenv("NORA_INCLUDE_PARENT_BODY", raising=False)
    snap = _snapshot_nora_lane_config({"llm_model": "from-result"})
    assert snap["llm_model"] == "from-result"      # result dict wins for this field
    assert snap["NORA_LLM_MODEL"] == "internal-llm-v2"
    assert snap["NORA_QUERY_BROAD_TOP_K"] == "25"
    assert snap["NORA_QUERY_NARROW_TOP_K"] == "10"
    assert "NORA_QUERY_RERANK_ENABLED" not in snap
    assert "NORA_INCLUDE_PARENT_BODY" not in snap


def test_nora_snapshot_missing_result_fields_are_none(monkeypatch):
    """A query that didn't surface llm_model still gets a snapshot —
    the key exists with None so analysis can spot the gap."""
    monkeypatch.delenv("NORA_LLM_MODEL", raising=False)
    snap = _snapshot_nora_lane_config({})
    assert snap["llm_model"] is None
    assert snap["query_intent"] is None
    assert snap["candidate_count"] is None


# ── lane runner progress callbacks (SSE streaming endpoint feeds these) ──

# Both lane runners are async; tests use asyncio.run via a small helper
# so we don't pull in pytest-asyncio just for these few cases.


import asyncio
from unittest.mock import MagicMock, patch


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_fake_request():
    """Build a minimal Request stand-in that has .app + .app.state.
    The lane runners only reach into request.app.state through the
    inner pipeline calls (which we mock), so this is enough."""
    req = MagicMock()
    req.app = MagicMock()
    return req


def test_nora_lane_runner_emits_progress_on_start_and_done():
    from core.src.web.routes import playground as pg
    msgs: list[str] = []

    async def emit(m: str) -> None:
        msgs.append(m)

    # Stub _run_query_for_test to return a successful, well-shaped result.
    def _fake_run(q, app, pinned_chunk_ids=None):
        return {
            "answer": "OK",
            "rag_chunks": [{"req_id": "R-1"}, {"req_id": "R-2"}],
            "llm_citations": [{"req_id": "R-1"}],
            "candidate_count": 5,
            "llm_model": "m",
        }

    with patch.object(pg, "_run_query_for_test", _fake_run):
        out = _run(pg._run_nora_lane_for_merged(
            "q", _make_fake_request(), emit_progress=emit,
        ))

    # Start + done — two events on the happy path.
    assert len(msgs) == 2
    assert msgs[0].startswith("Running NORA hybrid pipeline")
    assert "NORA: 2 chunks retrieved" in msgs[1]
    # The output dict is still well-formed for downstream consumers.
    assert "result" in out
    assert out["retrieved_ids"] == ["R-1", "R-2"]


def test_nora_lane_runner_emits_progress_on_error():
    from core.src.web.routes import playground as pg
    msgs: list[str] = []

    async def emit(m: str) -> None:
        msgs.append(m)

    def _fake_run(q, app, pinned_chunk_ids=None):
        raise RuntimeError("boom")

    with patch.object(pg, "_run_query_for_test", _fake_run):
        out = _run(pg._run_nora_lane_for_merged(
            "q", _make_fake_request(), emit_progress=emit,
        ))

    # First event is the start, second names the error.
    assert len(msgs) == 2
    assert "NORA error" in msgs[1]
    assert "boom" in msgs[1]
    assert "error" in out


def test_nora_lane_runner_works_without_callback():
    """emit_progress is optional — runners must work when it's None."""
    from core.src.web.routes import playground as pg

    def _fake_run(q, app, pinned_chunk_ids=None):
        return {"answer": "OK", "rag_chunks": [], "llm_citations": []}

    with patch.object(pg, "_run_query_for_test", _fake_run):
        out = _run(pg._run_nora_lane_for_merged("q", _make_fake_request()))
    assert "result" in out


def test_sira_lane_runner_emits_progress_at_stage_boundaries():
    from core.src.web.routes import playground as pg
    msgs: list[str] = []

    async def emit(m: str) -> None:
        msgs.append(m)

    async def _fake_sira_call(question, top_k=None):
        return {
            "results": [
                {"req_id": "R-1", "rerank_score": 90, "bm25_score": 0.5},
                {"req_id": "R-2", "rerank_score": 60, "bm25_score": 0.4},
            ],
            "candidates_reranked": 2,
            "top_k": 2,
            "timings_ms": {"search_ms": 5},
            "rerank_call_stats": {},
            "notes": [],
        }

    def _fake_run_query(q, app, pinned_chunk_ids=None):
        return {"answer": "ok", "rag_chunks": [], "llm_citations": []}

    async def _fake_snapshot():
        return {"rerank_enabled": True}

    with patch.object(pg, "_call_sira_query", _fake_sira_call), \
         patch.object(pg, "_run_query_for_test", _fake_run_query), \
         patch.object(pg, "_snapshot_sira_lane_config", _fake_snapshot):
        out = _run(pg._run_sira_lane_for_merged(
            "q", _make_fake_request(), emit_progress=emit,
        ))

    # Expect: call SIRA → candidates reranked → running synth → done.
    # The exact count is 4 in the happy path.
    assert len(msgs) == 4
    assert msgs[0].startswith("Calling SIRA service")
    assert "candidates reranked" in msgs[1]
    assert msgs[2].startswith("Running NORA synthesizer")
    assert "SIRA: answer ready" in msgs[3]
    assert "result" in out


def test_sira_lane_runner_emits_progress_on_service_failure():
    from core.src.web.routes import playground as pg
    msgs: list[str] = []

    async def emit(m: str) -> None:
        msgs.append(m)

    async def _fail_sira(question, top_k=None):
        raise RuntimeError("SIRA down")

    with patch.object(pg, "_call_sira_query", _fail_sira):
        out = _run(pg._run_sira_lane_for_merged(
            "q", _make_fake_request(), emit_progress=emit,
        ))

    # Start event + error event = 2 messages on this path.
    assert len(msgs) == 2
    assert "SIRA service error" in msgs[1]
    assert "error" in out


# ── balanced pin selection (D-DRAFT-16) ───────────────────────────────

def _sira_row(mno, rid, score):
    return {"mno": mno, "release": "Feb2026", "req_id": rid, "rerank_score": score}


def test_balanced_pin_round_robins_across_cells():
    from core.src.web.routes import playground as pg
    # MNO-B out-scores MNO-A across the board (the real-world skew).
    results = (
        [_sira_row("MNO-B", f"b{i}", 90 - i) for i in range(5)]
        + [_sira_row("MNO-A", f"a{i}", 40 - i) for i in range(5)]
    )
    out = pg._balanced_pin(results, limit=6)
    # interleaved by cell, in-cell rerank order, capped at 6 → 3 per cell
    assert [r["req_id"] for r in out] == ["b0", "a0", "b1", "a1", "b2", "a2"]
    assert {r["mno"] for r in out} == {"MNO-A", "MNO-B"}


def test_balanced_pin_caps_total():
    from core.src.web.routes import playground as pg
    results = [_sira_row("MNO-B", f"b{i}", 90 - i) for i in range(20)]
    assert len(pg._balanced_pin(results, limit=8)) == 8        # single cell still capped


def test_select_pinned_balanced_mode_keeps_both_mnos(monkeypatch):
    from core.src.web.routes import playground as pg
    monkeypatch.setattr(pg, "_PIN_MODE", "balanced")
    monkeypatch.setattr(pg, "_PIN_MAX", 4)
    # In rerank-topk mode the rel-threshold would drop every MNO-A row (40 < 90×0.5);
    # balanced mode must still surface MNO-A.
    results = (
        [_sira_row("MNO-B", f"b{i}", 90 - i) for i in range(5)]
        + [_sira_row("MNO-A", f"a{i}", 40 - i) for i in range(5)]
    )
    pinned, max_score = pg._select_pinned_chunks(results)
    assert max_score == 90
    assert len(pinned) == 4
    assert {r["mno"] for r in pinned} == {"MNO-A", "MNO-B"}


def test_select_pinned_default_mode_score_filters(monkeypatch):
    from core.src.web.routes import playground as pg
    monkeypatch.setattr(pg, "_PIN_MODE", "rerank-topk")
    monkeypatch.setattr(pg, "_PIN_MIN_SCORE", 30)
    monkeypatch.setattr(pg, "_PIN_REL_THRESHOLD", 0.5)
    results = (
        [_sira_row("MNO-B", f"b{i}", 90 - i) for i in range(5)]
        + [_sira_row("MNO-A", f"a{i}", 40 - i) for i in range(5)]
    )
    pinned, max_score = pg._select_pinned_chunks(results)
    # rel_floor = 45; only MNO-B rows (90..86) clear it → default stays MNO-B-only,
    # which is exactly why balanced mode exists.
    assert {r["mno"] for r in pinned} == {"MNO-B"}


# ── select-synth helpers (LLM-select synthesis) ─────────────────────────────

def _cand(mno, rid, text, release="Feb2026"):
    return {"mno": mno, "release": release, "req_id": rid, "text": text}


def test_pack_select_synth_round_robins_and_respects_budget():
    from core.src.web.routes import playground as pg
    cands = (
        [_cand("MNO-B", f"b{i}", "x" * 100) for i in range(10)]
        + [_cand("MNO-A", f"a{i}", "y" * 100) for i in range(10)]
    )
    # char budget = token_budget × 3.5 ≈ 665 → fits 6 chunks (600 chars), not 7 (700)
    packed = pg._pack_select_synth(cands, token_budget=190)
    assert len(packed) == 6
    assert [c["req_id"] for c in packed] == ["b0", "a0", "b1", "a1", "b2", "a2"]
    assert {c["mno"] for c in packed} == {"MNO-A", "MNO-B"}


def test_pack_select_synth_keeps_at_least_one_when_first_chunk_exceeds_budget():
    from core.src.web.routes import playground as pg
    cands = [_cand("MNO-A", "a0", "z" * 100000)]      # one giant chunk
    packed = pg._pack_select_synth(cands, token_budget=10)    # tiny budget
    assert [c["req_id"] for c in packed] == ["a0"]      # never returns empty on a non-empty pool


def test_build_select_synth_context_groups_by_cell():
    from core.src.web.routes import playground as pg
    packed = [
        _cand("MNO-A", "a0", "alpha body"),
        _cand("MNO-B", "b0", "bravo body"),
    ]
    ctx = pg._build_select_synth_context("what bands?", packed)
    assert "USER QUESTION: what bands?" in ctx
    assert "OPERATOR: MNO-A | RELEASE: Feb2026" in ctx
    assert "OPERATOR: MNO-B | RELEASE: Feb2026" in ctx
    assert "req_id: a0" in ctx and "alpha body" in ctx


def test_select_synth_extract_citations_is_corpus_agnostic():
    from core.src.web.routes import playground as pg
    packed = [
        _cand("MNO-A", "VZ-FOO-12", "..."),       # not VZ_REQ_*; the old regex would miss it
        _cand("MNO-B", "ATT_BAR_3", "..."),
        _cand("MNO-B", "ATT_BAZ_9", "..."),        # not mentioned in answer
    ]
    answer = "Per VZ-FOO-12 and ATT_BAR_3, both operators require band n78."
    cites = pg._select_synth_extract_citations(answer, packed)
    assert [c["req_id"] for c in cites] == ["VZ-FOO-12", "ATT_BAR_3"]
    assert all(c["llm_cited"] for c in cites)


def test_filter_sira_notes_drops_rerank_disabled_in_select_synth(monkeypatch):
    import core.src.web.routes.playground as pg
    monkeypatch.setattr(pg, "_SELECT_SYNTH_ENABLED", True)
    notes = ["rerank disabled — round-robin per-cell balance (...)",
             "query-enrich failed (continuing): boom"]
    # rerank-off is intended in select-synth → drop that note; real failure stays
    assert pg._filter_sira_notes(notes) == ["query-enrich failed (continuing): boom"]


def test_filter_sira_notes_keeps_all_when_not_select_synth(monkeypatch):
    import core.src.web.routes.playground as pg
    monkeypatch.setattr(pg, "_SELECT_SYNTH_ENABLED", False)
    notes = ["rerank disabled — round-robin per-cell balance (...)", "other"]
    assert pg._filter_sira_notes(notes) == notes


# ── Ingested-corpus inventory (/api/test/ingested) ─────────────────


def test_count_cell_distinct_plans_and_requirements():
    from core.src.web.routes.playground import _count_cell
    metas = [
        {"plan_id": "GP1", "req_id": "REQ_1", "is_requirement": True},
        {"plan_id": "GP1", "req_id": "REQ_1", "is_requirement": True},   # dup chunk
        {"plan_id": "GP2", "req_id": "REQ_2", "is_requirement": True},
        {"plan_id": "GP2", "req_id": "SEC_9", "is_requirement": False},  # section node
        {"plan_id": "",    "req_id": "",     "is_requirement": True},    # structural
        {"plan_id": "GP3", "req_id": "REQ_3"},                            # legacy: no flag
    ]
    assert _count_cell(metas) == (3, 3)


def test_ingested_rows_per_cell_layout(tmp_path, monkeypatch):
    """Per-cell store dirs -> one row per (mno, release) with counts + date."""
    import core.src.web.routes.playground as pg

    class _FakeStore:
        def __init__(self, metas):
            self._metas = metas

        def get_all(self):
            class R:  # duck-typed QueryResult
                pass
            r = R()
            r.metadatas = self._metas
            return r

    root = tmp_path / "out" / "vectorstore"
    (root / "GP" / "Feb2026").mkdir(parents=True)
    (root / "GP" / "Feb2026" / "config.json").write_text("{}")

    fake_cells = {("GP", "Feb2026"): _FakeStore([
        {"plan_id": "P1", "req_id": "REQ_1", "is_requirement": True},
        {"plan_id": "P2", "req_id": "REQ_2", "is_requirement": True},
    ])}
    monkeypatch.setattr("core.src.vectorstore.cell_loader.load_cell_stores",
                        lambda r: fake_cells)

    class _Cfg:
        def env_dir_path(self):
            return tmp_path
    monkeypatch.setattr("core.src.web.app.config", _Cfg(), raising=False)

    pg._INGESTED_CACHE.update(key=None, rows=None)
    rows = pg._ingested_rows()
    assert len(rows) == 1
    r = rows[0]
    assert (r["mno"], r["release"], r["plans"], r["requirements"]) == ("GP", "Feb2026", 2, 2)
    assert len(r["ingested"]) == 10  # YYYY-MM-DD
    # second call hits the cache (same key, monkeypatched loader gone would fail otherwise)
    assert pg._ingested_rows() is rows


def test_ingested_rows_latest_tag_per_mno(tmp_path, monkeypatch):
    """Each MNO's newest MMMYYYY release gets latest=True; MMMYYYY ordering
    (Nov2025 < Feb2026), independent per MNO."""
    import core.src.web.routes.playground as pg

    class _FakeStore:
        def __init__(self, metas):
            self._metas = metas

        def get_all(self):
            class R:
                pass
            r = R()
            r.metadatas = self._metas
            return r

    root = tmp_path / "out" / "vectorstore"
    for mno, rel in [("GP", "Nov2025"), ("GP", "Feb2026"), ("XY", "Jul2026")]:
        (root / mno / rel).mkdir(parents=True)
        (root / mno / rel / "config.json").write_text("{}")

    meta = [{"plan_id": "P", "req_id": "REQ_1", "is_requirement": True}]
    fake_cells = {("GP", "Nov2025"): _FakeStore(meta),
                  ("GP", "Feb2026"): _FakeStore(meta),
                  ("XY", "Jul2026"): _FakeStore(meta)}
    monkeypatch.setattr("core.src.vectorstore.cell_loader.load_cell_stores",
                        lambda r: fake_cells)

    class _Cfg:
        def env_dir_path(self):
            return tmp_path
    monkeypatch.setattr("core.src.web.app.config", _Cfg(), raising=False)

    pg._INGESTED_CACHE.update(key=None, rows=None)
    tags = {(r["mno"], r["release"]): r["latest"] for r in pg._ingested_rows()}
    assert tags == {("GP", "Nov2025"): False, ("GP", "Feb2026"): True,
                    ("XY", "Jul2026"): True}


def test_ingested_rows_merges_sira_cells(tmp_path, monkeypatch):
    """Cells served only by the SIRA lane (no nora vectorstore) appear in
    the table from the query service's /cells; overlapping cells are
    tagged lane=both."""
    import core.src.web.routes.playground as pg

    class _FakeStore:
        def get_all(self):
            class R:
                pass
            r = R()
            r.metadatas = [{"plan_id": "P", "req_id": "REQ_1", "is_requirement": True}]
            return r

    root = tmp_path / "out" / "vectorstore"
    (root / "GP" / "Feb2026").mkdir(parents=True)
    (root / "GP" / "Feb2026" / "config.json").write_text("{}")
    monkeypatch.setattr("core.src.vectorstore.cell_loader.load_cell_stores",
                        lambda r: {("GP", "Feb2026"): _FakeStore()})

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"cells": [
                {"mno": "GP", "release": "Feb2026", "plans": 1,
                 "requirements": 1, "ingested": "2026-07-01"},
                {"mno": "ZZ", "release": "Jul2026", "plans": 3,
                 "requirements": 42, "ingested": "2026-07-14"},
            ]}
    monkeypatch.setattr(pg.httpx, "get", lambda url, timeout: _Resp())

    class _Cfg:
        def env_dir_path(self):
            return tmp_path
    monkeypatch.setattr("core.src.web.app.config", _Cfg(), raising=False)

    pg._INGESTED_CACHE.update(key=None, rows=None, at=0.0)
    rows = {(r["mno"], r["release"]): r for r in pg._ingested_rows()}
    assert rows[("GP", "Feb2026")]["lane"] == "both"
    sira_only = rows[("ZZ", "Jul2026")]
    assert (sira_only["lane"], sira_only["plans"], sira_only["requirements"],
            sira_only["ingested"], sira_only["latest"]) == ("sira", 3, 42, "2026-07-14", True)


def test_ingested_rows_both_lane_mismatch_keeps_both_counts(tmp_path, monkeypatch):
    """When NORA and SIRA counts disagree for the same cell (stale/partial
    vectorstore vs fresh SIRA corpus), both numbers survive to the row."""
    import core.src.web.routes.playground as pg

    class _FakeStore:
        def get_all(self):
            class R:
                pass
            r = R()
            r.metadatas = [{"plan_id": "", "req_id": "REQ_1", "is_requirement": True}]
            return r

    root = tmp_path / "out" / "vectorstore"
    (root / "GP" / "Feb2026").mkdir(parents=True)
    (root / "GP" / "Feb2026" / "config.json").write_text("{}")
    monkeypatch.setattr("core.src.vectorstore.cell_loader.load_cell_stores",
                        lambda r: {("GP", "Feb2026"): _FakeStore()})

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"cells": [{"mno": "GP", "release": "Feb2026", "plans": 87,
                               "requirements": 1200, "ingested": "2026-07-15"}]}
    monkeypatch.setattr(pg.httpx, "get", lambda url, timeout: _Resp())

    class _Cfg:
        def env_dir_path(self):
            return tmp_path
    monkeypatch.setattr("core.src.web.app.config", _Cfg(), raising=False)

    pg._INGESTED_CACHE.update(key=None, rows=None, at=0.0)
    (row,) = pg._ingested_rows()
    assert row["lane"] == "both" and row["mismatch"] is True
    assert (row["plans"], row["sira_plans"]) == (0, 87)
    assert (row["requirements"], row["sira_requirements"]) == (1, 1200)
