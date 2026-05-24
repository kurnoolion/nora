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


def test_resolve_llm_provider_db_beats_env_var(tmp_path, monkeypatch):
    """Config-page DB sits **above** env var (deviation from the
    documented D-053 ordering) for LLM params so values saved through
    the Config page actually take effect at query time."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.setenv(env_cfg.LLM_PROVIDER_ENV_VAR, "ollama")
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        # DB has a value → wins over env var
        assert env_cfg.resolve_llm_provider(
            config_store_value="openai-compatible"
        ) == "openai-compatible"
        # DB unset (None / empty) → falls through to env var
        assert env_cfg.resolve_llm_provider(config_store_value=None) == "ollama"
        assert env_cfg.resolve_llm_provider(config_store_value="") == "ollama"
        # CLI still beats DB
        assert env_cfg.resolve_llm_provider(
            cli_value="mock",
            config_store_value="openai-compatible",
        ) == "mock"
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_llm_model_db_beats_env_var(tmp_path, monkeypatch):
    import json
    from core.src.env import config as env_cfg
    monkeypatch.setenv(env_cfg.LLM_MODEL_ENV_VAR, "gemma:7b")
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_llm_model(
            config_store_value="qwen:14b"
        ) == "qwen:14b"
        assert env_cfg.resolve_llm_model(config_store_value=None) == "gemma:7b"
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_llm_timeout_db_beats_env_var(tmp_path, monkeypatch):
    import json
    from core.src.env import config as env_cfg
    monkeypatch.setenv(env_cfg.LLM_TIMEOUT_ENV_VAR, "120")
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_llm_timeout(config_store_value=300) == 300
        assert env_cfg.resolve_llm_timeout(config_store_value=None) == 120
        # DB with "0" or non-positive falls through (treated as unset)
        assert env_cfg.resolve_llm_timeout(config_store_value=0) == 120
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_llm_base_url_3tier(tmp_path, monkeypatch):
    """CLI > Config-page DB > env var > config/llm.json > env config > ''."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.LLM_BASE_URL_ENV_VAR, raising=False)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        # Default
        assert env_cfg.resolve_llm_base_url() == ""
        # env config back-compat
        assert env_cfg.resolve_llm_base_url(
            env_config_value="http://ec/"
        ) == "http://ec/"
        # config file beats env config
        p.write_text(json.dumps({"llm_base_url": "http://cfg/"}))
        env_cfg._reset_llm_config_cache()
        assert env_cfg.resolve_llm_base_url(
            env_config_value="http://ec/"
        ) == "http://cfg/"
        # env var beats config file
        monkeypatch.setenv(env_cfg.LLM_BASE_URL_ENV_VAR, "http://env/")
        assert env_cfg.resolve_llm_base_url() == "http://env/"
        # DB beats env var
        assert env_cfg.resolve_llm_base_url(
            config_store_value="http://db/"
        ) == "http://db/"
        # CLI beats DB
        assert env_cfg.resolve_llm_base_url(
            cli_value="http://cli/",
            config_store_value="http://db/",
        ) == "http://cli/"
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_llm_api_key_db_beats_env_var(tmp_path, monkeypatch):
    import json
    from core.src.env import config as env_cfg
    monkeypatch.setenv(env_cfg.LLM_API_KEY_ENV_VAR, "envkey")
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_llm_api_key(config_store_value="dbkey") == "dbkey"
        assert env_cfg.resolve_llm_api_key(config_store_value=None) == "envkey"
        assert env_cfg.resolve_llm_api_key(config_store_value="") == "envkey"
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


