"""Tests for chunk_role classification and parent-body prepending.

Both features added under the nora-retrieval-parent-displacement strand:

- Marker classification (heuristic) tags chunks whose body is empty or
  effectively empty AND whose title is short + non-assertion as
  `chunk_role="marker"`. Marker chunks stay in the vectorstore (citation
  reachability preserved) but are excluded from BM25 + dense retrieval.

- Parent-body prepending (Tier-1) adds `[Parent context: <parent_body>]`
  to a leaf chunk's text when the parent requirement has a non-empty
  body. Bridges the asymmetry where parent body was only visible at
  synthesis time, not at indexing time.
"""

from __future__ import annotations

import csv

import pytest

from core.src.vectorstore.chunk_builder import (
    CHUNK_ROLE_MARKER,
    CHUNK_ROLE_PRIMARY,
    ChunkBuilder,
)
from core.src.vectorstore.config import VectorStoreConfig


def _config(**overrides) -> VectorStoreConfig:
    base = dict(
        include_mno_header=False,
        include_hierarchy_path=False,
        include_req_id=False,
        include_tables=False,
        include_image_context=False,
        include_children_titles=False,
        include_parent_body=True,
        skip_uninformative_headers=True,
    )
    base.update(overrides)
    return VectorStoreConfig(**base)


def _req(
    req_id: str,
    title: str = "",
    text: str = "",
    parent_req_id: str = "",
    section_number: str = "1.1",
) -> dict:
    return {
        "req_id": req_id,
        "title": title,
        "text": text,
        "section_number": section_number,
        "parent_req_id": parent_req_id,
        "parent_section": "",
        "hierarchy_path": [],
        "zone_type": "",
        "priority": "",
        "applicability": [],
        "tables": [],
        "images": [],
        "children": [],
        "cross_references": {},
    }


def _tree(reqs: list[dict]) -> dict:
    return {
        "mno": "VZW",
        "release": "OA-test",
        "plan_id": "TESTPLAN",
        "plan_name": "Test Plan",
        "version": "1",
        "requirements": reqs,
        "definitions_map": {},
        "definitions_section_number": "",
    }


# ── Classifier ──────────────────────────────────────────────────────


class TestChunkRoleClassifier:
    def test_empty_body_short_marker_title(self):
        cb = ChunkBuilder(_config())
        role, reason = cb._classify_chunk_role("5.1.1 5G SA bands", "")
        assert role == CHUNK_ROLE_MARKER
        assert "uninformative" in reason

    def test_empty_body_assertion_in_title(self):
        cb = ChunkBuilder(_config())
        role, reason = cb._classify_chunk_role(
            "Device shall support n41 band", "",
        )
        assert role == CHUNK_ROLE_PRIMARY
        assert "normative" in reason

    def test_empty_body_long_title_keeps(self):
        cb = ChunkBuilder(_config())
        role, _ = cb._classify_chunk_role(
            "Section 7.1.2.3 covers throttling for transient PLMN outages",
            "",
        )
        assert role == CHUNK_ROLE_PRIMARY

    def test_non_empty_body_always_primary(self):
        cb = ChunkBuilder(_config())
        role, _ = cb._classify_chunk_role(
            "1.1 Some heading",  # marker-ish title
            "The device shall do X.",
        )
        assert role == CHUNK_ROLE_PRIMARY

    @pytest.mark.parametrize("placeholder", [
        "VOID", "TBD", "TBA", "N/A", "NA", "None", "none",
        "No requirements", "Reserved", "Deleted", "()",
    ])
    def test_effectively_empty_treated_as_empty(self, placeholder):
        cb = ChunkBuilder(_config())
        role, _ = cb._classify_chunk_role("1.1 short", placeholder)
        assert role == CHUNK_ROLE_MARKER, (
            f"body={placeholder!r} should be effectively empty"
        )

    def test_effectively_empty_with_assertion_title_keeps(self):
        cb = ChunkBuilder(_config())
        role, reason = cb._classify_chunk_role(
            "Device shall throttle on PLMN reject", "VOID",
        )
        assert role == CHUNK_ROLE_PRIMARY
        assert "normative" in reason


# ── chunk_role metadata flows through to Chunk ──────────────────────


class TestChunkRoleMetadata:
    def test_marker_chunk_metadata_role(self):
        cb = ChunkBuilder(_config())
        tree = _tree([
            _req(req_id="VZ_REQ_TESTPLAN_1", title="5.1 5G SA bands"),
        ])
        chunks = cb.build_chunks([tree])
        assert len(chunks) == 1
        assert chunks[0].metadata["chunk_role"] == CHUNK_ROLE_MARKER

    def test_primary_chunk_metadata_role(self):
        cb = ChunkBuilder(_config())
        tree = _tree([
            _req(
                req_id="VZ_REQ_TESTPLAN_1",
                title="5.1 Mandatory bands",
                text="The device shall support n41, n78, and n260.",
            ),
        ])
        chunks = cb.build_chunks([tree])
        assert len(chunks) == 1
        assert chunks[0].metadata["chunk_role"] == CHUNK_ROLE_PRIMARY

    def test_skip_disabled_all_primary(self):
        cb = ChunkBuilder(_config(skip_uninformative_headers=False))
        tree = _tree([
            _req(req_id="VZ_REQ_TESTPLAN_1", title="5.1 5G SA bands"),
        ])
        chunks = cb.build_chunks([tree])
        assert chunks[0].metadata["chunk_role"] == CHUNK_ROLE_PRIMARY


