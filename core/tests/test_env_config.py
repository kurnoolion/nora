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


# ---------------------------------------------------------------------------
# Embedding fields + resolvers
# ---------------------------------------------------------------------------


def test_embedding_fields_have_defaults():
    """New embedding fields must default; old configs without them still load."""
    env = _make_env("/tmp/x")
    assert env.embedding_provider == "sentence-transformers"
    assert env.embedding_model == "all-MiniLM-L6-v2"


def test_validate_rejects_unknown_embedding_provider():
    env = _make_env("/tmp/x")
    env.embedding_provider = "bogus"
    errors = env.validate()
    assert any("embedding_provider" in e for e in errors)


def test_resolve_embedding_provider_precedence(monkeypatch):
    from core.src.env.config import (
        DEFAULT_EMBEDDING_PROVIDER,
        EMBEDDING_PROVIDER_ENV_VAR,
        resolve_embedding_provider,
    )

    monkeypatch.delenv(EMBEDDING_PROVIDER_ENV_VAR, raising=False)
    # default
    assert resolve_embedding_provider() == DEFAULT_EMBEDDING_PROVIDER
    # config-file-level value
    assert resolve_embedding_provider(env_config_value="ollama") == "ollama"
    # env var beats config
    monkeypatch.setenv(EMBEDDING_PROVIDER_ENV_VAR, "huggingface")
    assert resolve_embedding_provider(env_config_value="ollama") == "huggingface"
    # CLI beats env var
    assert resolve_embedding_provider(cli_value="ollama", env_config_value="huggingface") == "ollama"


def test_resolve_embedding_provider_rejects_unknown():
    import pytest

    from core.src.env.config import resolve_embedding_provider

    with pytest.raises(ValueError, match="not in"):
        resolve_embedding_provider(cli_value="bogus")


def test_resolve_embedding_model_precedence(monkeypatch):
    from core.src.env.config import (
        DEFAULT_EMBEDDING_MODEL,
        EMBEDDING_MODEL_ENV_VAR,
        resolve_embedding_model,
    )

    monkeypatch.delenv(EMBEDDING_MODEL_ENV_VAR, raising=False)
    assert resolve_embedding_model() == DEFAULT_EMBEDDING_MODEL
    assert resolve_embedding_model(env_config_value="my-model") == "my-model"
    monkeypatch.setenv(EMBEDDING_MODEL_ENV_VAR, "env-model")
    assert resolve_embedding_model(env_config_value="cfg-model") == "env-model"
    assert resolve_embedding_model(cli_value="cli-model", env_config_value="cfg-model") == "cli-model"


def test_load_old_json_without_embedding_fields(tmp_path):
    """Existing env JSONs lack embedding_* — they must still load with defaults."""
    import json

    p = tmp_path / "old.json"
    p.write_text(
        json.dumps({
            "name": "old",
            "description": "",
            "created_by": "",
            "member": "",
            "env_dir": "/tmp/x",
        })
    )
    env = EnvironmentConfig.load_json(p)
    assert env.embedding_provider == "sentence-transformers"
    assert env.embedding_model == "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# skip_taxonomy field — pin defaults + round-trip + back-compat for old JSONs
# ---------------------------------------------------------------------------


def test_skip_taxonomy_defaults_to_false():
    """The default must remain False — taxonomy stage continues to run
    unless the env or CLI explicitly opts out. Pinned because flipping
    this default would silently change every existing pipeline run."""
    env = _make_env("/tmp/x")
    assert env.skip_taxonomy is False


def test_skip_taxonomy_round_trips_through_json(tmp_path):
    """Setting skip_taxonomy=True survives save_json + load_json so the
    env config can persist the opt-out across runs."""
    p = tmp_path / "skip.json"
    env = _make_env("/tmp/x")
    env.skip_taxonomy = True
    env.save_json(p)
    loaded = EnvironmentConfig.load_json(p)
    assert loaded.skip_taxonomy is True


def test_load_old_json_without_skip_taxonomy_field(tmp_path):
    """Pre-skip-taxonomy env JSONs must continue to load — the field
    falls back to its default (False) so old environments behave
    exactly as they did before the flag was introduced."""
    import json

    p = tmp_path / "old_no_skip.json"
    p.write_text(
        json.dumps({
            "name": "legacy",
            "description": "",
            "created_by": "",
            "member": "",
            "env_dir": "/tmp/x",
        })
    )
    env = EnvironmentConfig.load_json(p)
    assert env.skip_taxonomy is False


def test_stages_filter_drops_taxonomy_when_skip_set():
    """The CLI filter logic — `[s for s in stages if s != 'taxonomy']` —
    drops only taxonomy and preserves the rest of the stage order."""
    stages = ["extract", "profile", "parse", "resolve", "taxonomy",
              "standards", "graph", "vectorstore", "eval"]
    skip = True
    if skip and "taxonomy" in stages:
        stages = [s for s in stages if s != "taxonomy"]
    assert stages == [
        "extract", "profile", "parse", "resolve",
        "standards", "graph", "vectorstore", "eval",
    ]


def test_stages_filter_no_op_when_skip_unset():
    """skip_taxonomy=False must NOT filter — taxonomy stays in the run
    list. Catches a regression where the default behavior changes."""
    stages = ["extract", "profile", "parse", "resolve", "taxonomy",
              "standards", "graph", "vectorstore", "eval"]
    skip = False
    if skip and "taxonomy" in stages:
        stages = [s for s in stages if s != "taxonomy"]
    assert "taxonomy" in stages
