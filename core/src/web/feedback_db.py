"""Test-page feedback store — async SQLite log of question / answer /
vote / free-form feedback for offline review.

Schema is intentionally narrow and append-only by row. Each user
question creates one row at submission time (with `vote=NULL`); the
later feedback POST updates `vote` and `free_form_feedback` in place
on that same row. Rows are never deleted by the app — the audit
trail is preserved even when the user changes their mind on a vote.

Path: `<env_dir>/state/nora_test_feedback.db` (per `WebConfig.
feedback_db_path()`). Uses the same aiosqlite pattern as
`web/metrics.py` and `web/jobs.py`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS test_feedback (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,
    section           TEXT NOT NULL,
    question          TEXT NOT NULL,
    answer            TEXT NOT NULL,
    citations_json    TEXT,
    vote              TEXT,                  -- 'up' | 'down' | NULL (legacy)
    free_form_feedback TEXT,
    query_elapsed_ms  INTEGER,
    llm_model         TEXT,
    metadata_json     TEXT,
    -- Merged-tab extensions (team-eval-pilot). Also added to legacy DBs
    -- via _ensure_columns() on initialize(). NULL on legacy rows.
    lane              TEXT,                  -- 'nora' | 'sira' (merged tab only)
    user_name         TEXT,
    retrieved_ids     TEXT,                  -- JSON array of req_ids
    reranked_ids      TEXT,                  -- JSON array; NULL when rerank off
    cited_ids         TEXT,                  -- JSON array (req_ids only)
    user_score        INTEGER,               -- 0..9; constrained in Python
    user_categories   TEXT,                  -- JSON array of category keys
    lane_config       TEXT                   -- JSON config snapshot at ask time
);
CREATE INDEX IF NOT EXISTS test_feedback_ts_idx
    ON test_feedback(timestamp);
CREATE INDEX IF NOT EXISTS test_feedback_section_idx
    ON test_feedback(section);
"""
# The lane index is created AFTER _ensure_columns so legacy DBs can have
# the column added first. CREATE INDEX ON a missing column errors hard,
# even with IF NOT EXISTS on the index.
_LANE_INDEX = (
    "CREATE INDEX IF NOT EXISTS test_feedback_lane_idx "
    "ON test_feedback(lane);"
)


# Columns added by the merged-tab migration. Listed here so legacy DBs
# (which were created before these existed) can be brought forward via
# ADD COLUMN — SQLite has no ALTER TABLE ADD COLUMN IF NOT EXISTS, so
# _ensure_columns() checks PRAGMA table_info first.
_MERGED_TAB_COLUMNS: list[tuple[str, str]] = [
    ("lane",            "TEXT"),
    ("user_name",       "TEXT"),
    ("retrieved_ids",   "TEXT"),
    ("reranked_ids",    "TEXT"),
    ("cited_ids",       "TEXT"),
    ("user_score",      "INTEGER"),
    ("user_categories", "TEXT"),
    ("lane_config",     "TEXT"),
]


# Feedback category keys + human-readable labels surfaced on the merged tab.
# Frozen for the pilot — adding a key here also requires UI work to expose
# the new checkbox, so don't change without a coordinated change.
CATEGORIES: dict[str, str] = {
    "has_all":              "Response has all required info",
    "has_partial":          "Response has partial info",
    "has_extra_relevant":   "Response has extraneous info, but possibly relevant",
    "has_extra_irrelevant": "Response has extraneous info, but irrelevant",
}


async def _ensure_columns(
    db: "aiosqlite.Connection", table: str,
    columns: list[tuple[str, str]],
) -> None:
    """Add any of `columns` not already present on `table`. SQLite has no
    ALTER TABLE ADD COLUMN IF NOT EXISTS, so this PRAGMA-checks first.
    Idempotent — running twice is a no-op.
    """
    cur = await db.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in await cur.fetchall()}
    for name, typ in columns:
        if name not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