# ── Parent body prepending (Tier-1) ─────────────────────────────────


class TestParentBodyPrepending:
    def test_parent_body_appears_in_leaf(self):
        cb = ChunkBuilder(_config())
        tree = _tree([
            _req(
                req_id="PARENT",
                title="Mandatory bands",
                text="Device shall support the following mandatory 5G NR bands.",
            ),
            _req(
                req_id="LEAF",
                title="FR1 bands",
                text="n41 and n78 are mandatory FR1 bands.",
                parent_req_id="PARENT",
            ),
        ])
        chunks = cb.build_chunks([tree])
        leaf = next(c for c in chunks if c.metadata["req_id"] == "LEAF")
        assert "[Parent context:" in leaf.text
        assert "Device shall support" in leaf.text
        assert "n41 and n78" in leaf.text  # own body still there

    def test_parent_body_capped(self):
        cb = ChunkBuilder(_config(parent_body_max_chars=20))
        long_body = "A" * 200
        tree = _tree([
            _req(req_id="PARENT", title="Parent", text=long_body),
            _req(
                req_id="LEAF", title="Leaf", text="leaf body",
                parent_req_id="PARENT",
            ),
        ])
        chunks = cb.build_chunks([tree])
        leaf = next(c for c in chunks if c.metadata["req_id"] == "LEAF")
        assert "[Parent context: " + "A" * 20 + "…]" in leaf.text

    def test_no_parent_body_when_disabled(self):
        cb = ChunkBuilder(_config(include_parent_body=False))
        tree = _tree([
            _req(req_id="PARENT", title="Parent", text="Parent body."),
            _req(
                req_id="LEAF", title="Leaf", text="leaf body",
                parent_req_id="PARENT",
            ),
        ])
        chunks = cb.build_chunks([tree])
        leaf = next(c for c in chunks if c.metadata["req_id"] == "LEAF")
        assert "[Parent context:" not in leaf.text

    def test_no_parent_body_when_parent_empty(self):
        cb = ChunkBuilder(_config())
        tree = _tree([
            _req(req_id="PARENT", title="5.1 Bands", text=""),
            _req(
                req_id="LEAF", title="Leaf", text="leaf body",
                parent_req_id="PARENT",
            ),
        ])
        chunks = cb.build_chunks([tree])
        leaf = next(c for c in chunks if c.metadata["req_id"] == "LEAF")
        assert "[Parent context:" not in leaf.text

    def test_no_parent_body_when_no_parent(self):
        cb = ChunkBuilder(_config())
        tree = _tree([
            _req(
                req_id="ROOT",
                title="Mandatory bands",
                text="Device shall support n41.",
            ),
        ])
        chunks = cb.build_chunks([tree])
        assert "[Parent context:" not in chunks[0].text


# ── Marker log CSV ──────────────────────────────────────────────────


class TestMarkerLog:
    def test_log_written_when_path_set(self, tmp_path):
        log_path = tmp_path / "markers.csv"
        cb = ChunkBuilder(_config(skipped_headers_log=str(log_path)))
        tree = _tree([
            _req(req_id="VZ_REQ_TESTPLAN_1", title="5.1 5G SA bands"),
            _req(
                req_id="VZ_REQ_TESTPLAN_2",
                title="Device shall support n41",
            ),
        ])
        cb.build_chunks([tree])

        assert log_path.exists()
        with open(log_path, newline="") as f:
            rows = list(csv.reader(f))
        # Header + one marker row
        assert rows[0] == [
            "plan_id", "plan_name", "section_number",
            "hierarchy_path", "title", "req_id", "verdict", "reason",
        ]
        assert len(rows) == 2  # header + 1 marker
        marker_row = rows[1]
        assert marker_row[0] == "TESTPLAN"  # plan_id
        assert marker_row[5] == "VZ_REQ_TESTPLAN_1"  # req_id
        assert marker_row[6] == CHUNK_ROLE_MARKER  # verdict

    def test_log_not_written_when_path_empty(self, tmp_path):
        # Default config has empty skipped_headers_log
        cb = ChunkBuilder(_config())
        tree = _tree([_req(req_id="X", title="5.1 short")])
        cb.build_chunks([tree])
        # No log file should have been created in tmp_path
        assert list(tmp_path.iterdir()) == []
