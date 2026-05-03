"""Web UI configuration.

Loads settings from config/web.json with sensible defaults for
development. The path_mappings field translates Windows UNC paths
(used by team members) to Linux mount points on the server.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# core/src/web/config.py -> core/src/web/ -> core/src/ -> core/ -> <repo_root>
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "web.json"
DEFAULT_ENV_JSON_PATH = PROJECT_ROOT / "config" / "env.json"

# Env var names used for DB-path overrides. The CLI flags
# (--jobs-db / --metrics-db / --feedback-db in app.__main__) feed
# into these by setting them before uvicorn spawns the worker, so
# the effective resolution chain is:
#   CLI flag > matching env var > config/env.json > <env_dir>/state/<default>.
_ENV_VAR_JOBS_DB = "NORA_JOBS_DB"
_ENV_VAR_METRICS_DB = "NORA_METRICS_DB"
_ENV_VAR_FEEDBACK_DB = "NORA_FEEDBACK_DB"


@dataclass
class PathMapping:
    """Maps a Windows network path to a Linux mount point."""

    windows: str
    linux: str
    label: str


@dataclass
class EnvJsonConfig:
    """Per-environment config loaded from `config/env.json`. All
    fields default to "" (fall through). Treated as a config layer
    sitting between env-vars and computed-defaults — see
    `load_config` for the full priority chain."""

    env_dir: str = ""
    jobs_db: str = ""
    metrics_db: str = ""
    feedback_db: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> EnvJsonConfig:
        config_path = path or DEFAULT_ENV_JSON_PATH
        if not config_path.exists():
            return cls()
        try:
            with open(config_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            logger.warning("Could not parse %s: %s — using empty defaults",
                           config_path, e)
            return cls()
        return cls(
            env_dir=str(data.get("env_dir", "") or "").strip(),
            jobs_db=str(data.get("jobs_db", "") or "").strip(),
            metrics_db=str(data.get("metrics_db", "") or "").strip(),
            feedback_db=str(data.get("feedback_db", "") or "").strip(),
        )


@dataclass
class WebConfig:
    """Web application configuration.

    `env_dir` (D-022) is the per-Web-UI runtime root: jobs and metrics SQLite
    databases live under `<env_dir>/state/`. Pipeline jobs may target different
    env_dirs at submission time, but Web-UI state always tracks under the
    configured one.

    The three DB-path fields (`jobs_db`, `metrics_db`, `feedback_db`)
    are resolved overrides — `""` means the path resolver fell
    through to the computed `<env_dir>/state/<default>.db` default.
    """

    host: str = "0.0.0.0"
    port: int = 8000
    root_path: str = ""
    path_mappings: list[PathMapping] = field(default_factory=list)
    ollama_url: str = "http://localhost:11434"
    default_model: str = "gemma3:12b"
    env_dir: str = ""
    # DB path overrides (empty → fall through to computed default).
    # Resolution happens in `load_config`; runtime code reads via the
    # *_db_path() helpers which honor these values.
    jobs_db: str = ""
    metrics_db: str = ""
    feedback_db: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> WebConfig:
        mappings = [
            PathMapping(**m) for m in data.get("path_mappings", [])
        ]
        return cls(
            host=data.get("host", cls.host),
            port=data.get("port", cls.port),
            root_path=data.get("root_path", cls.root_path),
            path_mappings=mappings,
            ollama_url=data.get("ollama_url", cls.ollama_url),
            default_model=data.get("default_model", cls.default_model),
            env_dir=data.get("env_dir", cls.env_dir),
        )

    # --- Derived paths (D-022) ---

    def env_dir_path(self) -> Path:
        return Path(self.env_dir).resolve() if self.env_dir else PROJECT_ROOT

    def state_path(self) -> Path:
        return self.env_dir_path() / "state"

    def jobs_db_path(self) -> Path:
        if self.jobs_db:
            return Path(self.jobs_db)
        return self.state_path() / "nora.db"

    def metrics_db_path(self) -> Path:
        if self.metrics_db:
            return Path(self.metrics_db)
        return self.state_path() / "nora_metrics.db"

    def feedback_db_path(self) -> Path:
        """SQLite path for the Test page's question/answer/vote/feedback log."""
        if self.feedback_db:
            return Path(self.feedback_db)
        return self.state_path() / "nora_test_feedback.db"


def load_config(path: Path | None = None) -> WebConfig:
    """Load config from JSON file, falling back to defaults.

    `env_dir` resolution order (highest priority first):
      1. `env_dir` field in `config/web.json`
      2. `ENV_DIR` environment variable
      3. `env_dir` field in `config/env.json`

    The CLI `--env-dir <path>` flag (handled in `app.__main__`)
    feeds into step 2 by setting `ENV_DIR` before uvicorn spawns
    the worker.

    DB-path overrides (jobs/metrics/feedback) — per-DB resolution
    (highest priority first):
      1. CLI flag (--jobs-db / --metrics-db / --feedback-db)
      2. Matching env var (NORA_JOBS_DB / NORA_METRICS_DB / NORA_FEEDBACK_DB)
      3. Field in `config/env.json`
      4. Computed default: `<env_dir>/state/<default>.db`

    Steps 1 + 2 are unified in this function: the CLI flags in
    `__main__` set the env vars before the worker re-imports, so
    the worker only needs to read env vars.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.exists():
        logger.info("Loading web config from %s", config_path)
        with open(config_path) as f:
            cfg = WebConfig.from_dict(json.load(f))
    else:
        logger.warning("Config file %s not found, using defaults", config_path)
        cfg = WebConfig()

    env_json = EnvJsonConfig.load()

    # env_dir: web.json > $ENV_DIR > env.json
    if not cfg.env_dir:
        env_var = os.environ.get("ENV_DIR", "").strip()
        if env_var:
            logger.info("env_dir not in web.json; using $ENV_DIR=%s", env_var)
            cfg.env_dir = env_var
        elif env_json.env_dir:
            logger.info("env_dir from config/env.json: %s", env_json.env_dir)
            cfg.env_dir = env_json.env_dir

    # DB paths: env var > env.json > "" (computed default)
    cfg.jobs_db = _resolve_db_path(_ENV_VAR_JOBS_DB, env_json.jobs_db, "jobs")
    cfg.metrics_db = _resolve_db_path(_ENV_VAR_METRICS_DB, env_json.metrics_db, "metrics")
    cfg.feedback_db = _resolve_db_path(_ENV_VAR_FEEDBACK_DB, env_json.feedback_db, "feedback")

    return cfg


def _resolve_db_path(env_var: str, env_json_value: str, label: str) -> str:
    """Pick the highest-priority override for a DB path. Returns ""
    when no override is set (the WebConfig.<db>_db_path() helper
    will then fall through to the computed default)."""
    env_val = os.environ.get(env_var, "").strip()
    if env_val:
        logger.info("%s_db override from $%s: %s", label, env_var, env_val)
        return env_val
    if env_json_value:
        logger.info("%s_db override from config/env.json: %s", label, env_json_value)
        return env_json_value
    return ""
