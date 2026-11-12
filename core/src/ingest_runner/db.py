"""The runner's view of the web's jobs database (stdlib `sqlite3`).

Schema from `core.src.web.jobs_schema`; this side claims `ingest*` jobs,
heartbeats, writes log lines and keeps the `ingests` row current.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from core.src.web.jobs_schema import RUNNER_JOB_TYPES, ensure_schema


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class RunnerDB:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        con = self._connect()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            ensure_schema(con)
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    # -- jobs -----------------------------------------------------------------

    def claim_next(self, runner: str, job_types: tuple[str, ...] = RUNNER_JOB_TYPES) -> dict | None:
        """Claim the oldest submitted job of the given types; None if none.
        One UPDATE guarded by the status, so two runners never claim one job."""
        marks = ",".join("?" * len(job_types))
        now = now_iso()
        con = self._connect()
        try:
            row = con.execute(
                f"SELECT id FROM jobs WHERE status = 'submitted' AND job_type IN ({marks}) "
                f"ORDER BY created_at LIMIT 1", job_types).fetchone()
            if row is None:
                return None
            cur = con.execute(
                "UPDATE jobs SET status = 'running', runner = ?, started_at = ?, heartbeat_at = ? "
                "WHERE id = ? AND status = 'submitted'", (runner, now, now, row["id"]))
            con.commit()
            if cur.rowcount != 1:
                return None
            job = con.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        finally:
            con.close()
        d = dict(job)
        d["params"] = json.loads(d["params"]) if d.get("params") else {}
        return d

    def heartbeat(self, job_id: str) -> None:
        con = self._connect()
        try:
            con.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (now_iso(), job_id))
            con.commit()
        finally:
            con.close()

    def job_status(self, job_id: str) -> str | None:
        con = self._connect()
        try:
            row = con.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return row["status"] if row else None
        finally:
            con.close()

    def set_job(self, job_id: str, **cols: object) -> None:
        allowed = {"status", "current_stage", "progress", "result_summary",
                   "error_message", "completed_at", "heartbeat_at"}
        sets, params = [], []
        for k, v in cols.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                params.append(v)
        if not sets:
            return
        params.append(job_id)
        con = self._connect()
        try:
            con.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)
            con.commit()
        finally:
            con.close()

    def finish_job(self, job_id: str, status: str, *, summary: str = "", error: str = "") -> None:
        cols: dict[str, object] = {"status": status, "completed_at": now_iso(),
                                   "result_summary": summary or None, "error_message": error or None}
        if status == "completed":
            cols["progress"] = 100
        self.set_job(job_id, **cols)

    def append_log(self, job_id: str, line: str) -> None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT COALESCE(MAX(line_number), 0) FROM job_logs WHERE job_id = ?", (job_id,)).fetchone()
            con.execute(
                "INSERT INTO job_logs (job_id, line_number, timestamp, message) VALUES (?, ?, ?, ?)",
                (job_id, row[0] + 1, now_iso(), line))
            con.commit()
        finally:
            con.close()

    def logs(self, job_id: str) -> list[str]:
        con = self._connect()
        try:
            return [r[0] for r in con.execute(
                "SELECT message FROM job_logs WHERE job_id = ? ORDER BY line_number", (job_id,))]
        finally:
            con.close()

    def seen(self, runner: str, job_id: str | None = None) -> None:
        """Record that this runner polled now (and what it runs)."""
        con = self._connect()
        try:
            con.execute("INSERT INTO runners (name, seen_at, job_id) VALUES (?, ?, ?) "
                        "ON CONFLICT(name) DO UPDATE SET seen_at = excluded.seen_at, job_id = excluded.job_id",
                        (runner, now_iso(), job_id))
            con.commit()
        finally:
            con.close()

    # -- ingests ----------------------------------------------------------------

    def get_ingest(self, ingest_id: str) -> dict | None:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM ingests WHERE id = ?", (ingest_id,)).fetchone()
            return dict(row) if row else None
        finally:
            con.close()

    def update_ingest(self, ingest_id: str, **cols: object) -> None:
        allowed = {"job_id", "state", "stage", "build", "label", "run_ref", "recall_avg",
                   "baseline", "verdict", "verdict_detail", "previous_label", "flipped_at",
                   "rolled_back_at", "error", "stage_origins", "documents_staged"}
        sets, params = ["updated_at = ?"], [now_iso()]
        for k, v in cols.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                params.append(v)
        params.append(ingest_id)
        con = self._connect()
        try:
            con.execute(f"UPDATE ingests SET {', '.join(sets)} WHERE id = ?", params)
            con.commit()
        finally:
            con.close()

    def ingests_sharing(self, ingest_id: str, label: str, build: str) -> list[dict]:
        """Other ingest rows that carry this label or this build. Two rows share a
        name after a by-hand cleanup let the same-day number restart; a purge
        of one of them by name would remove the other's artifacts."""
        if not label and not build:
            return []
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT id, state, label, build FROM ingests WHERE id <> ? AND "
                "((label IS NOT NULL AND label = ?) OR (build IS NOT NULL AND build = ?)) "
                "ORDER BY created_at", (ingest_id, label or "", build or ""))
            return [dict(r) for r in rows]
        finally:
            con.close()

    def last_served_build(self) -> str | None:
        """The build of the newest ingest that was flipped (served, or
        served then rolled back): the seed for the next cycle."""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT build FROM ingests WHERE flipped_at IS NOT NULL AND build IS NOT NULL "
                "ORDER BY flipped_at DESC LIMIT 1").fetchone()
            return row["build"] if row else None
        finally:
            con.close()

    def baseline_recall(self) -> float | None:
        """Stage-1 recall of the newest ingest whose gate passed."""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT recall_avg FROM ingests WHERE verdict = 'pass' AND recall_avg IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 1").fetchone()
            return float(row["recall_avg"]) if row else None
        finally:
            con.close()