class FeedbackStore:
    """Async SQLite store for Test-page question/answer/feedback logs."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)

    async def initialize(self) -> None:
        """Create the schema if missing and bring older DBs forward with
        the merged-tab columns. Safe to call repeatedly."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await _ensure_columns(db, "test_feedback", _MERGED_TAB_COLUMNS)
            await db.execute(_LANE_INDEX)
            await db.commit()
        logger.info(f"FeedbackStore ready at {self._db_path}")

    async def record_qa(
        self,
        section: str,
        question: str,
        answer: str,
        citations: list[dict] | None = None,
        query_elapsed_ms: int | None = None,
        llm_model: str | None = None,
        metadata: dict | None = None,
        *,
        # Merged-tab extensions (team-eval-pilot). All optional; legacy
        # callers omit these and the row's new columns stay NULL.
        lane: str | None = None,
        user_name: str | None = None,
        retrieved_ids: list[str] | None = None,
        reranked_ids: list[str] | None = None,
        cited_ids: list[str] | None = None,
        lane_config: dict | None = None,
    ) -> int:
        """Insert a new row at question-submission time. Returns the
        row id; pass it to `record_feedback()` (legacy vote path) or
        `record_user_feedback()` (merged-tab 0..9 + categories) later
        when the user submits. `vote`, `free_form_feedback`, and the
        merged-tab feedback fields start NULL so unvoted/unrated rows
        are still captured for audit.
        """
        if lane is not None and lane not in ("nora", "sira"):
            raise ValueError(
                f"lane must be 'nora' or 'sira' or None, got {lane!r}"
            )
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """
                INSERT INTO test_feedback
                  (timestamp, section, question, answer, citations_json,
                   query_elapsed_ms, llm_model, metadata_json,
                   lane, user_name, retrieved_ids, reranked_ids,
                   cited_ids, lane_config)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    section,
                    question,
                    answer,
                    json.dumps(citations or []),
                    query_elapsed_ms,
                    llm_model,
                    json.dumps(metadata or {}),
                    lane,
                    user_name,
                    json.dumps(retrieved_ids) if retrieved_ids is not None else None,
                    json.dumps(reranked_ids) if reranked_ids is not None else None,
                    json.dumps(cited_ids) if cited_ids is not None else None,
                    json.dumps(lane_config) if lane_config is not None else None,
                ),
            )
            await db.commit()
            return cur.lastrowid

    async def record_feedback(
        self,
        row_id: int,
        vote: str | None,
        free_form_feedback: str | None,
    ) -> bool:
        """Update an existing Q&A row with the user's vote and/or
        free-form comment. `vote` is `'up'`, `'down'`, or `None` to
        clear. Returns True if a row was updated, False otherwise.
        """
        if vote not in ("up", "down", None):
            raise ValueError(f"Invalid vote {vote!r}; expected 'up', 'down', or None")
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """
                UPDATE test_feedback
                   SET vote = ?, free_form_feedback = ?
                 WHERE id = ?
                """,
                (vote, free_form_feedback, row_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def record_user_feedback(
        self,
        row_id: int,
        *,
        user_score: int,
        user_categories: list[str] | None = None,
        comment: str | None = None,
        user_name: str | None = None,
    ) -> bool:
        """Merged-tab feedback path: update an existing row with the user's
        0..9 score, multi-select category flags, free-form comment, and
        optional name. Re-submitting overwrites; the pilot does not keep
        feedback history. Returns True if a row was updated, False if no
        row with `row_id` exists.

        The comment is stored in `free_form_feedback` (the existing column),
        so analyses can union legacy votes' free-form text with merged-tab
        comments by querying that single column. Does NOT touch `vote` —
        the merged tab uses `user_score` instead.
        """
        if not (0 <= user_score <= 9):
            raise ValueError(f"user_score must be 0..9, got {user_score}")
        cats = list(user_categories or [])
        unknown = [c for c in cats if c not in CATEGORIES]
        if unknown:
            raise ValueError(f"unknown category keys: {unknown}")
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """
                UPDATE test_feedback
                   SET user_score      = ?,
                       user_categories = ?,
                       free_form_feedback = ?,
                       user_name       = COALESCE(?, user_name)
                 WHERE id = ?
                """,
                (
                    user_score,
                    json.dumps(sorted(set(cats))),
                    comment,
                    user_name,
                    row_id,
                ),
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_row(self, row_id: int) -> dict[str, Any] | None:
        """Read a single row by id (for testing / inspection)."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM test_feedback WHERE id = ?", (row_id,)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_recent(
        self,
        section: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Read the N most recent rows, optionally filtered by section.
        Used by inspection tooling; not a public API surface."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if section:
                cur = await db.execute(
                    "SELECT * FROM test_feedback "
                    "WHERE section = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (section, limit),
                )
            else:
                cur = await db.execute(
                    "SELECT * FROM test_feedback "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