def test_resolve_skip_resolve_3tier(tmp_path, monkeypatch):
    """CLI True > env var > config/llm.json > env config > False."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.SKIP_RESOLVE_ENV_VAR, raising=False)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_skip_resolve() is False
        assert env_cfg.resolve_skip_resolve(env_config_value=True) is True
        p.write_text(json.dumps({"skip_resolve": True}))
        env_cfg._reset_llm_config_cache()
        assert env_cfg.resolve_skip_resolve(env_config_value=False) is True
        p.write_text(json.dumps({"skip_resolve": False}))
        env_cfg._reset_llm_config_cache()
        monkeypatch.setenv(env_cfg.SKIP_RESOLVE_ENV_VAR, "1")
        assert env_cfg.resolve_skip_resolve(env_config_value=False) is True
        assert env_cfg.resolve_skip_resolve(cli_value=False) is False
        assert env_cfg.resolve_skip_resolve(cli_value=True) is True
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_skip_standards_3tier(tmp_path, monkeypatch):
    """CLI True > env var > config/llm.json > env config > False."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.SKIP_STANDARDS_ENV_VAR, raising=False)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_skip_standards() is False
        assert env_cfg.resolve_skip_standards(env_config_value=True) is True
        p.write_text(json.dumps({"skip_standards": True}))
        env_cfg._reset_llm_config_cache()
        assert env_cfg.resolve_skip_standards(env_config_value=False) is True
        p.write_text(json.dumps({"skip_standards": False}))
        env_cfg._reset_llm_config_cache()
        monkeypatch.setenv(env_cfg.SKIP_STANDARDS_ENV_VAR, "1")
        assert env_cfg.resolve_skip_standards(env_config_value=False) is True
        assert env_cfg.resolve_skip_standards(cli_value=False) is False
        assert env_cfg.resolve_skip_standards(cli_value=True) is True
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_reranker_enabled_3tier(tmp_path, monkeypatch):
    """env var > Config-page DB > config/llm.json > env config > False."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.RERANKER_ENABLED_ENV_VAR, raising=False)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_reranker_enabled() is False
        assert env_cfg.resolve_reranker_enabled(env_config_value=True) is True
        # config/llm.json beats env config
        p.write_text(json.dumps({"reranker_enabled": True}))
        env_cfg._reset_llm_config_cache()
        assert env_cfg.resolve_reranker_enabled(env_config_value=False) is True
        # Config-page DB beats config/llm.json
        p.write_text(json.dumps({"reranker_enabled": True}))
        env_cfg._reset_llm_config_cache()
        assert env_cfg.resolve_reranker_enabled(
            config_store_value=False
        ) is False
        # Env var is the strongest
        monkeypatch.setenv(env_cfg.RERANKER_ENABLED_ENV_VAR, "1")
        assert env_cfg.resolve_reranker_enabled(
            config_store_value=False
        ) is True
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_reranker_model_3tier(tmp_path, monkeypatch):
    """env var > Config-page DB > config/llm.json > env config > default."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.RERANKER_MODEL_ENV_VAR, raising=False)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        # Default
        assert env_cfg.resolve_reranker_model() == env_cfg.DEFAULT_RERANKER_MODEL
        # Env config back-compat
        assert env_cfg.resolve_reranker_model(
            env_config_value="from-env-config"
        ) == "from-env-config"
        # config/llm.json beats env config
        p.write_text(json.dumps({"reranker_model": "from-config-file"}))
        env_cfg._reset_llm_config_cache()
        assert env_cfg.resolve_reranker_model(
            env_config_value="from-env-config"
        ) == "from-config-file"
        # DB beats config/llm.json
        assert env_cfg.resolve_reranker_model(
            config_store_value="from-db",
            env_config_value="from-env-config",
        ) == "from-db"
        # Env var beats DB
        monkeypatch.setenv(env_cfg.RERANKER_MODEL_ENV_VAR, "from-env-var")
        assert env_cfg.resolve_reranker_model(
            config_store_value="from-db",
            env_config_value="from-env-config",
        ) == "from-env-var"
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_skip_taxonomy_cli_false_beats_config_file(tmp_path, monkeypatch):
    """``--no-skip-taxonomy`` (cli_value=False) FORCES the stage on even
    when config/llm.json has skip_taxonomy=True. Regression guard for
    the 'I removed --skip-taxonomy but it's still skipping' bug — user
    can now pass --no-skip-taxonomy to override a sticky config-file
    default without editing the file."""
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.SKIP_TAXONOMY_ENV_VAR, raising=False)
    monkeypatch.delenv(env_cfg.RAG_ONLY_ENV_VAR, raising=False)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({"skip_taxonomy": True}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        # Without CLI: config-file wins → skip = True
        assert env_cfg.resolve_skip_taxonomy() is True
        # Explicit --no-skip-taxonomy → cli_value=False → wins
        assert env_cfg.resolve_skip_taxonomy(cli_value=False) is False
    finally:
        env_cfg._reset_llm_config_cache()


def test_resolve_skip_graph_cli_false_beats_config_file(tmp_path, monkeypatch):
    import json
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.SKIP_GRAPH_ENV_VAR, raising=False)
    monkeypatch.delenv(env_cfg.RAG_ONLY_ENV_VAR, raising=False)
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({"skip_graph": True}))
    monkeypatch.setattr(env_cfg, "DEFAULT_LLM_CONFIG_PATH", p)
    env_cfg._reset_llm_config_cache()
    try:
        assert env_cfg.resolve_skip_graph() is True
        assert env_cfg.resolve_skip_graph(cli_value=False) is False
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


