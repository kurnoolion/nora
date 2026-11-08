"""The jobs database schema, shared by the web and the ingest runner.

Standard library only: the ingest runner opens this database on the host
with `sqlite3`, the web through `aiosqlite`. Both call `ensure_schema`
on a plain `sqlite3` connection (the web does so from a thread), so the
DDL has one home and the additive migrations run from either side.

Tables:
- `jobs` — one row per job; `params` (JSON), `runner` and `heartbeat_at`
  are the runner's columns (an `ingest*` job is claimed by a host-side
  runner, not run in the web process).
- `job_logs` — the streamed log lines of a job.
- `ingests` — one row per self-serve ingest across its jobs (run, retry,
  flip, rollback, abandon): the state the Ingest page shows.
- `runners` — when each runner last polled and what it was running, so
  the page can say whether anyone will pick a job up.
"""

from __future__ import annotations

import sqlite3

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted',
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    environment TEXT,
    stages TEXT NOT NULL DEFAULT '[]',
    current_stage TEXT,
    query_text TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    result_summary TEXT,
    error_message TEXT,
    params TEXT,
    runner TEXT,
    heartbeat_at TEXT
)
"""

LOGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_logs (
    job_id TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (job_id, line_number),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
)
"""

INGESTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingests (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    mno TEXT NOT NULL,
    release TEXT NOT NULL,
    source TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    build TEXT,
    label TEXT,
    run_ref TEXT,
    recall_avg REAL,
    baseline REAL,
    verdict TEXT,
    verdict_detail TEXT,
    previous_label TEXT,
    flipped_at TEXT,
    rolled_back_at TEXT,
    error TEXT,
    callback_url TEXT
)
"""

RUNNERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS runners (
    name TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL,
    job_id TEXT
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_logs_job_id ON job_logs (job_id, line_number)",
    "CREATE INDEX IF NOT EXISTS idx_ingests_state ON ingests (state)",
    "CREATE INDEX IF NOT EXISTS idx_ingests_created ON ingests (created_at DESC)",
)

# Columns added after the first release of the jobs table: added with
# ALTER TABLE on an existing database (SQLite cannot add them in place
# otherwise). Order matters only for readability.
JOBS_ADDED_COLUMNS = (
    ("params", "TEXT"),
    ("runner", "TEXT"),
    ("heartbeat_at", "TEXT"),
)
INGESTS_ADDED_COLUMNS = (
    ("callback_url", "TEXT"),
)

# Ingest states a client is told about (webhook events `ingest.<state>`).
NOTIFY_STATES = ("running", "staged", "served", "rolled_back", "failed", "cancelled", "abandoned", "purged")

# The ingest lifecycle. `queued` → `running` → `staged` (gate passed or
# failed; the verdict says which) | `failed` | `cancelled`; `staged` →
# `served` (flip); `served` → `rolled_back`; `failed` → `running` (retry)
# | `abandoned`; `abandoned | rolled_back | failed | cancelled` → `purged`
# (every artifact of the ingest removed; the row stays as history).
INGEST_STATES = (
    "queued", "running", "staged", "served", "rolled_back",
    "failed", "cancelled", "abandoned", "purged",
)
PURGEABLE_STATES = ("abandoned", "rolled_back", "failed", "cancelled")

# Job types the runner claims; the web never runs these in-process.
RUNNER_JOB_TYPES = ("ingest", "ingest-flip", "ingest-rollback", "ingest-abandon", "ingest-purge")

# The stages of an `ingest` job, in order (the runner runs them, the
# page shows them), and the stage a failed one is retried from.
INGEST_STAGES = (
    "documents", "start", "parse", "prompts", "taxonomy", "enrich",
    "verify", "promote", "convert", "publish", "eval", "gate",
)
RETRY_FROM = {**{s: s for s in INGEST_STAGES}, "verify": "enrich"}


def ensure_schema(con: sqlite3.Connection) -> None:
    """Create the tables and indexes; add the columns an older `jobs`
    table lacks. Idempotent; commits."""
    con.execute(JOBS_SCHEMA)
    con.execute(LOGS_SCHEMA)
    con.execute(INGESTS_SCHEMA)
    con.execute(RUNNERS_SCHEMA)
    for table, added in (("jobs", JOBS_ADDED_COLUMNS), ("ingests", INGESTS_ADDED_COLUMNS)):
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in added:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    for ddl in INDEXES:
        con.execute(ddl)
    con.commit()
