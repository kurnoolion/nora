"""Eval Studio route tests (FR-39): sample CRUD, ground-truth add flows,
picker cascade, Stage-1 preview, curation chat, golden save.

Sira-query HTTP and the LLM are stubbed; sample storage and parse trees
run for real on tmp_path. Synthetic fixtures only (NFR-8).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import core.src.web.routes.golden_eval as ge
from core.src.eval.golden import GoldenSample, GroundTruthEntry, load_sample, sample_path, save_sample
from core.src.web.app import app


def _write_tree(env_dir: Path, mno: str, rel: str, doc_id: str, tree: dict):
    d = env_dir / "out" / "parse" / mno / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{doc_id}_tree.json").write_text(json.dumps(tree), encoding="utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import core.src.web.app as app_mod
    monkeypatch.setattr(app_mod.config, "env_dir", str(tmp_path))
    _write_tree(tmp_path, "mno-a", "Jan2026", "docA", {
        "plan_id": "PLAN_X",
        "requirements": [
            {"req_id": "REQ_FOO_0001", "title": "Root rule",
             "text": "Widgets shall retry."},
            {"req_id": "REQ_FOO_0002", "title": "Backoff rule",
             "text": "Retries use exponential backoff."},
        ],
    })
    _write_tree(tmp_path, "mno-a", "Apr2026", "docA", {
        "plan_id": "PLAN_X",
        "requirements": [{"req_id": "REQ_FOO_0001", "title": "Root rule",
                          "text": "Widgets shall retry."}],
    })
    return TestClient(app)


def _create(client, query="widget retry?"):
    r = client.post("/api/eval-studio/sample", data={
        "query": query, "area": "retry", "created_by": "expert-a",
    })
    assert r.status_code == 200
    return r


class TestPageAndBoard:
    def test_page_renders(self, client):
        r = client.get("/eval-studio")
        assert r.status_code == 200
        assert "Eval Studio" in r.text

    def test_board_empty_then_lists(self, client, tmp_path):
        assert "No samples yet" in client.get("/api/eval-studio/samples").text
        _create(client)
        board = client.get("/api/eval-studio/samples").text
        assert "gs-0001" in board and "draft" in board


class TestSampleCrud:
    def test_create_and_edit_meta(self, client, tmp_path):
        _create(client)
        r = client.post("/api/eval-studio/sample/gs-0001/meta", data={
            "query": "updated query?", "area": "roaming",
        })
        assert r.status_code == 200
        s = load_sample(sample_path(tmp_path, "gs-0001"))
        assert s.query == "updated query?" and s.area == "roaming"

    def test_status_transition_gated_by_validation(self, client, tmp_path):
        _create(client)
        r = client.post("/api/eval-studio/sample/gs-0001/status",
                        data={"status": "stage1-ready"})
        assert "requires ground_truth" in r.text
        assert load_sample(sample_path(tmp_path, "gs-0001")).status == "draft"

    def test_draft_delete_open_to_experts(self, client, tmp_path, monkeypatch):
        _create(client)
        monkeypatch.setattr(ge, "is_admin", lambda request: False)
        r = client.post("/api/eval-studio/sample/gs-0001/delete")
        assert r.status_code == 200
        assert not sample_path(tmp_path, "gs-0001").exists()

    def test_promoted_delete_requires_admin(self, client, tmp_path, monkeypatch):
        _create(client)
        client.post("/api/eval-studio/sample/gs-0001/gt/add",
                    data={"req_id": "REQ_FOO_0002"})
        client.post("/api/eval-studio/sample/gs-0001/status",
                    data={"status": "stage1-ready"})
        monkeypatch.setattr(ge, "is_admin", lambda request: False)
        r = client.post("/api/eval-studio/sample/gs-0001/delete")
        assert r.status_code == 403
        assert sample_path(tmp_path, "gs-0001").exists()
        monkeypatch.setattr(ge, "is_admin", lambda request: True)
        client.post("/api/eval-studio/sample/gs-0001/delete")
        assert not sample_path(tmp_path, "gs-0001").exists()

    def test_editor_shows_chat_system_prompt(self, client):
        _create(client)
        client.post("/api/eval-studio/sample/gs-0001/gt/add",
                    data={"req_id": "REQ_FOO_0002"})
        r = client.get("/api/eval-studio/sample/gs-0001")
        assert "QUESTION: widget retry?" in r.text
        assert "Retries use exponential backoff." in r.text  # GT text visible

    def test_missing_sample_is_warning(self, client):
        r = client.get("/api/eval-studio/sample/gs-9999")
        assert "not found" in r.text


class TestGroundTruth:
    def test_picker_add_fully_qualified(self, client, tmp_path):
        _create(client)
        r = client.post("/api/eval-studio/sample/gs-0001/gt/add", data={
            "req_id": "REQ_FOO_0002", "mno": "mno-a", "release": "Jan2026",
            "plan": "PLAN_X", "source": "picker",
        })
        assert r.status_code == 200
        e = load_sample(sample_path(tmp_path, "gs-0001")).ground_truth[0]
        assert (e.mno, e.release, e.plan, e.source) == (
            "mno-a", "Jan2026", "PLAN_X", "picker")

    def test_direct_add_autoqualifies_unique(self, client, tmp_path):
        _create(client)
        client.post("/api/eval-studio/sample/gs-0001/gt/add",
                    data={"req_id": "REQ_FOO_0002"})
        e = load_sample(sample_path(tmp_path, "gs-0001")).ground_truth[0]
        assert (e.mno, e.release, e.plan) == ("mno-a", "Jan2026", "PLAN_X")

    def test_direct_add_ambiguous_picks_latest(self, client, tmp_path):
        # REQ_FOO_0001 lives in Jan2026 and Apr2026 — auto-pick the latest
        # revision and note it, rather than erroring on the ambiguity.
        _create(client)
        r = client.post("/api/eval-studio/sample/gs-0001/gt/add",
                        data={"req_id": "REQ_FOO_0001"})
        assert "matched 2 cells" in r.text and "added latest (Apr2026)" in r.text
        gt = load_sample(sample_path(tmp_path, "gs-0001")).ground_truth
        assert [(e.req_id, e.release) for e in gt] == [("REQ_FOO_0001", "Apr2026")]

    def test_unknown_id_rejected(self, client, tmp_path):
        _create(client)
        r = client.post("/api/eval-studio/sample/gs-0001/gt/add",
                        data={"req_id": "REQ_NOPE_9999"})
        assert "Not found: REQ_NOPE_9999" in r.text
        assert load_sample(sample_path(tmp_path, "gs-0001")).ground_truth == []

    def test_direct_add_bulk_split(self, client, tmp_path):
        # One field, several ids separated by commas/spaces — add all in one go.
        _create(client)
        r = client.post("/api/eval-studio/sample/gs-0001/gt/add",
                        data={"req_id": "REQ_FOO_0002, REQ_FOO_0001"})
        assert "Added 2" in r.text
        gt = load_sample(sample_path(tmp_path, "gs-0001")).ground_truth
        assert {e.req_id for e in gt} == {"REQ_FOO_0001", "REQ_FOO_0002"}

    def test_duplicate_add_rejected_and_remove(self, client, tmp_path):
        _create(client)
        for _ in range(2):
            r = client.post("/api/eval-studio/sample/gs-0001/gt/add",
                            data={"req_id": "REQ_FOO_0002"})
        assert "1 already present" in r.text
        client.post("/api/eval-studio/sample/gs-0001/gt/remove", data={
            "req_id": "REQ_FOO_0002", "mno": "mno-a", "release": "Jan2026",
        })
        assert load_sample(sample_path(tmp_path, "gs-0001")).ground_truth == []


class TestBulkAdd:
    def test_bulk_add_and_duplicate_skip(self, client, tmp_path):
        _create(client)
        r = client.post("/api/eval-studio/sample/gs-0001/gt/add-bulk", data={
            "req_ids": ["REQ_FOO_0001", "REQ_FOO_0002"],
            "mno": "mno-a", "release": "Jan2026", "plan": "PLAN_X",
        })
        assert r.status_code == 200 and "Added 2 requirement(s)" in r.text
        s = load_sample(sample_path(tmp_path, "gs-0001"))
        assert [(e.req_id, e.source) for e in s.ground_truth] == [
            ("REQ_FOO_0001", "picker"), ("REQ_FOO_0002", "picker")]
        r = client.post("/api/eval-studio/sample/gs-0001/gt/add-bulk", data={
            "req_ids": ["REQ_FOO_0001"],
            "mno": "mno-a", "release": "Jan2026", "plan": "PLAN_X",
        })
        assert "1 already present" in r.text
        assert len(load_sample(sample_path(tmp_path, "gs-0001")).ground_truth) == 2

    def test_bulk_add_empty_selection(self, client, tmp_path):
        _create(client)
        r = client.post("/api/eval-studio/sample/gs-0001/gt/add-bulk", data={
            "mno": "mno-a", "release": "Jan2026", "plan": "PLAN_X",
        })
        assert "No requirements selected" in r.text
        assert load_sample(sample_path(tmp_path, "gs-0001")).ground_truth == []

    def test_reqs_list_renders_selection_controls(self, client):
        reqs = client.get(
            "/api/eval-studio/picker/reqs?sid=gs-0001&mno=mno-a"
            "&plan=PLAN_X&release=Jan2026").text
        assert 'name="req_ids"' in reqs and "es-req-selall" in reqs
        assert "Add selected" in reqs


class TestPicker:
    def test_cascade(self, client):
        plans = client.get("/api/eval-studio/picker/plans?mno=mno-a").text
        assert "PLAN_X" in plans
        rels = client.get(
            "/api/eval-studio/picker/releases?mno=mno-a&plan=PLAN_X").text
        # Latest (Apr2026) preselected.
        assert 'value="Apr2026" selected' in rels.replace("  ", " ")
        reqs = client.get(
            "/api/eval-studio/picker/reqs?sid=gs-0001&mno=mno-a"
            "&plan=PLAN_X&release=Jan2026").text
        assert "REQ_FOO_0001" in reqs and "REQ_FOO_0002" in reqs
        filtered = client.get(
            "/api/eval-studio/picker/reqs?sid=gs-0001&mno=mno-a"
            "&plan=PLAN_X&release=Jan2026&filter=backoff").text
        assert "REQ_FOO_0002" in filtered and "REQ_FOO_0001" not in filtered


class TestPreview:
    def test_preview_renders_hits_misses_and_seeding(self, client, tmp_path, monkeypatch):
        _create(client)
        client.post("/api/eval-studio/sample/gs-0001/gt/add",
                    data={"req_id": "REQ_FOO_0002"})
        monkeypatch.setenv("NORA_SIRA_QUERY_URLS", "http://127.0.0.1:9999")
        from core.src.eval import golden_runner
        monkeypatch.setattr(golden_runner, "_post_json", lambda u, p, t: {
            "results": [
                {"rank": 1, "req_id": "REQ_FOO_0009"},
                {"rank": 2, "req_id": "REQ_FOO_0002"},
            ],
            "top_k": 10, "mode": "multi-cell", "resolved_cells": [],
        })
        r = client.post("/api/eval-studio/sample/gs-0001/preview")
        assert "recall 100%" in r.text
        assert "REQ_FOO_0002 @2" in r.text
        assert "REQ_FOO_0009" in r.text  # retrieval-assisted seeding block

    def test_preview_without_stack_warns(self, client, monkeypatch):
        _create(client)
        monkeypatch.delenv("NORA_SIRA_QUERY_URLS", raising=False)
        monkeypatch.delenv("NORA_SIRA_QUERY_URL", raising=False)
        r = client.post("/api/eval-studio/sample/gs-0001/preview")
        assert "No sira-query stack" in r.text


class _FakeLLM:
    def complete(self, prompt, system=None, temperature=0.0, max_tokens=None):
        assert "Retries use exponential backoff." in system  # GT context assembled
        assert "QUESTION: widget retry?" in system  # sample query in system prompt
        return "Draft golden answer."


class TestCuration:
    def test_empty_first_send_drafts_empty_refinement_warns(
            self, client, tmp_path, monkeypatch):
        _create(client)
        client.post("/api/eval-studio/sample/gs-0001/gt/add",
                    data={"req_id": "REQ_FOO_0002"})
        import core.src.web.routes.query as query_routes
        monkeypatch.setattr(
            query_routes, "_build_llm_from_env_or_default", lambda: _FakeLLM())
        # Empty message, empty history -> kickoff draft.
        r = client.post("/api/eval-studio/sample/gs-0001/chat",
                        data={"message": "", "history": "[]"})
        assert "Draft the golden answer." in r.text  # kickoff shown as user turn
        assert "Draft golden answer." in r.text      # LLM reply rendered
        # Empty message with history -> refinement guidance required.
        r = client.post("/api/eval-studio/sample/gs-0001/chat", data={
            "message": "",
            "history": json.dumps([
                {"role": "user", "text": "Draft the golden answer."},
                {"role": "assistant", "text": "Draft golden answer."},
            ]),
        })
        assert "refinements need guidance" in r.text

    def test_chat_and_golden_save(self, client, tmp_path, monkeypatch):
        _create(client)
        client.post("/api/eval-studio/sample/gs-0001/gt/add",
                    data={"req_id": "REQ_FOO_0002"})
        import core.src.web.routes.query as query_routes
        monkeypatch.setattr(
            query_routes, "_build_llm_from_env_or_default", lambda: _FakeLLM())
        r = client.post("/api/eval-studio/sample/gs-0001/chat", data={
            "message": "Draft an answer",
            "history": "[]",
        })
        assert "Draft golden answer." in r.text
        r = client.post("/api/eval-studio/sample/gs-0001/golden", data={
            "golden_response": "Widgets retry with exponential backoff.",
            "chat_turns": 2, "model": "local-model",
        })
        assert r.status_code == 200
        s = load_sample(sample_path(tmp_path, "gs-0001"))
        assert s.golden_response == "Widgets retry with exponential backoff."
        assert s.golden_meta["chat_turns"] == 2
        assert s.golden_meta["curated_at"]
        # Now golden-ready is reachable.
        client.post("/api/eval-studio/sample/gs-0001/gt/add",
                    data={"req_id": "REQ_FOO_0002"})  # keep GT non-empty (dup no-op)
        r = client.post("/api/eval-studio/sample/gs-0001/status",
                        data={"status": "golden-ready"})
        assert load_sample(sample_path(tmp_path, "gs-0001")).status == "golden-ready"

    def test_golden_edit_flag(self, client, tmp_path):
        _create(client)
        # Manual paste with no in-session draft counts as edited.
        client.post("/api/eval-studio/sample/gs-0001/golden",
                    data={"golden_response": "hand written", "generated": ""})
        assert load_sample(sample_path(tmp_path, "gs-0001")).golden_meta["edited"]
        # Accepting an LLM draft verbatim is not an edit.
        client.post("/api/eval-studio/sample/gs-0001/golden",
                    data={"golden_response": "the draft", "generated": "the draft"})
        s = load_sample(sample_path(tmp_path, "gs-0001"))
        assert s.golden_meta["edited"] is False
        # Tweaking a draft flags an edit and stamps edited_at.
        r = client.post("/api/eval-studio/sample/gs-0001/golden",
                        data={"golden_response": "the draft, tweaked",
                              "generated": "the draft"})
        s = load_sample(sample_path(tmp_path, "gs-0001"))
        assert s.golden_meta["edited"] is True and s.golden_meta.get("edited_at")
        # Response is the golden-card partial (OOB tab check), not the whole
        # editor — so the expert stays on the Golden tab.
        assert 'id="es-golden-check"' in r.text and 'hx-swap-oob="true"' in r.text
        assert "nav-tabs" not in r.text

    def test_meta_save_returns_partial(self, client, tmp_path):
        _create(client)
        r = client.post("/api/eval-studio/sample/gs-0001/meta",
                        data={"query": "updated?", "area": "roam"})
        # Only the meta card returns — no tab bar — so the active tab survives;
        # the board is refreshed (area shows there).
        assert "nav-tabs" not in r.text and "es-board-refresh" in r.text
        assert load_sample(sample_path(tmp_path, "gs-0001")).area == "roam"

    def test_meta_saves_mno_and_board_filters(self, client, tmp_path):
        _create(client)  # gs-0001
        _create(client)  # gs-0002
        client.post("/api/eval-studio/sample/gs-0001/meta",
                    data={"query": "q?", "area": "", "mno": "mno-a"})
        assert load_sample(sample_path(tmp_path, "gs-0001")).mno == "mno-a"
        # Unfiltered board lists both; filtered to mno-a lists only gs-0001.
        both = client.get("/api/eval-studio/samples").text
        assert "gs-0001" in both and "gs-0002" in both
        only = client.get("/api/eval-studio/samples", params={"mno": "mno-a"}).text
        assert "gs-0001" in only and "gs-0002" not in only
