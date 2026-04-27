"""Tests for `EnvironmentConfig` path helpers — focused on `~` expansion."""

from __future__ import annotations

import os
from pathlib import Path

from core.src.env.config import EnvironmentConfig


def _make_env(env_dir: str) -> EnvironmentConfig:
    return EnvironmentConfig(
        name="test",
        description="",
        created_by="",
        member="",
        env_dir=env_dir,
    )


def test_env_dir_path_expands_tilde():
    """`env_dir` stored as `~/...` must resolve to an absolute home-relative path."""
    env = _make_env("~/nora-env-test")
    p = env.env_dir_path
    assert p.is_absolute()
    assert "~" not in str(p)
    expected_home = Path(os.path.expanduser("~")).resolve()
    assert str(p).startswith(str(expected_home))


def test_env_dir_path_handles_absolute():
    """Absolute paths pass through unchanged."""
    env = _make_env("/tmp/nora-env-test")
    p = env.env_dir_path
    assert p == Path("/tmp/nora-env-test").resolve()


def test_partition_paths_inherit_expansion():
    """`input_path` / `out_path` / etc. derive from the expanded env_dir."""
    env = _make_env("~/nora-env-test")
    assert "~" not in str(env.input_path("VZW", "Feb2026"))
    assert "~" not in str(env.out_path("extract"))
    assert "~" not in str(env.state_path())
    assert "~" not in str(env.corrections_path())
    assert "~" not in str(env.reports_path())
    assert "~" not in str(env.eval_path())


def test_pipeline_context_standalone_expands_tilde():
    """`PipelineContext.standalone(env_dir=~/...)` must expand the tilde too."""
    from core.src.pipeline.runner import PipelineContext

    ctx = PipelineContext.standalone(env_dir=Path("~/nora-env-test"))
    assert "~" not in str(ctx.documents_dir)
    assert "~" not in str(ctx.corrections_dir)
    assert ctx.documents_dir.is_absolute()
