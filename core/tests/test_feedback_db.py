"""Tests for `core/src/web/feedback_db.py` — Test page Q&A + vote log."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.src.web.feedback_db import FeedbackStore


@pytest.fixture
def store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback.db")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_initialize_creates_schema(store):
    _run(store.initialize())
    # Re-initialize is idempotent
    _run(store.initialize())
    rows = _run(store.list_recent())
    assert rows == []


def test_record_qa_returns_row_id_and_persists_fields(store):
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="requirement_bot",
        question="What is T3402?",
        answer="The T3402 timer is …",
        citations=[{"req_id": "VZ_REQ_LTEDATARETRY_2377", "plan_id": "LTEDATARETRY"}],
        query_elapsed_ms=1234,
        llm_model="qwen/qwen3-235b-a22b",
        metadata={"candidate_count": 12},
    ))
    assert isinstance(rid, int) and rid > 0

    row = _run(store.get_row(rid))
    assert row is not None
    assert row["section"] == "requirement_bot"
    assert row["question"] == "What is T3402?"
    assert row["answer"] == "The T3402 timer is …"
    assert row["query_elapsed_ms"] == 1234
    assert row["llm_model"] == "qwen/qwen3-235b-a22b"
    # vote / free_form_feedback start NULL — captured for audit even
    # when the user never votes
    assert row["vote"] is None
    assert row["free_form_feedback"] is None
    # citations + metadata round-trip as JSON
    assert json.loads(row["citations_json"]) == [
        {"req_id": "VZ_REQ_LTEDATARETRY_2377", "plan_id": "LTEDATARETRY"}
    ]
    assert json.loads(row["metadata_json"]) == {"candidate_count": 12}


def test_record_feedback_updates_existing_row(store):
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="requirement_bot",
        question="Q?",
        answer="A.",
    ))

    ok = _run(store.record_feedback(
        rid, vote="up", free_form_feedback="exactly what I needed"
    ))
    assert ok is True

    row = _run(store.get_row(rid))
    assert row["vote"] == "up"
    assert row["free_form_feedback"] == "exactly what I needed"
    # Original Q&A fields untouched
    assert row["question"] == "Q?"
    assert row["answer"] == "A."


def test_record_feedback_handles_missing_row(store):
    _run(store.initialize())
    ok = _run(store.record_feedback(999, vote="up", free_form_feedback=None))
    assert ok is False


def test_record_feedback_rejects_invalid_vote(store):
    _run(store.initialize())
    rid = _run(store.record_qa(section="x", question="q", answer="a"))
    with pytest.raises(ValueError):
        _run(store.record_feedback(rid, vote="meh", free_form_feedback=None))


def test_record_feedback_can_clear_vote(store):
    """vote=None is valid — represents the user reverting their
    decision. Free-form feedback can also be cleared independently."""
    _run(store.initialize())
    rid = _run(store.record_qa(section="x", question="q", answer="a"))
    _run(store.record_feedback(rid, vote="up", free_form_feedback="ok"))
    _run(store.record_feedback(rid, vote=None, free_form_feedback=None))
    row = _run(store.get_row(rid))
    assert row["vote"] is None
    assert row["free_form_feedback"] is None


def test_list_recent_orders_newest_first_and_filters_by_section(store):
    _run(store.initialize())
    rid_a = _run(store.record_qa(section="requirement_bot", question="a", answer="a"))
    rid_b = _run(store.record_qa(section="compliance_check", question="b", answer="b"))
    rid_c = _run(store.record_qa(section="requirement_bot", question="c", answer="c"))

    all_rows = _run(store.list_recent())
    assert [r["id"] for r in all_rows] == [rid_c, rid_b, rid_a]

    rb_rows = _run(store.list_recent(section="requirement_bot"))
    assert [r["id"] for r in rb_rows] == [rid_c, rid_a]

    cc_rows = _run(store.list_recent(section="compliance_check"))
    assert [r["id"] for r in cc_rows] == [rid_b]