# ---------------------------------------------------------------------------
# RetrievalConfig + Phase-3-config resolvers (Step 3 grouping knobs)
# ---------------------------------------------------------------------------


def _isolate_retrieval_config(monkeypatch, tmp_path, **fields):
    """Helper — point DEFAULT_RETRIEVAL_CONFIG_PATH at a tmp file with
    given fields, reset the cache, return the path."""
    import json
    from core.src.env import config as env_cfg
    p = tmp_path / "retrieval.json"
    p.write_text(json.dumps(fields))
    monkeypatch.setattr(env_cfg, "DEFAULT_RETRIEVAL_CONFIG_PATH", p)
    env_cfg._reset_retrieval_config_cache()
    return p


def test_retrieval_config_load_missing_returns_defaults(tmp_path):
    from core.src.env.config import RetrievalConfig
    cfg = RetrievalConfig.load(tmp_path / "no-such.json")
    assert cfg.enable_grouping is None
    assert cfg.gap_threshold is None
    assert cfg.gap_threshold_by_type == {}


def test_retrieval_config_parses_fields(tmp_path):
    import json
    from core.src.env.config import RetrievalConfig
    p = tmp_path / "retrieval.json"
    p.write_text(json.dumps({
        "enable_grouping": True,
        "gap_threshold": 0.07,
        "gap_threshold_by_type": {"single_doc": 0.1, "cross_doc": 0.03},
    }))
    cfg = RetrievalConfig.load(p)
    assert cfg.enable_grouping is True
    assert cfg.gap_threshold == 0.07
    assert cfg.gap_threshold_by_type == {"single_doc": 0.1, "cross_doc": 0.03}


def test_resolve_grouping_enabled_3tier(monkeypatch, tmp_path):
    """CLI True > env var > config/retrieval.json > default (False)."""
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.GROUPING_ENABLED_ENV_VAR, raising=False)
    _isolate_retrieval_config(monkeypatch, tmp_path)
    try:
        # Default
        assert env_cfg.resolve_grouping_enabled() is False
        # File: True wins over default
        _isolate_retrieval_config(monkeypatch, tmp_path, enable_grouping=True)
        assert env_cfg.resolve_grouping_enabled() is True
        # Env var "0" beats file True
        monkeypatch.setenv(env_cfg.GROUPING_ENABLED_ENV_VAR, "0")
        assert env_cfg.resolve_grouping_enabled() is False
        # Env var "1" beats file False
        _isolate_retrieval_config(monkeypatch, tmp_path, enable_grouping=False)
        monkeypatch.setenv(env_cfg.GROUPING_ENABLED_ENV_VAR, "true")
        assert env_cfg.resolve_grouping_enabled() is True
        # CLI False overrides everything
        assert env_cfg.resolve_grouping_enabled(cli_value=False) is False
        # CLI True is the strongest
        assert env_cfg.resolve_grouping_enabled(cli_value=True) is True
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_resolve_gap_threshold_3tier(monkeypatch, tmp_path):
    """CLI > env > by_type > scalar in file > default (0.05)."""
    from core.src.env import config as env_cfg
    monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
    _isolate_retrieval_config(monkeypatch, tmp_path)
    try:
        # Default
        assert env_cfg.resolve_gap_threshold() == env_cfg.DEFAULT_GAP_THRESHOLD
        # Scalar in file
        _isolate_retrieval_config(monkeypatch, tmp_path, gap_threshold=0.08)
        assert env_cfg.resolve_gap_threshold() == 0.08
        # Per-type override beats scalar (when type matches)
        _isolate_retrieval_config(
            monkeypatch, tmp_path,
            gap_threshold=0.08,
            gap_threshold_by_type={"single_doc": 0.02},
        )
        assert env_cfg.resolve_gap_threshold(query_type="single_doc") == 0.02
        # Unknown type falls through to scalar
        assert env_cfg.resolve_gap_threshold(query_type="cross_doc") == 0.08
        # Env var beats file (any type)
        monkeypatch.setenv(env_cfg.GAP_THRESHOLD_ENV_VAR, "0.123")
        assert env_cfg.resolve_gap_threshold(query_type="single_doc") == 0.123
        # CLI beats env var
        assert env_cfg.resolve_gap_threshold(cli_value=0.3, query_type="single_doc") == 0.3
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_resolve_gap_threshold_invalid_env_var_falls_through(monkeypatch, tmp_path):
    """Bad env var value is logged and ignored; falls through to file default."""
    from core.src.env import config as env_cfg
    _isolate_retrieval_config(monkeypatch, tmp_path, gap_threshold=0.06)
    monkeypatch.setenv(env_cfg.GAP_THRESHOLD_ENV_VAR, "not-a-number")
    try:
        assert env_cfg.resolve_gap_threshold() == 0.06
    finally:
        env_cfg._reset_retrieval_config_cache()


