"""Tests for `core/src/web/config.py` — env_dir + DB-path resolution.

DB-path resolution chain (highest priority first), per DB:
  1. CLI flag (handled in app.__main__ — sets the env var)
  2. Matching env var (NORA_JOBS_DB / NORA_METRICS_DB / NORA_FEEDBACK_DB)
  3. Field in `config/env.json`
  4. Computed default `<env_dir>/state/<name>.db`

These tests exercise layers 2-4 directly (CLI → env var is just an
os.environ assignment in __main__).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.src.web.config import (
    EnvJsonConfig,
    WebConfig,
    load_config,
)


@pytest.fixture
def clean_env(monkeypatch):
    """Unset every env var the resolver looks at so each test starts
    from a clean slate."""
    for v in ("ENV_DIR", "NORA_JOBS_DB", "NORA_METRICS_DB", "NORA_FEEDBACK_DB"):
        monkeypatch.delenv(v, raising=False)


@pytest.fixture
def isolated_configs(tmp_path, monkeypatch):
    """Point the loader at a temp config dir so we can write
    web.json / env.json freely without touching the real ones."""
    web_path = tmp_path / "web.json"
    env_path = tmp_path / "env.json"
    monkeypatch.setattr(
        "core.src.web.config.DEFAULT_CONFIG_PATH", web_path,
    )
    monkeypatch.setattr(
        "core.src.web.config.DEFAULT_ENV_JSON_PATH", env_path,
    )
    return {"web": web_path, "env": env_path}


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# DB-path resolution priority
# ---------------------------------------------------------------------------


def test_db_paths_default_to_env_dir_state(clean_env, isolated_configs):
    """No overrides anywhere → fall through to <env_dir>/state/<default>.db
    for all three DBs. This is the legacy behavior and must remain
    untouched when nothing is configured."""
    _write(isolated_configs["web"], {"env_dir": "/tmp/myenv"})
    cfg = load_config()
    assert cfg.jobs_db_path() == Path("/tmp/myenv/state/nora.db")
    assert cfg.metrics_db_path() == Path("/tmp/myenv/state/nora_metrics.db")
    assert cfg.feedback_db_path() == Path("/tmp/myenv/state/nora_test_feedback.db")


def test_env_var_overrides_state_default(clean_env, isolated_configs, monkeypatch):
    """`NORA_JOBS_DB` set in the environment → that path is used,
    even when env_dir is configured (the env var bypasses state/)."""
    _write(isolated_configs["web"], {"env_dir": "/tmp/myenv"})
    monkeypatch.setenv("NORA_JOBS_DB", "/var/lib/custom-jobs.sqlite")
    monkeypatch.setenv("NORA_FEEDBACK_DB", "/var/lib/feedback.sqlite")
    cfg = load_config()
    assert cfg.jobs_db_path() == Path("/var/lib/custom-jobs.sqlite")
    assert cfg.feedback_db_path() == Path("/var/lib/feedback.sqlite")
    # Untouched DB still uses computed default
    assert cfg.metrics_db_path() == Path("/tmp/myenv/state/nora_metrics.db")


def test_env_json_overrides_state_default(clean_env, isolated_configs):
    """When the env var is unset and `config/env.json` has a value,
    use it. This is the fallback for users who prefer file-based
    config over env vars."""
    _write(isolated_configs["web"], {"env_dir": "/tmp/myenv"})
    _write(isolated_configs["env"], {
        "metrics_db": "/srv/data/metrics.db",
        "feedback_db": "/srv/data/feedback.db",
    })
    cfg = load_config()
    assert cfg.metrics_db_path() == Path("/srv/data/metrics.db")
    assert cfg.feedback_db_path() == Path("/srv/data/feedback.db")
    # Untouched DB still uses computed default
    assert cfg.jobs_db_path() == Path("/tmp/myenv/state/nora.db")


def test_env_var_beats_env_json(clean_env, isolated_configs, monkeypatch):
    """When BOTH the env var and env.json are set, env var wins.
    Mirrors the docstring's stated priority (CLI > env var > env.json
    > computed default)."""
    _write(isolated_configs["web"], {"env_dir": "/tmp/myenv"})
    _write(isolated_configs["env"], {"jobs_db": "/from/env-json.db"})
    monkeypatch.setenv("NORA_JOBS_DB", "/from/env-var.db")
    cfg = load_config()
    assert cfg.jobs_db_path() == Path("/from/env-var.db")


def test_empty_strings_in_env_json_fall_through(clean_env, isolated_configs):
    """Empty strings in env.json mean "fall through" — must not
    poison the resolver into using `Path("")`."""
    _write(isolated_configs["web"], {"env_dir": "/tmp/myenv"})
    _write(isolated_configs["env"], {
        "jobs_db": "",
        "metrics_db": "",
        "feedback_db": "",
    })
    cfg = load_config()
    # All fall through to computed defaults under <env_dir>/state/
    assert cfg.jobs_db_path() == Path("/tmp/myenv/state/nora.db")
    assert cfg.metrics_db_path() == Path("/tmp/myenv/state/nora_metrics.db")
    assert cfg.feedback_db_path() == Path("/tmp/myenv/state/nora_test_feedback.db")


def test_env_json_missing_treated_as_no_overrides(clean_env, isolated_configs):
    """If `config/env.json` doesn't exist, the resolver should silently
    fall through. Don't require users to create the file."""
    _write(isolated_configs["web"], {"env_dir": "/tmp/myenv"})
    # No env.json written
    assert not isolated_configs["env"].exists()
    cfg = load_config()
    assert cfg.jobs_db_path() == Path("/tmp/myenv/state/nora.db")


