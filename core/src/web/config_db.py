"""SQLite-backed config store for the web Config page.

Persists user-edited config values across web-app restarts so admins
don't have to re-edit `config/*.json` on every host. Slots into the
resolver chain between env vars and the JSON files (see
core/src/env/config.py for the full chain).

Path resolution:
  - `--config-db /path/to/config.db` CLI flag (highest)
  - `NORA_CONFIG_DB` env var
  - None (DB layer disabled; resolver chain falls through to JSON
    files as before — Config page renders read-only with a notice
    asking the admin to set the path)

Schema is a single key-value table:
  config_kv (
    module TEXT NOT NULL,
    key    TEXT NOT NULL,
    value  TEXT NOT NULL,    -- JSON-encoded so we round-trip int/
                                bool/float/list cleanly
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (module, key)
  )

Values are JSON-encoded so the layer can hold any of the dataclass
field types (str / int / float / bool / list). The reader decodes
on load.

Synchronous SQLite — config writes are infrequent (admin-driven via
the UI) and the DB is single-process. Aiosqlite would be overkill.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# All values JSON-encode/decode through these helpers so consumers
# always see the original Python type, not a string.
_JSON_DECODE_FALLBACK = object()


def _encode(value: Any) -> str:
    return json.dumps(value)


def _decode(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Defensive — if the column ever holds raw text, return it
        # as a string rather than crash callers.
        return text


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config_kv (
    module     TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (module, key)
);
CREATE INDEX IF NOT EXISTS idx_config_kv_module ON config_kv(module);
"""


class ConfigStore:
    """SQLite-backed key-value config store, scoped by (module, key)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    # ── Reads ────────────────────────────────────────────────────

    def get(self, module: str, key: str) -> Any | None:
        """Return decoded value or None if absent."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM config_kv WHERE module = ? AND key = ?",
                (module, key),
            ).fetchone()
        if row is None:
            return None
        return _decode(row["value"])

    def get_module(self, module: str) -> dict[str, Any]:
        """Return all (key → value) pairs for one module."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM config_kv WHERE module = ?",
                (module,),
            ).fetchall()
        return {r["key"]: _decode(r["value"]) for r in rows}

    def get_all(self) -> dict[tuple[str, str], Any]:
        """Return everything, indexed by (module, key) tuples."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT module, key, value FROM config_kv",
            ).fetchall()
        return {(r["module"], r["key"]): _decode(r["value"]) for r in rows}

    # ── Writes ───────────────────────────────────────────────────

    def set(
        self,
        module: str,
        key: str,
        value: Any,
        updated_by: str | None = None,
    ) -> None:
        """Upsert one (module, key) → value pair."""
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO config_kv (module, key, value, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(module, key) DO UPDATE SET
                    value      = excluded.value,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (module, key, _encode(value), ts, updated_by),
            )

    def delete(self, module: str, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM config_kv WHERE module = ? AND key = ?",
                (module, key),
            )

    # ── Bulk apply for startup hydration ─────────────────────────

    def apply_to_caches(self) -> None:
        """Overlay every stored value onto the in-memory config caches.

        Called at web-app startup after the JSON files have been
        loaded. Mutates the cached `LLMConfigFile` / `RetrievalConfig`
        instances so the existing resolver chain (CLI > env > config
        cache > legacy back-compat > default) automatically picks up
        the DB layer.

        Resolver-layer ordering note: env vars stay above this layer
        (they remain a hard admin override). Config-file JSON values
        sit below it (the DB is "the user edited this through the UI"
        — wins over the file but loses to env).
        """
        from core.src.env import config as env_cfg

        # Force JSON load before we override.
        llm_cfg = env_cfg._llm_config()
        retrieval_cfg = env_cfg._retrieval_config()

        for (module, key), value in self.get_all().items():
            if module == "llm" and hasattr(llm_cfg, key):
                setattr(llm_cfg, key, value)
            elif module == "retrieval" and hasattr(retrieval_cfg, key):
                setattr(retrieval_cfg, key, value)
            elif module == "pipeline":
                # `pipeline` knobs (top_k, max_distance_threshold) don't
                # live on the cached config dataclasses; they get
                # consumed by the web pipeline-build path directly.
                # Stored here so the Config page can edit them; read at
                # _build_pipeline time via this same ConfigStore.
                pass
            else:
                logger.debug(
                    "ConfigStore: ignoring unknown (module=%s, key=%s)",
                    module, key,
                )

    def seed_missing(
        self,
        specs: list[tuple[str, str, Any]],
        updated_by: str = "seed_from_env",
    ) -> int:
        """Write each (module, key, value) only when the key is absent.

        Idempotent — does NOT overwrite existing values. Returns the
        count of keys actually written. Used at web-app startup to seed
        a fresh ConfigStore with values from env vars / defaults so
        first-run behavior matches the runtime resolution chain.

        Once seeded, the DB is authoritative — subsequent runs use the
        stored value regardless of env-var changes (per the design in
        the nora-retrieval-parent-displacement strand).
        """
        written = 0
        for module, key, value in specs:
            if self.get(module, key) is None:
                self.set(module, key, value, updated_by=updated_by)
                written += 1
        return written

    def reapply_one(self, module: str, key: str) -> None:
        """After a single write, re-overlay just that value onto the
        appropriate cache. Cheaper than apply_to_caches() when the UI
        edits one field at a time."""
        from core.src.env import config as env_cfg

        value = self.get(module, key)
        if value is None:
            return
        if module == "llm":
            cfg = env_cfg._llm_config()
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        elif module == "retrieval":
            cfg = env_cfg._retrieval_config()
            if hasattr(cfg, key):
                setattr(cfg, key, value)
