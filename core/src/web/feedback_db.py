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
    vote              TEXT,                  -- 'up' | 'down' | NULL
    free_form_feedback TEXT,
    query_elapsed_ms  INTEGER,
    llm_model         TEXT,
    metadata_json     TEXT
);
CREATE INDEX IF NOT EXISTS test_feedback_ts_idx
    ON test_feedback(timestamp);
CREATE INDEX IF NOT EXISTS test_feedback_section_idx
    ON test_feedback(section);
"""


class FeedbackStore:
    """Async SQLite store for Test-page question/answer/feedback logs."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)

    async def initialize(self) -> None:
        """Create the schema if missing. Safe to call repeatedly."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
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
    ) -> int:
        """Insert a new row at question-submission time. Returns the
        row id; pass it to `record_feedback()` later when the user
        votes or comments. `vote` and `free_form_feedback` start as
        NULL so unvoted Q&A pairs are still captured for audit.
        """
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """
                INSERT INTO test_feedback
                  (timestamp, section, question, answer, citations_json,
                   query_elapsed_ms, llm_model, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