def test_env_json_malformed_treated_as_no_overrides(clean_env, isolated_configs):
    """Garbage in env.json → log + treat as empty defaults. Must not
    crash app startup just because the file has a typo."""
    _write(isolated_configs["web"], {"env_dir": "/tmp/myenv"})
    isolated_configs["env"].write_text("{ not valid json")
    cfg = load_config()
    assert cfg.jobs_db_path() == Path("/tmp/myenv/state/nora.db")


# ---------------------------------------------------------------------------
# env_dir resolution still uses its own chain
# ---------------------------------------------------------------------------


def test_env_dir_falls_back_to_env_json_when_web_and_envvar_empty(
    clean_env, isolated_configs,
):
    """env_dir chain: web.json > $ENV_DIR > env.json. With web.json
    empty and $ENV_DIR unset, env.json wins."""
    _write(isolated_configs["web"], {"env_dir": ""})
    _write(isolated_configs["env"], {"env_dir": "/from/env-json"})
    cfg = load_config()
    assert cfg.env_dir == "/from/env-json"


def test_env_dir_envvar_beats_env_json(clean_env, isolated_configs, monkeypatch):
    """$ENV_DIR sits above env.json in the chain."""
    _write(isolated_configs["web"], {"env_dir": ""})
    _write(isolated_configs["env"], {"env_dir": "/from/env-json"})
    monkeypatch.setenv("ENV_DIR", "/from/env-var")
    cfg = load_config()
    assert cfg.env_dir == "/from/env-var"


def test_web_json_env_dir_beats_everything(clean_env, isolated_configs, monkeypatch):
    """Top of the chain — web.json's env_dir is non-empty → wins
    over both $ENV_DIR and env.json."""
    _write(isolated_configs["web"], {"env_dir": "/from/web-json"})
    _write(isolated_configs["env"], {"env_dir": "/from/env-json"})
    monkeypatch.setenv("ENV_DIR", "/from/env-var")
    cfg = load_config()
    assert cfg.env_dir == "/from/web-json"


# ---------------------------------------------------------------------------
# EnvJsonConfig parsing
# ---------------------------------------------------------------------------


def test_env_json_config_load_missing_returns_defaults(tmp_path):
    nonexistent = tmp_path / "no-such-file.json"
    cfg = EnvJsonConfig.load(nonexistent)
    assert cfg == EnvJsonConfig()


def test_env_json_config_load_strips_whitespace(tmp_path):
    """Trailing whitespace in db path values is stripped — guards
    against CRLF / trailing-space user errors."""
    p = tmp_path / "env.json"
    p.write_text(json.dumps({
        "env_dir": "  /trimmed  ",
        "jobs_db": " /trimmed-db ",
    }))
    cfg = EnvJsonConfig.load(p)
    assert cfg.env_dir == "/trimmed"
    assert cfg.jobs_db == "/trimmed-db"


def test_web_config_db_path_overrides_are_absolute_paths(clean_env, isolated_configs):
    """When a DB path override is set, the helper returns it as-is
    (no joining with state_path). This is the contract: overrides
    ARE the full path."""
    _write(isolated_configs["web"], {"env_dir": "/tmp/myenv"})
    _write(isolated_configs["env"], {"jobs_db": "/some/where/else.db"})
    cfg = load_config()
    p = cfg.jobs_db_path()
    # Returned path is exactly the override, not joined under state_path()
    assert p == Path("/some/where/else.db")
    assert "state" not in str(p)
