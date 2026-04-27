"""Metrics persistence store for NORA observability.

SQLite-backed metrics storage using the same aiosqlite pattern as jobs.py.
Records request timing, LLM performance, pipeline stage durations, and
system resource utilization. Produces compact reports for paste-friendly
debugging across remote team members.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import aiosqlite

logger = logging.getLogger(__name__)

_METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '{}'
)
"""

_IDX_CATEGORY = """
CREATE INDEX IF NOT EXISTS idx_metrics_category ON metrics (category)
"""

_IDX_NAME = """
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics (name)
"""

_IDX_TIMESTAMP = """
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics (timestamp DESC)
"""

_IDX_CAT_NAME_TS = """
CREATE INDEX IF NOT EXISTS idx_metrics_cat_name_ts ON metrics (category, name, timestamp DESC)
"""


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class MetricRecord:
    timestamp: str
    category: str       # "request", "llm", "pipeline", "resource", "eval"
    name: str           # "response_time", "ollama_latency", "stage_duration", etc.
    value: float
    unit: str           # "ms", "seconds", "percent", "bytes", "tok/s", "count"
    tags: dict = field(default_factory=dict)


class MetricsStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(_METRICS_SCHEMA)
            await db.execute(_IDX_CATEGORY)
            await db.execute(_IDX_NAME)
            await db.execute(_IDX_TIMESTAMP)
            await db.execute(_IDX_CAT_NAME_TS)
            await db.commit()

    async def record(
        self,
        category: str,
        name: str,
        value: float,
        unit: str,
        tags: dict | None = None,
    ) -> None:
        ts = _now_iso()
        tags_json = json.dumps(tags or {})
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO metrics (timestamp, category, name, value, unit, tags) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, category, name, value, unit, tags_json),
            )
            await db.commit()

    async def record_batch(self, records: list[MetricRecord]) -> None:
        if not records:
            return
        rows = [
            (r.timestamp, r.category, r.name, r.value, r.unit, json.dumps(r.tags))
            for r in records
        ]
        async with aiosqlite.connect(self._db_path) as db:
            await db.executemany(
                "INSERT INTO metrics (timestamp, category, name, value, unit, tags) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            await db.commit()

    async def query(
        self,
        category: str | None = None,
        name: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[MetricRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if name:
            clauses.append("name = ?")
            params.append(name)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT timestamp, category, name, value, unit, tags FROM metrics{where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()

        results = []
        for row in rows:
            tags = {}
            try:
                tags = json.loads(row[5]) if row[5] else {}
            except (json.JSONDecodeError, TypeError):
                pass
            results.append(MetricRecord(
                timestamp=row[0],
                category=row[1],
                name=row[2],
                value=row[3],
                unit=row[4],
                tags=tags,
            ))
        return results

    async def summary(
        self,
        category: str | None = None,
        since: str | None = None,
    ) -> dict:
        """Aggregates: count, avg, min, max, p95 per metric name."""
        clauses: list[str] = []
        params: list[object] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        # Get distinct metric names in this scope
        sql_names = f"SELECT DISTINCT category, name FROM metrics{where}"
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(sql_names, params)
            name_rows = await cursor.fetchall()

        result: dict[str, dict] = {}
        for cat, name in name_rows:
            key = f"{cat}.{name}"
            inner_clauses = ["category = ?", "name = ?"]
            inner_params: list[object] = [cat, name]
            if since:
                inner_clauses.append("timestamp >= ?")
                inner_params.append(since)
            inner_where = " WHERE " + " AND ".join(inner_clauses)

            async with aiosqlite.connect(self._db_path) as db:
                # Basic aggregates
                agg_sql = (
                    f"SELECT COUNT(*), AVG(value), MIN(value), MAX(value) "
                    f"FROM metrics{inner_where}"
                )
                cursor = await db.execute(agg_sql, inner_params)
                agg_row = await cursor.fetchone()

                # p95 via offset
                count = agg_row[0] if agg_row else 0
                p95 = 0.0
                if count > 0:
                    p95_offset = max(0, int(count * 0.95) - 1)
                    p95_sql = (
                        f"SELECT value FROM metrics{inner_where} "
                        f"ORDER BY value ASC LIMIT 1 OFFSET ?"
                    )
                    p95_params = list(inner_params) + [p95_offset]
                    cursor = await db.execute(p95_sql, p95_params)
                    p95_row = await cursor.fetchone()
                    p95 = p95_row[0] if p95_row else 0.0

                # Get unit from latest record
                unit_sql = (
                    f"SELECT unit FROM metrics{inner_where} "
                    f"ORDER BY timestamp DESC LIMIT 1"
                )
                cursor = await db.execute(unit_sql, inner_params)
                unit_row = await cursor.fetchone()
                unit = unit_row[0] if unit_row else ""

            result[key] = {
                "count": count,
                "avg": round(agg_row[1], 2) if agg_row and agg_row[1] is not None else 0,
                "min": round(agg_row[2], 2) if agg_row and agg_row[2] is not None else 0,
                "max": round(agg_row[3], 2) if agg_row and agg_row[3] is not None else 0,
                "p95": round(p95, 2),
                "unit": unit,
            }
        return result

    async def compact_report(self) -> str:
        """Compact pasteable summary in RPT style."""
        now = datetime.now(UTC)
        since_1h = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        ts = now.strftime("%Y-%m-%dT%H:%M")

        lines: list[str] = [f"MET {ts}"]

        # Request summary (last hour)
        req_summary = await self._agg_for("request", "response_time", since_1h)
        if req_summary["count"] > 0:
            lines.append(
                f"REQ avg={req_summary['avg']:.0f}ms "
                f"p95={req_summary['p95']:.0f}ms "
                f"n={req_summary['count']}"
            )
            err_summary = await self._agg_for("request", "error_count", since_1h)
            if err_summary["count"] > 0:
                lines[-1] += f" err={int(err_summary['sum'])}"
        else:
            lines.append("REQ n=0")

        # LLM summary
        llm_latency = await self._agg_for("llm", "latency", since_1h)
        llm_toks = await self._agg_for("llm", "tokens_per_second", since_1h)
        if llm_latency["count"] > 0:
            toks_str = f"tok/s={llm_toks['avg']:.1f}" if llm_toks["count"] > 0 else "tok/s=?"
            lines.append(
                f"LLM avg={llm_latency['avg']:.1f}s "
                f"{toks_str} "
                f"calls={llm_latency['count']}"
            )
        else:
            lines.append("LLM calls=0")

        # Pipeline summary: latest run per stage
        pip_summary = await self._pipeline_stage_summary(since_1h)
        if pip_summary:
            parts = []
            for stage_short, info in pip_summary.items():
                if info["status"] == "OK":
                    parts.append(f"{stage_short}=OK({info['elapsed']:.1f}s)")
                else:
                    parts.append(f"{stage_short}={info['status']}")
            lines.append("PIP " + " ".join(parts))

        # Resource summary: latest values
        res_parts = []
        latest_cpu = await self._latest_value("resource", "cpu_percent")
        if latest_cpu is not None:
            res_parts.append(f"cpu={latest_cpu:.0f}%")
        latest_ram = await self._latest_value("resource", "ram_used_gb")
        if latest_ram is not None:
            res_parts.append(f"ram={latest_ram:.1f}GB")
        latest_gpu = await self._latest_value("resource", "gpu_percent")
        if latest_gpu is not None:
            res_parts.append(f"gpu={latest_gpu:.0f}%")
        latest_gpu_mem = await self._latest_value("resource", "gpu_mem_used_gb")
        if latest_gpu_mem is not None:
            res_parts.append(f"gpu_mem={latest_gpu_mem:.1f}GB")
        if res_parts:
            lines.append("RES " + " ".join(res_parts))

        return "\n".join(lines)

    async def cleanup_old(self, days: int = 30) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM metrics WHERE timestamp < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            await db.commit()
        return deleted

    # -- Internal helpers ------------------------------------------------------

    async def _agg_for(
        self, category: str, name: str, since: str,
    ) -> dict:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*), AVG(value), MIN(value), MAX(value), SUM(value) "
                "FROM metrics WHERE category = ? AND name = ? AND timestamp >= ?",
                (category, name, since),
            )
            row = await cursor.fetchone()
            if not row or row[0] == 0:
                return {"count": 0, "avg": 0, "min": 0, "max": 0, "sum": 0, "p95": 0}

            count = row[0]
            p95_offset = max(0, int(count * 0.95) - 1)
            cursor = await db.execute(
                "SELECT value FROM metrics "
                "WHERE category = ? AND name = ? AND timestamp >= ? "
                "ORDER BY value ASC LIMIT 1 OFFSET ?",
                (category, name, since, p95_offset),
            )
            p95_row = await cursor.fetchone()

        return {
            "count": count,
            "avg": row[1] or 0,
            "min": row[2] or 0,
            "max": row[3] or 0,
            "sum": row[4] or 0,
            "p95": p95_row[0] if p95_row else 0,
        }

    async def _pipeline_stage_summary(self, since: str) -> dict:
        _SHORT = {
            "extract": "ext", "profile": "prf", "parse": "prs",
            "resolve": "res", "taxonomy": "tax", "standards": "std",
            "graph": "grf", "vectorstore": "vec", "eval": "evl",
        }
        result: dict[str, dict] = {}
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT tags, value FROM metrics "
                "WHERE category = 'pipeline' AND name = 'stage_duration' "
                "AND timestamp >= ? ORDER BY timestamp DESC",
                (since,),
            )
            rows = await cursor.fetchall()

        seen: set[str] = set()
        for tags_json, value in rows:
            try:
                tags = json.loads(tags_json) if tags_json else {}
            except (json.JSONDecodeError, TypeError):
                continue
            stage = tags.get("stage", "")
            if stage in seen:
                continue
            seen.add(stage)
            short = _SHORT.get(stage, stage[:3])
            result[short] = {
                "elapsed": value,
                "status": tags.get("status", "?"),
            }
        return result

    async def _latest_value(
        self, category: str, name: str,
    ) -> float | None:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT value FROM metrics "
                "WHERE category = ? AND name = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (category, name),
            )
            row = await cursor.fetchone()
        return row[0] if row else None
