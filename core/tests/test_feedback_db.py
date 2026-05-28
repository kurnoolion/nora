"""Tests for `core/src/web/feedback_db.py` — Test page Q&A + vote log.

Two flows covered:
  * Legacy (requirement_bot / sira_retrieval tabs): record_qa + record_feedback
    (vote up/down + free_form_feedback). Pre-existing tests below.
  * Merged tab (team-eval-pilot): record_qa with lane/retrieved_ids/etc +
    record_user_feedback (0..9 score + categories + comment). Added below.

Also covers schema migration: fresh DB has all columns; legacy DB (just the
original columns) is brought forward via ADD COLUMN on initialize().
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiosqlite
import pytest

from core.src.web.feedback_db import (
    _MERGED_TAB_COLUMNS,
    CATEGORIES,
    FeedbackStore,
)


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


# ── schema migration (merged-tab columns) ─────────────────────────────


def test_initialize_creates_merged_tab_columns_on_fresh_db(store):
    """A fresh DB must have every merged-tab column from day 1."""
    _run(store.initialize())
    async def _cols():
        async with aiosqlite.connect(store._db_path) as conn:
            cur = await conn.execute("PRAGMA table_info(test_feedback)")
            return {row[1] for row in await cur.fetchall()}
    cols = _run(_cols())
    for name, _ in _MERGED_TAB_COLUMNS:
        assert name in cols, f"fresh DB missing column {name}"


def test_initialize_upgrades_legacy_db_without_losing_rows(tmp_path):
    """A DB created with the OLD (pre-merged-tab) schema gets the new
    columns added in place via ADD COLUMN, and existing rows survive."""
    db_path = tmp_path / "legacy.db"
    legacy_schema = """
        CREATE TABLE test_feedback (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT NOT NULL,
            section           TEXT NOT NULL,
            question          TEXT NOT NULL,
            answer            TEXT NOT NULL,
            citations_json    TEXT,
            vote              TEXT,
            free_form_feedback TEXT,
            query_elapsed_ms  INTEGER,
            llm_model         TEXT,
            metadata_json     TEXT
        );
    """
    async def _seed_legacy():
        async with aiosqlite.connect(str(db_path)) as conn:
            await conn.executescript(legacy_schema)
            await conn.execute(
                "INSERT INTO test_feedback (timestamp, section, question, answer) "
                "VALUES (?, ?, ?, ?)",
                ("2026-01-01T00:00:00+00:00", "requirement_bot", "old q", "old a"),
            )
            await conn.commit()
    _run(_seed_legacy())

    # Run initialize() — must ADD COLUMN for each missing merged-tab column.
    _run(FeedbackStore(db_path).initialize())

    async def _inspect():
        async with aiosqlite.connect(str(db_path)) as conn:
            cur = await conn.execute("PRAGMA table_info(test_feedback)")
            cols = {row[1] for row in await cur.fetchall()}
            cur = await conn.execute("SELECT question, answer FROM test_feedback")
            rows = await cur.fetchall()
        return cols, rows
    cols, rows = _run(_inspect())
    for name, _ in _MERGED_TAB_COLUMNS:
        assert name in cols, f"upgrade did not add {name}"
    assert rows == [("old q", "old a")], "legacy row was lost in migration"


# ── merged-tab record_qa (extended kwargs) ────────────────────────────


def test_record_qa_merged_tab_round_trips(store):
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="merged",
        question="What is foo?",
        answer="Foo is …",
        citations=[{"req_id": "R1", "title": "x"}],
        llm_model="internal-llm",
        lane="sira",
        user_name="alice",
        retrieved_ids=["R1", "R2", "R3"],
        reranked_ids=["R2", "R1", "R3"],
        cited_ids=["R1"],
        lane_config={"rerank_enabled": False, "fanout_enabled": False, "top_k": 15},
    ))
    row = _run(store.get_row(rid))
    assert row["section"] == "merged"
    assert row["lane"] == "sira"
    assert row["user_name"] == "alice"
    assert json.loads(row["retrieved_ids"]) == ["R1", "R2", "R3"]
    assert json.loads(row["reranked_ids"]) == ["R2", "R1", "R3"]
    assert json.loads(row["cited_ids"]) == ["R1"]
    assert json.loads(row["lane_config"]) == {
        "rerank_enabled": False, "fanout_enabled": False, "top_k": 15,
    }
    # merged-tab insert leaves all feedback fields NULL
    assert row["user_score"] is None
    assert row["user_categories"] is None
    assert row["vote"] is None


def test_record_qa_legacy_call_leaves_merged_columns_null(store):
    """The legacy call signature must keep working — new columns stay NULL."""
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="requirement_bot", question="q", answer="a",
        citations=[{"req_id": "R1"}], llm_model="m",
    ))
    row = _run(store.get_row(rid))
    for col in ("lane", "user_name", "retrieved_ids", "reranked_ids",
                "cited_ids", "user_score", "user_categories", "lane_config"):
        assert row[col] is None, f"{col} should be NULL on legacy insert"


def test_record_qa_rejects_unknown_lane(store):
    _run(store.initialize())
    with pytest.raises(ValueError, match="lane"):
        _run(store.record_qa(
            section="merged", question="q", answer="a", lane="other",
        ))


def test_record_qa_reranked_ids_null_when_rerank_off(store):
    """Omitting reranked_ids (i.e. rerank disabled for this lane) stores NULL."""
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="merged", question="q", answer="a",
        lane="sira", retrieved_ids=["R1"], cited_ids=["R1"],
    ))
    row = _run(store.get_row(rid))
    assert row["reranked_ids"] is None
    assert json.loads(row["retrieved_ids"]) == ["R1"]


# ── merged-tab record_user_feedback ───────────────────────────────────


def test_record_user_feedback_inserts_then_overwrites(store):
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="merged", question="q", answer="a", lane="nora",
    ))
    assert _run(store.record_user_feedback(
        rid, user_score=5, user_categories=["has_partial"],
        comment="ok-ish", user_name="bob",
    )) is True
    row = _run(store.get_row(rid))
    assert row["user_score"] == 5
    assert json.loads(row["user_categories"]) == ["has_partial"]
    assert row["free_form_feedback"] == "ok-ish"
    assert row["user_name"] == "bob"

    # re-submit overwrites (no history table)
    _run(store.record_user_feedback(
        rid, user_score=8,
        user_categories=["has_all", "has_extra_relevant"],
        comment="better",
    ))
    row = _run(store.get_row(rid))
    assert row["user_score"] == 8
    assert json.loads(row["user_categories"]) == ["has_all", "has_extra_relevant"]
    assert row["free_form_feedback"] == "better"
    # user_name preserved via COALESCE when not re-passed
    assert row["user_name"] == "bob"


def test_record_user_feedback_missing_row_returns_false(store):
    _run(store.initialize())
    assert _run(store.record_user_feedback(
        99999, user_score=5, user_categories=[], comment=None,
    )) is False


def test_record_user_feedback_validates_score_range(store):
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="merged", question="q", answer="a", lane="nora",
    ))
    with pytest.raises(ValueError, match="score"):
        _run(store.record_user_feedback(rid, user_score=10, user_categories=[]))
    with pytest.raises(ValueError, match="score"):
        _run(store.record_user_feedback(rid, user_score=-1, user_categories=[]))


def test_record_user_feedback_validates_category_keys(store):
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="merged", question="q", answer="a", lane="sira",
    ))
    with pytest.raises(ValueError, match="category"):
        _run(store.record_user_feedback(
            rid, user_score=5, user_categories=["bogus"],
        ))


def test_record_user_feedback_dedups_and_sorts_categories(store):
    """Stored normalized so analysis SQL can treat categories as a stable set."""
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="merged", question="q", answer="a", lane="nora",
    ))
    _run(store.record_user_feedback(
        rid, user_score=4,
        user_categories=["has_partial", "has_all", "has_partial"],
    ))
    row = _run(store.get_row(rid))
    assert json.loads(row["user_categories"]) == ["has_all", "has_partial"]


def test_record_user_feedback_empty_categories_allowed(store):
    """Score + comment without any category checked is valid."""
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="merged", question="q", answer="a", lane="nora",
    ))
    assert _run(store.record_user_feedback(
        rid, user_score=7, user_categories=[], comment="just a score",
    )) is True
    row = _run(store.get_row(rid))
    assert json.loads(row["user_categories"]) == []


def test_record_user_feedback_does_not_clobber_vote(store):
    """Merged-tab feedback path must leave the legacy vote column alone."""
    _run(store.initialize())
    rid = _run(store.record_qa(
        section="merged", question="q", answer="a", lane="nora",
    ))
    # imagine a stray vote on the row (e.g. analytical scenario)
    async def _set_vote():
        async with aiosqlite.connect(store._db_path) as conn:
            await conn.execute("UPDATE test_feedback SET vote='up' WHERE id=?", (rid,))
            await conn.commit()
    _run(_set_vote())
    _run(store.record_user_feedback(rid, user_score=6, user_categories=[]))
    row = _run(store.get_row(rid))
    assert row["vote"] == "up", "merged-tab feedback must not clobber vote"


# ── CATEGORIES ─────────────────────────────────────────────────────────


def test_categories_has_exactly_four_keys():
    assert set(CATEGORIES) == {
        "has_all", "has_partial", "has_extra_relevant", "has_extra_irrelevant",
    }


def test_categories_values_are_human_readable():
    for label in CATEGORIES.values():
        assert isinstance(label, str) and len(label) > 0
