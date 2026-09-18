"""Tests for the req-bubble endpoint and its wiring (strand req-id-bubbles).

Covers the three things that can break the feature outside the renderer:
  - `_bubble_req_ids` builds the same set for the nora and sira lanes
    (they are exercised on different machines, so parity has to be
    structural rather than observed)
  - `GET /api/req/{req_id}` resolves through the shared req_tree helpers
  - `_answer.html` still renders from the call sites that pass no
    `bubble_req_ids` at all
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from core.src.web.routes import playground


# ── Anchor set ─────────────────────────────────────────────────


class TestBubbleReqIds:
    def test_union_of_chunks_and_citations(self):
        result = {
            "rag_chunks": [{"req_id": "A_1"}, {"req_id": "A_2"}],
            "llm_citations": [{"req_id": "A_2"}, {"req_id": "A_3"}],
        }
        assert playground._bubble_req_ids(result) == ["A_1", "A_2", "A_3"]

    def test_nora_and_sira_lane_shapes_produce_the_same_set(self):
        """Lane parity is the whole design: nora gets rag_chunks from the
        query pipeline, sira builds them from its packed results. Same key,
        so the same set — no branch on lane anywhere."""
        nora = {
            "rag_chunks": [{"req_id": "X_1", "similarity_score": 0.4}],
            "llm_citations": [{"req_id": "X_1", "plan_id": "P"}],
        }
        sira = {
            "rag_chunks": [{"req_id": "X_1", "text": "…", "plan_id": "VZW"}],
            "llm_citations": [{"req_id": "X_1", "llm_cited": True}],
        }
        assert playground._bubble_req_ids(nora) == playground._bubble_req_ids(sira)

    def test_sira_lane_ids_survive_empty_llm_citations(self):
        """nora's llm_citations come from a VZ_REQ_-only regex and go empty on
        other MNOs; retrieval-side ids must still carry the bubbles."""
        result = {"rag_chunks": [{"req_id": "REQ-TMO-5G-42"}], "llm_citations": []}
        assert playground._bubble_req_ids(result) == ["REQ-TMO-5G-42"]

    def test_tolerates_missing_and_malformed(self):
        assert playground._bubble_req_ids(None) == []
        assert playground._bubble_req_ids({}) == []
        assert playground._bubble_req_ids(
            {"rag_chunks": [None, {"req_id": ""}, "junk", {"no_id": 1}]}
        ) == []


# ── Endpoint ───────────────────────────────────────────────────


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """Minimal per-cell parse layout: <env>/out/parse/<mno>/<release>/<doc>_tree.json"""
    for release, text in (("Feb2026", "old text"), ("Jun2026", "new text")):
        d = tmp_path / "out" / "parse" / "VZW" / release
        d.mkdir(parents=True)
        (d / "PLANA_tree.json").write_text(json.dumps({
            "plan_id": "PLANA",
            "plan_name": "Plan A",
            "requirements": [
                {"req_id": "VZ_REQ_A_1", "title": "Band support", "text": text,
                 "section_number": "1.4.3"},
                # Table-anchored shape: no section_number of its own, linked
                # to its owning section by parent_section.
                {"req_id": "VZ_REQ_A_2", "title": "Anchored", "text": text,
                 "section_number": "", "parent_section": "1.4"},
                # Neither — the section line has nothing to say.
                {"req_id": "VZ_REQ_A_3", "title": "Bare", "text": text,
                 "section_number": "", "parent_section": ""},
            ],
        }))
        # Multi-plan doc: empty tree-level plan, per-req plan ids. Its reqs are
        # SECONDARY plans, which carry no document-level name.
        (d / "MULTI_tree.json").write_text(json.dumps({
            "plan_id": "",
            "plan_name": "Whole Document Name",
            "requirements": [
                {"req_id": "VZ_REQ_B_1", "title": "Secondary", "text": text,
                 "plan_id": "PLANB", "section_number": "9"},
            ],
        }))
    return tmp_path


@pytest.fixture()
def client(corpus: Path):
    from core.src.web.app import app, config

    with patch.object(type(config), "env_dir_path", lambda self: corpus):
        yield TestClient(app)


class TestReqEndpoint:
    def test_known_id_returns_the_requirement(self, client):
        r = client.get("/api/req/VZ_REQ_A_1")
        assert r.status_code == 200
        assert "VZ_REQ_A_1" in r.text

    def test_unknown_id_returns_404(self, client):
        r = client.get("/api/req/VZ_REQ_NOPE_9")
        assert r.status_code == 404
        assert "not found" in r.text.lower()

    def test_hit_is_browser_cacheable_briefly(self, client):
        """Collapses the repeats `once` cannot: duplicate badges for one req in
        the same answer, reloads, and the same req cited by another answer."""
        r = client.get("/api/req/VZ_REQ_A_1")
        assert r.headers["cache-control"] == "private, max-age=300"

    def test_miss_is_not_cached(self, client):
        """A 404 usually means the corpus is mid-rebuild — freezing that for
        five minutes would keep showing "not found" after it came back."""
        r = client.get("/api/req/VZ_REQ_NOPE_9")
        assert "cache-control" not in r.headers

    def test_id_in_several_releases_shows_the_latest(self, client):
        """D-207's latest-on-conflict contract, reused rather than re-invented."""
        r = client.get("/api/req/VZ_REQ_A_1")
        assert "new text" in r.text
        assert "2 releases" in r.text


# ── Panel metadata (strand req-bubble-metadata) ────────────────


class TestBubbleMetadata:
    def test_section_number_is_shown(self, client):
        r = client.get("/api/req/VZ_REQ_A_1")
        assert "1.4.3" in r.text

    def test_missing_section_number_falls_back_to_parent_section(self, client):
        """Table-anchored and leading-id-body reqs have `section_number=""` by
        design (parser MODULE.md) but do carry `parent_section` — rendering
        nothing would discard information the tree already holds."""
        r = client.get("/api/req/VZ_REQ_A_2")
        assert "1.4" in r.text

    def test_section_line_is_omitted_when_there_is_nothing_to_show(self, client):
        r = client.get("/api/req/VZ_REQ_A_3")
        assert "Section" not in r.text

    def test_primary_plan_shows_the_name_not_the_id(self, client):
        r = client.get("/api/req/VZ_REQ_A_1")
        assert "Plan A" in r.text
        assert "PLANA" not in r.text

    def test_secondary_plan_shows_its_id_not_the_document_name(self, client):
        """The document's name belongs to the tree's primary plan only. Showing
        it beside a secondary plan's requirement would label it wrongly."""
        r = client.get("/api/req/VZ_REQ_B_1")
        assert "PLANB" in r.text
        assert "Whole Document Name" not in r.text


# ── Regression: the templates that pass no ids ─────────────────


class TestAnswerTemplateWithoutIds:
    def test_answer_renders_when_bubble_req_ids_is_absent(self):
        """~10 call sites render _answer.html without the new key. Jinja
        Undefined must degrade to a plain answer, not raise."""
        from core.src.web.app import templates

        html = templates.get_template("test/_answer.html").render(
            request=None, root_path="", answer="Plain **answer** text.",
        )
        assert "answer-body" in html
        assert "req-bubble" not in html