# ── BM25 weight per-type resolver ─────────────────────────────


def test_resolve_bm25_weight_falls_back_to_pipeline_default(monkeypatch, tmp_path):
    """When DB / file have no override, uses pipeline._TYPE_BM25_WEIGHT."""
    from core.src.env import config as env_cfg
    from core.src.query.pipeline import _TYPE_BM25_WEIGHT
    from core.src.query.schema import QueryType
    _isolate_retrieval_config(monkeypatch, tmp_path)
    try:
        # FACT has built-in default 0.5
        assert env_cfg.resolve_bm25_weight(query_type="fact") == 0.5
        # CROSS_DOC is omitted from the dict → 0.0
        assert env_cfg.resolve_bm25_weight(query_type="cross_doc") == 0.0
        # Sanity: returned value matches the pipeline dict
        assert env_cfg.resolve_bm25_weight(query_type="summarize") == _TYPE_BM25_WEIGHT.get(QueryType.SUMMARIZE, 0.0)
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_resolve_bm25_weight_file_overrides_pipeline(monkeypatch, tmp_path):
    """A value in config/retrieval.json's bm25_weight_by_type wins over
    the pipeline built-in."""
    from core.src.env import config as env_cfg
    _isolate_retrieval_config(
        monkeypatch, tmp_path,
        bm25_weight_by_type={"fact": 0.9, "summarize": 0.0},
    )
    try:
        assert env_cfg.resolve_bm25_weight(query_type="fact") == 0.9
        assert env_cfg.resolve_bm25_weight(query_type="summarize") == 0.0
        # Types absent from the override dict still fall through to pipeline.
        assert env_cfg.resolve_bm25_weight(query_type="single_doc") == 0.5
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_resolve_bm25_weight_cli_beats_file(monkeypatch, tmp_path):
    """CLI flag is the highest priority."""
    from core.src.env import config as env_cfg
    _isolate_retrieval_config(
        monkeypatch, tmp_path,
        bm25_weight_by_type={"fact": 0.9},
    )
    try:
        assert env_cfg.resolve_bm25_weight(query_type="fact", cli_value=0.1) == 0.1
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_resolve_bm25_weight_unknown_query_type_returns_zero(monkeypatch, tmp_path):
    """Unknown / mistyped query_type falls through to 0.0 (pure dense)."""
    from core.src.env import config as env_cfg
    _isolate_retrieval_config(monkeypatch, tmp_path)
    try:
        assert env_cfg.resolve_bm25_weight(query_type="not_a_real_type") == 0.0
        assert env_cfg.resolve_bm25_weight(query_type=None) == 0.0
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_retrieval_config_parses_bm25_weight_by_type(tmp_path):
    """RetrievalConfig.load round-trips bm25_weight_by_type."""
    import json
    from core.src.env.config import RetrievalConfig
    p = tmp_path / "retrieval.json"
    p.write_text(json.dumps({
        "bm25_weight_by_type": {"single_doc": 0.7, "summarize": 0.1},
    }))
    cfg = RetrievalConfig.load(p)
    assert cfg.bm25_weight_by_type == {"single_doc": 0.7, "summarize": 0.1}


# ─── resolve_top_k (broad/narrow buckets) ──────────────────────────


