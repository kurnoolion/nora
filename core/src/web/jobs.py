"""Job queue for NORA pipeline execution tracking.

Persists jobs and logs to SQLite via aiosqlite. Jobs are submitted through
the web UI, run in the background, and polled for progress. Jobs of the
`ingest*` types are not run here: a host-side runner claims them from the
same database file (`core.src.ingest_runner`) and the web only shows
them. The schema lives in `jobs_schema.py`, shared with that runner.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from core.src.web.jobs_schema import ensure_schema


@dataclass
class Job:
    id: str
    job_type: str  # "pipeline", "query", "eval", "ingest", "ingest-flip", ...
    status: str  # "submitted", "running", "completed", "failed", "cancelled"
    submitted_by: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None

    # Pipeline-specific
    environment: str | None = None
    stages: list[str] = field(default_factory=list)
    current_stage: str | None = None

    # Query-specific
    query_text: str | None = None

    # Results
    progress: int = 0
    log_lines: list[str] = field(default_factory=list)
    result_summary: str | None = None
    error_message: str | None = None

    # Runner-executed jobs (ingest*): the request, who claimed it, liveness
    params: dict = field(default_factory=dict)
    runner: str | None = None
    heartbeat_at: str | None = None


@dataclass
class Ingest:
    """One self-serve ingest across its jobs; the row the Ingest page shows."""
    id: str
    job_id: str | None
    mno: str
    release: str
    source: str
    note: str
    submitted_by: str
    created_at: str
    updated_at: str
    state: str = "queued"
    stage: str | None = None
    build: str | None = None
    label: str | None = None
    run_ref: str | None = None
    recall_avg: float | None = None
    baseline: float | None = None
    verdict: str | None = None
    verdict_detail: str | None = None
    previous_label: str | None = None
    flipped_at: str | None = None
    rolled_back_at: str | None = None
    error: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_to_job(row: aiosqlite.Row) -> Job:
    return Job(
        id=row["id"],
        job_type=row["job_type"],
        status=row["status"],
        submitted_by=row["submitted_by"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        environment=row["environment"],
        stages=json.loads(row["stages"]),
        current_stage=row["current_stage"],
        query_text=row["query_text"],
        progress=row["progress"],
        result_summary=row["result_summary"],
        error_message=row["error_message"],
        params=json.loads(row["params"]) if row["params"] else {},
        runner=row["runner"],
        heartbeat_at=row["heartbeat_at"],
    )


def _row_to_ingest(row: aiosqlite.Row) -> Ingest:
    return Ingest(**{k: row[k] for k in row.keys()})


class JobQueue:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)

    async def init_db(self) -> None:
        def _sync() -> None:
            con = sqlite3.connect(self._db_path)
            try:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA foreign_keys=ON")
                ensure_schema(con)
            finally:
                con.close()
        await asyncio.to_thread(_sync)

    async def submit(
        self,
        job_type: str,
        submitted_by: str,
        *,
        environment: str | None = None,
        stages: list[str] | None = None,
        query_text: str | None = None,
        params: dict | None = None,
    ) -> Job:
        job = Job(
            id=str(uuid.uuid4()),
            job_type=job_type,
            status="submitted",
            submitted_by=submitted_by,
            created_at=_now_iso(),
            environment=environment,
            stages=stages or [],
            query_text=query_text,
            params=params or {},
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO jobs
                   (id, job_type, status, submitted_by, created_at,
                    environment, stages, query_text, progress, params)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.id,
                    job.job_type,
                    job.status,
                    job.submitted_by,
                    job.created_at,
                    job.environment,
                    json.dumps(job.stages),
                    job.query_text,
                    job.progress,
                    json.dumps(job.params) if job.params else None,
                ),
            )
            await db.commit()
        return job

    async def get(self, job_id: str) -> Job | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            job = _row_to_job(row)

        job.log_lines = await self.get_logs(job_id)
        return job

    async def get_meta(self, job_id: str) -> Job | None:
        """Get job metadata without loading log lines."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_job(row)

    async def list_jobs(
        self, limit: int = 50, status: str | None = None
    ) -> list[Job]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if status:
                cursor = await db.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]

    async def update_status(self, job_id: str, status: str, **kwargs: object) -> None:
        allowed = {
            "current_stage",
            "progress",
            "error_message",
            "result_summary",
            "started_at",
            "completed_at",
            "runner",
            "heartbeat_at",
        }
        sets = ["status = ?"]
        params: list[object] = [status]

        if status == "running" and "started_at" not in kwargs:
            kwargs["started_at"] = _now_iso()
        if status in ("completed", "failed", "cancelled") and "completed_at" not in kwargs:
            kwargs["completed_at"] = _now_iso()

        for key, value in kwargs.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            params.append(value)

        params.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?"
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(sql, params)
            await db.commit()

    async def append_log(self, job_id: str, line: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT COALESCE(MAX(line_number), 0) FROM job_logs WHERE job_id = ?",
                (job_id,),
            )
            row = await cursor.fetchone()
            next_line = (row[0] if row else 0) + 1

            await db.execute(
                "INSERT INTO job_logs (job_id, line_number, timestamp, message) VALUES (?, ?, ?, ?)",
                (job_id, next_line, _now_iso(), line),
            )
            await db.commit()

    async def get_logs(self, job_id: str, after_line: int = 0) -> list[str]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT message FROM job_logs WHERE job_id = ? AND line_number > ? ORDER BY line_number",
                (job_id, after_line),
            )
            rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def get_logs_with_numbers(
        self, job_id: str, after_line: int = 0,
    ) -> list[tuple[int, str]]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT line_number, message FROM job_logs "
                "WHERE job_id = ? AND line_number > ? ORDER BY line_number",
                (job_id, after_line),
            )
            rows = await cursor.fetchall()
        return [(r[0], r[1]) for r in rows]

    async def cancel(self, job_id: str) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            if row[0] in ("completed", "failed", "cancelled"):
                return False

            await db.execute(
                "UPDATE jobs SET status = 'cancelled', completed_at = ? WHERE id = ?",
                (_now_iso(), job_id),
            )
            await db.commit()
        return True

    # -- ingests (self-serve ingestion; written by the runner, read here) ----

    async def create_ingest(
        self, *, mno: str, release: str, source: str, submitted_by: str,
        note: str = "", job_id: str | None = None,
    ) -> Ingest:
        now = _now_iso()
        ing = Ingest(
            id=str(uuid.uuid4()), job_id=job_id, mno=mno, release=release,
            source=source, note=note, submitted_by=submitted_by,
            created_at=now, updated_at=now,
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO ingests
                   (id, job_id, mno, release, source, note, submitted_by,
                    created_at, updated_at, state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ing.id, ing.job_id, ing.mno, ing.release, ing.source,
                 ing.note, ing.submitted_by, ing.created_at, ing.updated_at,
                 ing.state),
            )
            await db.commit()
        return ing

    async def get_ingest(self, ingest_id: str) -> Ingest | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM ingests WHERE id = ?", (ingest_id,))
            row = await cursor.fetchone()
            return _row_to_ingest(row) if row is not None else None

    async def list_ingests(self, limit: int = 50, state: str | None = None) -> list[Ingest]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if state:
                cursor = await db.execute(
                    "SELECT * FROM ingests WHERE state = ? ORDER BY created_at DESC LIMIT ?",
                    (state, limit))
            else:
                cursor = await db.execute(
                    "SELECT * FROM ingests ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = await cursor.fetchall()
        return [_row_to_ingest(r) for r in rows]

    async def update_ingest(self, ingest_id: str, **fields: object) -> None:
        """Set columns of an ingest row; unknown names are ignored."""
        allowed = {
            "job_id", "state", "stage", "build", "label", "run_ref",
            "recall_avg", "baseline", "verdict", "verdict_detail",
            "previous_label", "flipped_at", "rolled_back_at", "error", "note",
        }
        sets = ["updated_at = ?"]
        params: list[object] = [_now_iso()]
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                params.append(value)
        params.append(ingest_id)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(f"UPDATE ingests SET {', '.join(sets)} WHERE id = ?", params)
            await db.commit()

    async def cleanup_old(self, days: int = 30) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            cursor = await db.execute(
                "DELETE FROM jobs WHERE created_at < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            # Clean orphan logs
            await db.execute(
                "DELETE FROM job_logs WHERE job_id NOT IN (SELECT id FROM jobs)"
            )
            await db.commit()
        return deleted
