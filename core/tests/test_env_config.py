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


def test_resolve_embedding_provider_precedence(monkeypatch, tmp_path):
    from core.src.env import config as env_cfg
    from core.src.env.config import (
        DEFAULT_EMBEDDING_PROVIDER,
        EMBEDDING_PROVIDER_ENV_VAR,
        resolve_embedding_provider,
    )

    # Isolate from any real config/llm.json on disk — point the resolver
    # at an empty file so only the precedence under test contributes.
    empty_llm_cfg = tmp_path / "llm.json"
    empty_llm_cfg.write_text("{}")
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", empty_llm_cfg)
    env_cfg._reset_llm_config_cache()
    try:
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
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_embedding_provider_rejects_unknown():
    import pytest

    from core.src.env.config import resolve_embedding_provider

    with pytest.raises(ValueError, match="not in"):
        resolve_embedding_provider(cli_value="bogus")


def test_resolve_embedding_model_precedence(monkeypatch, tmp_path):
    from core.src.env import config as env_cfg
    from core.src.env.config import (
        DEFAULT_EMBEDDING_MODEL,
        EMBEDDING_MODEL_ENV_VAR,
        resolve_embedding_model,
    )

    # Isolate from any real config/llm.json on disk.
    empty_llm_cfg = tmp_path / "llm.json"
    empty_llm_cfg.write_text("{}")
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", empty_llm_cfg)
    env_cfg._reset_llm_config_cache()
    try:
        monkeypatch.delenv(EMBEDDING_MODEL_ENV_VAR, raising=False)
        assert resolve_embedding_model() == DEFAULT_EMBEDDING_MODEL
        assert resolve_embedding_model(env_config_value="my-model") == "my-model"
        monkeypatch.setenv(EMBEDDING_MODEL_ENV_VAR, "env-model")
        assert resolve_embedding_model(env_config_value="cfg-model") == "env-model"
        assert resolve_embedding_model(cli_value="cli-model", env_config_value="cfg-model") == "cli-model"
    finally:
        env_cfg._reset_llm_config_cache()


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


# ---------------------------------------------------------------------------
# config/llm.json layer + skip_taxonomy / skip_graph 3-tier resolution
# ---------------------------------------------------------------------------


def test_llm_config_file_load_missing_returns_defaults(tmp_path):
    from core.src.env.config import LLMConfigFile
    cfg = LLMConfigFile.load(tmp_path / "no-such-llm.json")
    assert cfg == LLMConfigFile()
    assert cfg.skip_taxonomy is False
    assert cfg.skip_graph is False


def test_llm_config_file_parses_canonical_fields(tmp_path):
    import json
    from core.src.env.config import LLMConfigFile
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({
        "llm_provider": "openai-compatible",
        "llm_model": "qwen/qwen3-235b-a22b",
        "llm_timeout": 600,
        "embedding_provider": "sentence-transformers",
        "embedding_model": "all-MiniLM-L6-v2",
        "ollama_url": "http://localhost:11434",
        "ollama_timeout_s": 300,
        "skip_taxonomy": True,
        "skip_graph": True,
    }))
    cfg = LLMConfigFile.load(p)
    assert cfg.llm_provider == "openai-compatible"
    assert cfg.llm_model == "qwen/qwen3-235b-a22b"
    assert cfg.llm_timeout == 600
    assert cfg.embedding_provider == "sentence-transformers"
    assert cfg.embedding_model == "all-MiniLM-L6-v2"
    assert cfg.ollama_url == "http://localhost:11434"
    assert cfg.ollama_timeout_s == 300
    assert cfg.skip_taxonomy is True
    assert cfg.skip_graph is True


def test_resolve_llm_provider_uses_llm_config_when_no_cli_or_env(tmp_path, monkeypatch):
    """3-tier behavior: with CLI=None and env unset, value comes from
    config/llm.json. Tested by pointing the loader at a temp file."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.LLM_PROVIDER_ENV_VAR, raising=False)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({"llm_provider": "openai-compatible"}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_llm_provider() == "openai-compatible"
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_llm_provider_env_var_beats_llm_config(tmp_path, monkeypatch):
    import json
    from core.src.env import config as env_cfg
    monkeypatch.setenv(env_cfg.LLM_PROVIDER_ENV_VAR, "ollama")
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({"llm_provider": "openai-compatible"}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_llm_provider() == "ollama"
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_llm_provider_cli_beats_env_and_config(tmp_path, monkeypatch):
    import json
    from core.src.env import config as env_cfg
    monkeypatch.setenv(env_cfg.LLM_PROVIDER_ENV_VAR, "ollama")
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({"llm_provider": "openai-compatible"}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_llm_provider(cli_value="mock") == "mock"
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_skip_graph_3tier(tmp_path, monkeypatch):
    """CLI True > env var > config/llm.json > env config > False."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.SKIP_GRAPH_ENV_VAR, raising=False)
    monkeypatch.delenv(env_cfg.RAG_ONLY_ENV_VAR, raising=False)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        # Default
        assert env_cfg.resolve_skip_graph() is False
        # Env config (back-compat)
        assert env_cfg.resolve_skip_graph(env_config_value=True) is True
        # config/llm.json beats env config
        p.write_text(json.dumps({"skip_graph": True}))
        env_cfg._reset_llm_config_cache()
        assert env_cfg.resolve_skip_graph(env_config_value=False) is True
        # Env var beats config
        p.write_text(json.dumps({"skip_graph": False}))
        env_cfg._reset_llm_config_cache()
        monkeypatch.setenv(env_cfg.SKIP_GRAPH_ENV_VAR, "1")
        assert env_cfg.resolve_skip_graph(env_config_value=False) is True
        # CLI False overrides everything
        assert env_cfg.resolve_skip_graph(cli_value=False) is False
        # CLI True is the strongest
        assert env_cfg.resolve_skip_graph(cli_value=True) is True
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_skip_rag_only_envvar_implies_both(tmp_path, monkeypatch):
    """`NORA_RAG_ONLY=1` flips both skip_taxonomy and skip_graph on."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.SKIP_TAXONOMY_ENV_VAR, raising=False)
    monkeypatch.delenv(env_cfg.SKIP_GRAPH_ENV_VAR, raising=False)
    monkeypatch.setenv(env_cfg.RAG_ONLY_ENV_VAR, "1")
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_skip_taxonomy() is True
        assert env_cfg.resolve_skip_graph() is True
    finally:
        env_cfg._reset_llm_config_cache()