def test_resolve_top_k_defaults(monkeypatch, tmp_path):
    """No DB, no env, no file overrides → 25 (broad) / 10 (narrow)."""
    from core.src.env import config as env_cfg
    _isolate_retrieval_config(monkeypatch, tmp_path)
    monkeypatch.delenv("NORA_BROAD_QUERY_TOP_K", raising=False)
    monkeypatch.delenv("NORA_NARROW_QUERY_TOP_K", raising=False)
    try:
        assert env_cfg.resolve_top_k("cross_doc") == 25
        assert env_cfg.resolve_top_k("feature_level") == 25
        assert env_cfg.resolve_top_k("summarize") == 25
        assert env_cfg.resolve_top_k("traceability") == 25
        assert env_cfg.resolve_top_k("release_diff") == 25
        assert env_cfg.resolve_top_k("single_doc") == 10
        assert env_cfg.resolve_top_k("fact") == 10
        assert env_cfg.resolve_top_k("general") == 10
        assert env_cfg.resolve_top_k(None) == 10  # unknown → narrow
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_resolve_top_k_env_overrides_default(monkeypatch, tmp_path):
    from core.src.env import config as env_cfg
    _isolate_retrieval_config(monkeypatch, tmp_path)
    monkeypatch.setenv("NORA_BROAD_QUERY_TOP_K", "40")
    monkeypatch.setenv("NORA_NARROW_QUERY_TOP_K", "7")
    try:
        assert env_cfg.resolve_top_k("cross_doc") == 40
        assert env_cfg.resolve_top_k("single_doc") == 7
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_resolve_top_k_db_overlay_beats_env(monkeypatch, tmp_path):
    """DB-overlaid value (set on RetrievalConfig directly) wins over env."""
    from core.src.env import config as env_cfg
    _isolate_retrieval_config(
        monkeypatch, tmp_path,
        broad_query_top_k=33, narrow_query_top_k=8,
    )
    monkeypatch.setenv("NORA_BROAD_QUERY_TOP_K", "40")
    monkeypatch.setenv("NORA_NARROW_QUERY_TOP_K", "5")
    try:
        # File-loaded values should be the cfg.broad/narrow values that
        # the DB overlay normally writes; either way, resolve_top_k must
        # return the cfg value when set.
        assert env_cfg.resolve_top_k("cross_doc") == 33
        assert env_cfg.resolve_top_k("single_doc") == 8
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_resolve_top_k_cli_beats_all(monkeypatch, tmp_path):
    from core.src.env import config as env_cfg
    _isolate_retrieval_config(
        monkeypatch, tmp_path,
        broad_query_top_k=33,
    )
    monkeypatch.setenv("NORA_BROAD_QUERY_TOP_K", "40")
    try:
        assert env_cfg.resolve_top_k("cross_doc", cli_value=99) == 99
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_resolve_top_k_invalid_env_falls_through(monkeypatch, tmp_path):
    """Non-int env value → default."""
    from core.src.env import config as env_cfg
    _isolate_retrieval_config(monkeypatch, tmp_path)
    monkeypatch.setenv("NORA_BROAD_QUERY_TOP_K", "not-a-number")
    monkeypatch.setenv("NORA_NARROW_QUERY_TOP_K", "0")  # 0 → invalid (> 0 required)
    try:
        assert env_cfg.resolve_top_k("cross_doc") == 25
        assert env_cfg.resolve_top_k("single_doc") == 10
    finally:
        env_cfg._reset_retrieval_config_cache()


def test_is_broad_query_type_classification():
    from core.src.env.config import is_broad_query_type
    # Broad
    for qt in (
        "cross_doc", "cross_mno_comparison", "standards_comparison",
        "feature_level", "summarize", "traceability", "release_diff",
    ):
        assert is_broad_query_type(qt), f"{qt} should be broad"
    # Narrow / unknown
    for qt in ("single_doc", "fact", "general", None, "", "bogus"):
        assert not is_broad_query_type(qt), f"{qt} should be narrow"


def test_retrieval_config_parses_top_k_fields(tmp_path):
    """RetrievalConfig.load round-trips broad/narrow_query_top_k."""
    import json
    from core.src.env.config import RetrievalConfig
    p = tmp_path / "retrieval.json"
    p.write_text(json.dumps({
        "broad_query_top_k": 30, "narrow_query_top_k": 5,
    }))
    cfg = RetrievalConfig.load(p)
    assert cfg.broad_query_top_k == 30
    assert cfg.narrow_query_top_k == 5


def test_config_store_seed_missing(tmp_path):
    """seed_missing writes only absent keys; subsequent calls are no-ops."""
    from core.src.web.config_db import ConfigStore
    db_path = tmp_path / "config.db"
    cs = ConfigStore(db_path)
    specs = [
        ("retrieval", "broad_query_top_k", 25),
        ("retrieval", "narrow_query_top_k", 10),
    ]
    n = cs.seed_missing(specs)
    assert n == 2
    assert cs.get("retrieval", "broad_query_top_k") == 25
    assert cs.get("retrieval", "narrow_query_top_k") == 10
    # Idempotent: a second call writes nothing
    n2 = cs.seed_missing(specs)
    assert n2 == 0
    # Explicitly written different value isn't overwritten
    cs.set("retrieval", "broad_query_top_k", 99, updated_by="test")
    n3 = cs.seed_missing(specs)
    assert n3 == 0
    assert cs.get("retrieval", "broad_query_top_k") == 99
