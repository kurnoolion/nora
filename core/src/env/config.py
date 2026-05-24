"""Environment configuration for multi-user pipeline workflows.

An environment defines a workspace for a team member to run specific
pipeline stages against specific documents, with defined scope and objectives.

Per D-022, every environment is rooted at a single `env_dir` containing six
purpose-partitioned subdirectories: `input/`, `out/`, `state/`, `corrections/`,
`reports/`, `eval/`. The `env_dir` path is supplied per-environment (CLI,
config file, or Web UI form) — no hardcoded paths.

Usage:
    from core.src.env.config import EnvironmentConfig, PIPELINE_STAGES

    env = EnvironmentConfig(
        name="profiler-review",
        description="Verify profiler accuracy on new VZW docs",
        created_by="mohan",
        member="alice",
        env_dir="/data/vzw-new-batch",
        stage_start="extract",
        stage_end="parse",
        mnos=["VZW"],
        releases=["Feb2026"],
        objectives=["Verify heading detection", "Check table extraction"],
    )
    env.save_json(Path("environments/profiler-review.json"))
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# config/llm.json — single canonical home for LLM + embedding settings
# ---------------------------------------------------------------------------

# core/src/env/config.py -> core/src/env -> core/src -> core -> <repo_root>
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_LLM_CONFIG_PATH = _PROJECT_ROOT / "config" / "llm.json"


@dataclass
class LLMConfigFile:
    """Schema for `config/llm.json`. Empty/zero values mean "fall through".

    Treated as the lowest CLI/env-var-overridable layer. The legacy
    LLM fields under `environments/<name>.json` (`model_provider`,
    `model_name`, `model_timeout`, `embedding_provider`, `embedding_model`)
    are still honored as a back-compat fallback below this file but
    are deprecated — prefer this file going forward."""

    llm_provider: str = ""
    llm_model: str = ""
    llm_timeout: int = 0  # 0 = unset / fall through
    llm_base_url: str = ""
    llm_api_key: str = ""
    embedding_provider: str = ""
    embedding_model: str = ""
    ollama_url: str = ""
    ollama_timeout_s: int = 0  # 0 = unset / fall through
    skip_taxonomy: bool = False
    skip_graph: bool = False
    skip_resolve: bool = False
    skip_standards: bool = False
    reranker_enabled: bool = False
    reranker_model: str = ""
    reranker_provider: str = ""
    reranker_ollama_url: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> LLMConfigFile:
        config_path = path or DEFAULT_LLM_CONFIG_PATH
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
            llm_provider=str(data.get("llm_provider", "") or "").strip(),
            llm_model=str(data.get("llm_model", "") or "").strip(),
            llm_timeout=int(data.get("llm_timeout", 0) or 0),
            llm_base_url=str(data.get("llm_base_url", "") or "").strip(),
            llm_api_key=str(data.get("llm_api_key", "") or "").strip(),
            embedding_provider=str(data.get("embedding_provider", "") or "").strip(),
            embedding_model=str(data.get("embedding_model", "") or "").strip(),
            ollama_url=str(data.get("ollama_url", "") or "").strip(),
            ollama_timeout_s=int(data.get("ollama_timeout_s", 0) or 0),
            skip_taxonomy=bool(data.get("skip_taxonomy", False)),
            skip_graph=bool(data.get("skip_graph", False)),
            skip_resolve=bool(data.get("skip_resolve", False)),
            skip_standards=bool(data.get("skip_standards", False)),
            reranker_enabled=bool(data.get("reranker_enabled", False)),
            reranker_model=str(data.get("reranker_model", "") or "").strip(),
            reranker_provider=str(data.get("reranker_provider", "") or "").strip(),
            reranker_ollama_url=str(data.get("reranker_ollama_url", "") or "").strip(),
        )


# Module-level cache so repeat calls don't re-read the file. Tests can
# clear it via `_reset_llm_config_cache()`.
_LLM_CONFIG_CACHE: LLMConfigFile | None = None


def _llm_config() -> LLMConfigFile:
    global _LLM_CONFIG_CACHE
    if _LLM_CONFIG_CACHE is None:
        _LLM_CONFIG_CACHE = LLMConfigFile.load()
    return _LLM_CONFIG_CACHE


def _reset_llm_config_cache() -> None:
    """Test hook — drop the cached config so the next read picks up
    a freshly-written file."""
    global _LLM_CONFIG_CACHE
    _LLM_CONFIG_CACHE = None


# ---------------------------------------------------------------------------
# config/retrieval.json — single canonical home for retrieval-pipeline
# tunables (toggles + thresholds). Per-knob 3-tier resolution:
#   CLI flag > NORA_RETRIEVAL_<KNOB> env var > config/retrieval.json
#   > built-in default. Per-type overrides under *_by_type maps in the
#   file; absent type falls through to the scalar default.
# ---------------------------------------------------------------------------

DEFAULT_RETRIEVAL_CONFIG_PATH = _PROJECT_ROOT / "config" / "retrieval.json"


@dataclass
class RetrievalConfig:
    """Schema for `config/retrieval.json`.

    Phase 3-config seed: only Step 3's grouping knobs are wired here.
    Other retrieval tunables (top_k, BM25 weight, max_distance_threshold,
    rerank/rewrite toggles) migrate in Phase 4 and will share this file.
    See `core/src/query/grouping.py` for grouping semantics.

    Empty/None values mean "fall through" — the resolver chain consults
    env vars and built-in defaults below this file.
    """

    enable_grouping: bool | None = None
    """Toggle for Stage 4.7 hierarchy-based grouping. When True, retrieved
    chunks are clustered by longest-common hierarchy_path prefix; when
    the gap between top groups is below `gap_threshold`, the pipeline
    short-circuits with a disambiguation response instead of synthesizing
    one collapsed answer. None / unset means "use default" (currently False
    for backward compat)."""

    gap_threshold: float | None = None
    """Distance gap below which the pipeline returns disambiguation
    instead of auto-committing to the top group. None / unset → default
    (0.05). Specific to the embedding model + corpus distance distribution;
    re-tune when those change."""

    gap_threshold_by_type: dict[str, float] = field(default_factory=dict)
    """Per-QueryType override for `gap_threshold`. Keys are
    `QueryType.value` strings (e.g. "single_doc", "cross_doc"). Lookup
    falls through to the scalar `gap_threshold` when a type is absent.
    Reserved for Step 4 intent classification (Fact intent will likely
    want a stricter gap than general queries)."""

    bm25_weight_by_type: dict[str, float] = field(default_factory=dict)
    """Per-QueryType override for the BM25 weight in RRF fusion. Keys
    are `QueryType.value` strings. Empty dict (default) means
    `pipeline._TYPE_BM25_WEIGHT` built-in defaults apply. Set entries
    here to override per-type without code changes — useful for
    tuning hybrid retrieval against a new corpus where the default
    empirical values are off."""

    broad_query_top_k: int | None = None
    """Number of chunks to retrieve for broad / breadth queries
    (CROSS_DOC, CROSS_MNO_COMPARISON, STANDARDS_COMPARISON,
    FEATURE_LEVEL, SUMMARIZE, TRACEABILITY, RELEASE_DIFF). None /
    unset means "fall through" — the resolver consults the
    NORA_BROAD_QUERY_TOP_K env var, then the built-in default (25).
    This field is populated by the ConfigStore overlay when the web
    app runs with --config-db; the pipeline runner sees None here
    and falls through to env / default."""

    narrow_query_top_k: int | None = None
    """Number of chunks to retrieve for narrow / lookup queries
    (SINGLE_DOC, FACT, GENERAL). None / unset means "fall through" —
    the resolver consults NORA_NARROW_QUERY_TOP_K, then the
    built-in default (10). Same overlay semantics as
    `broad_query_top_k`."""

    @classmethod
    def load(cls, path: Path | None = None) -> RetrievalConfig:
        config_path = path or DEFAULT_RETRIEVAL_CONFIG_PATH
        if not config_path.exists():
            return cls()
        try:
            with open(config_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            logger.warning("Could not parse %s: %s — using empty defaults",
                           config_path, e)
            return cls()

        eg = data.get("enable_grouping")
        gt = data.get("gap_threshold")
        gtbt = data.get("gap_threshold_by_type") or {}
        bm25bt = data.get("bm25_weight_by_type") or {}
        bq = data.get("broad_query_top_k")
        nq = data.get("narrow_query_top_k")
        return cls(
            enable_grouping=bool(eg) if eg is not None else None,
            gap_threshold=float(gt) if gt is not None else None,
            gap_threshold_by_type={
                str(k): float(v) for k, v in gtbt.items()
                if isinstance(v, (int, float))
            },
            bm25_weight_by_type={
                str(k): float(v) for k, v in bm25bt.items()
                if isinstance(v, (int, float))
            },
            broad_query_top_k=int(bq) if isinstance(bq, (int, float)) and bq > 0 else None,
            narrow_query_top_k=int(nq) if isinstance(nq, (int, float)) and nq > 0 else None,
        )


# Module-level cache so repeat calls don't re-read the file.
_RETRIEVAL_CONFIG_CACHE: RetrievalConfig | None = None


def _retrieval_config() -> RetrievalConfig:
    global _RETRIEVAL_CONFIG_CACHE
    if _RETRIEVAL_CONFIG_CACHE is None:
        _RETRIEVAL_CONFIG_CACHE = RetrievalConfig.load()
    return _RETRIEVAL_CONFIG_CACHE


def _reset_retrieval_config_cache() -> None:
    """Test hook — drop the cached config so the next read picks up
    a freshly-written file."""
    global _RETRIEVAL_CONFIG_CACHE
    _RETRIEVAL_CONFIG_CACHE = None


# ---------------------------------------------------------------------------
# Retrieval knobs — Phase 3-config (Step 3 only)
# ---------------------------------------------------------------------------

GROUPING_ENABLED_ENV_VAR: str = "NORA_RETRIEVAL_GROUPING_ENABLED"
GAP_THRESHOLD_ENV_VAR: str = "NORA_RETRIEVAL_GAP_THRESHOLD"
DEFAULT_ENABLE_GROUPING: bool = False
DEFAULT_GAP_THRESHOLD: float = 0.05


def resolve_grouping_enabled(cli_value: bool | None = None) -> bool:
    """Resolve whether Stage 4.7 grouping is enabled.

    Precedence: --enable-grouping / --no-grouping CLI flag >
    NORA_RETRIEVAL_GROUPING_ENABLED env var > config/retrieval.json
    `enable_grouping` > default (False — Step 3 ships off by default).
    """
    if cli_value is not None:
        return cli_value
    env_raw = os.environ.get(GROUPING_ENABLED_ENV_VAR)
    if env_raw is not None:
        return _truthy(env_raw)
    cfg_value = _retrieval_config().enable_grouping
    if cfg_value is not None:
        return cfg_value
    return DEFAULT_ENABLE_GROUPING


# ---------------------------------------------------------------------------
# Top-K resolver — broad vs narrow buckets
# ---------------------------------------------------------------------------

BROAD_QUERY_TOP_K_ENV_VAR: str = "NORA_BROAD_QUERY_TOP_K"
NARROW_QUERY_TOP_K_ENV_VAR: str = "NORA_NARROW_QUERY_TOP_K"
DEFAULT_BROAD_QUERY_TOP_K: int = 25
DEFAULT_NARROW_QUERY_TOP_K: int = 10

# Bucket assignment per QueryType. Broad = "user wants many chunks
# covering the topic"; narrow = "user wants the specific answer". The
# split is intentionally coarse — two knobs (broad / narrow) cover all
# query types without proliferating per-type config. If a corpus needs
# finer per-type control, layer it via top_k_cap (ceiling).
_BROAD_QUERY_TYPES: frozenset[str] = frozenset({
    "cross_doc",
    "cross_mno_comparison",
    "standards_comparison",
    "feature_level",
    "summarize",
    "traceability",
    "release_diff",
})


def is_broad_query_type(query_type: str | None) -> bool:
    """True when the query_type belongs to the broad bucket. Unknown
    types fall back to narrow (more conservative — fewer chunks)."""
    return (query_type or "").lower() in _BROAD_QUERY_TYPES


def _resolve_int_env_or_default(env_var: str, default: int) -> int:
    """Read int from env var; fall back to default on absent / invalid."""
    raw = os.environ.get(env_var, "")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except (ValueError, TypeError):
            pass
    return default


def resolve_top_k(
    query_type: str | None = None,
    cli_value: int | None = None,
) -> int:
    """Resolve the per-query top-k for retrieval.

    Two-bucket design: every QueryType maps to either broad (default 25)
    or narrow (default 10). Resolution priority:

      cli_value > config DB (overlaid into RetrievalConfig) > env var > default

    The DB overlay populates `RetrievalConfig.broad_query_top_k` /
    `narrow_query_top_k` via `ConfigStore.apply_to_caches`. Web app runs
    with --config-db go through DB → env → default. Pipeline runner
    has no DB; its `RetrievalConfig` fields stay None and the chain
    collapses to env → default. First-time DB creation seeds the keys
    from env / defaults at web-app startup so the DB is always the
    source of truth thereafter (see ConfigStore.seed_missing).
    """
    if cli_value is not None and cli_value > 0:
        return int(cli_value)
    cfg = _retrieval_config()
    if is_broad_query_type(query_type):
        if cfg.broad_query_top_k is not None and cfg.broad_query_top_k > 0:
            return int(cfg.broad_query_top_k)
        return _resolve_int_env_or_default(
            BROAD_QUERY_TOP_K_ENV_VAR, DEFAULT_BROAD_QUERY_TOP_K,
        )
    if cfg.narrow_query_top_k is not None and cfg.narrow_query_top_k > 0:
        return int(cfg.narrow_query_top_k)
    return _resolve_int_env_or_default(
        NARROW_QUERY_TOP_K_ENV_VAR, DEFAULT_NARROW_QUERY_TOP_K,
    )


def resolve_bm25_weight(
    query_type: str | None = None,
    cli_value: float | None = None,
) -> float:
    """Resolve the BM25 weight for the RRF fusion at query time.

    Precedence: CLI flag > config/retrieval.json
    `bm25_weight_by_type[query_type]` (DB layer overlays this via
    apply_to_caches) > pipeline `_TYPE_BM25_WEIGHT` built-in default
    > 0.0 (pure dense).

    No env-var tier — per-type maps are file-only by convention
    (see D-050: shell-level overrides for ten query types would be
    untidy; admins use the Config page or edit the JSON file).
    """
    if cli_value is not None:
        return float(cli_value)
    if query_type:
        cfg = _retrieval_config()
        if query_type in cfg.bm25_weight_by_type:
            return cfg.bm25_weight_by_type[query_type]
        # Fall back to the built-in per-type default in pipeline.py.
        from core.src.query.pipeline import _TYPE_BM25_WEIGHT
        from core.src.query.schema import QueryType
        try:
            qt_enum = QueryType(query_type)
        except ValueError:
            return 0.0
        return _TYPE_BM25_WEIGHT.get(qt_enum, 0.0)
    return 0.0


def resolve_gap_threshold(
    cli_value: float | None = None,
    query_type: str | None = None,
) -> float:
    """Resolve the gap threshold for grouping auto-commit vs disambiguation.

    Precedence: CLI flag > NORA_RETRIEVAL_GAP_THRESHOLD env var >
    config/retrieval.json `gap_threshold_by_type[query_type]` (if present)
    > config/retrieval.json `gap_threshold` (scalar default in file) >
    built-in default (0.05).

    `query_type` accepts a `QueryType.value` string (e.g. "single_doc").
    None or unknown type falls through to the scalar default — same
    behavior as having no per-type entry.
    """
    if cli_value is not None:
        return float(cli_value)
    env_raw = os.environ.get(GAP_THRESHOLD_ENV_VAR)
    if env_raw:
        try:
            return float(env_raw)
        except ValueError:
            logger.warning(
                "%s=%r is not a valid float; ignoring",
                GAP_THRESHOLD_ENV_VAR, env_raw,
            )
    cfg = _retrieval_config()
    if query_type and query_type in cfg.gap_threshold_by_type:
        return cfg.gap_threshold_by_type[query_type]
    if cfg.gap_threshold is not None:
        return cfg.gap_threshold
    return DEFAULT_GAP_THRESHOLD


# ---------------------------------------------------------------------------
# Standards source selection — see core/src/standards/spec_downloader.py
# ---------------------------------------------------------------------------

STANDARDS_SOURCES: tuple[str, ...] = ("huggingface", "3gpp")
DEFAULT_STANDARDS_SOURCE: str = "huggingface"
STANDARDS_SOURCE_ENV_VAR: str = "NORA_STANDARDS_SOURCE"


def resolve_standards_source(
    cli_value: str | None = None,
    env_config_value: str | None = None,
) -> str:
    """Resolve the effective standards source.

    Precedence: CLI flag > NORA_STANDARDS_SOURCE env var > EnvironmentConfig
    field > default ("huggingface"). Raises ValueError if any provided value
    is not in STANDARDS_SOURCES.
    """
    for label, value in (
        ("--standards-source", cli_value),
        (STANDARDS_SOURCE_ENV_VAR, os.environ.get(STANDARDS_SOURCE_ENV_VAR)),
        ("EnvironmentConfig.standards_source", env_config_value),
    ):
        if value:
            if value not in STANDARDS_SOURCES:
                raise ValueError(
                    f"{label}={value!r} not in {STANDARDS_SOURCES}"
                )
            return value
    return DEFAULT_STANDARDS_SOURCE


# ---------------------------------------------------------------------------
# LLM provider selection — see core/src/llm/openai_provider.py
# ---------------------------------------------------------------------------

LLM_PROVIDERS: tuple[str, ...] = ("ollama", "openai-compatible", "mock")
DEFAULT_LLM_PROVIDER: str = "ollama"
DEFAULT_LLM_MODEL: str = "auto"
DEFAULT_LLM_TIMEOUT: int = 600
LLM_PROVIDER_ENV_VAR: str = "NORA_LLM_PROVIDER"
LLM_MODEL_ENV_VAR: str = "NORA_LLM_MODEL"
LLM_TIMEOUT_ENV_VAR: str = "NORA_LLM_TIMEOUT"
LLM_BASE_URL_ENV_VAR: str = "NORA_LLM_BASE_URL"
LLM_API_KEY_ENV_VAR: str = "NORA_LLM_API_KEY"


def resolve_llm_provider(
    cli_value: str | None = None,
    config_store_value: str | None = None,
    env_config_value: str | None = None,
) -> str:
    """Resolve the effective LLM provider.

    Precedence: CLI flag > Config-page DB (``llm.llm_provider``) >
    NORA_LLM_PROVIDER env var > config/llm.json ``llm_provider`` >
    EnvironmentConfig field (deprecated, back-compat only) > default
    ("ollama"). DB sits **above** env var (deviation from D-053's
    documented ordering) so that values saved through the Config page
    actually take effect at query time — otherwise a stale shell-set
    env var permanently overrides UI edits. Raises ValueError if any
    provided value is not in LLM_PROVIDERS.
    """
    for label, value in (
        ("--llm-provider", cli_value),
        ("config_db:llm.llm_provider", config_store_value),
        (LLM_PROVIDER_ENV_VAR, os.environ.get(LLM_PROVIDER_ENV_VAR)),
        ("config/llm.json:llm_provider", _llm_config().llm_provider),
        ("EnvironmentConfig.model_provider", env_config_value),
    ):
        if value:
            if value not in LLM_PROVIDERS:
                raise ValueError(
                    f"{label}={value!r} not in {LLM_PROVIDERS}"
                )
            return value
    return DEFAULT_LLM_PROVIDER


def resolve_llm_model(
    cli_value: str | None = None,
    config_store_value: str | None = None,
    env_config_value: str | None = None,
) -> str:
    """Resolve the effective LLM model name.

    Precedence: CLI flag > Config-page DB (``llm.llm_model``) >
    NORA_LLM_MODEL env var > config/llm.json ``llm_model`` >
    EnvironmentConfig field (deprecated, back-compat only) > default
    ("auto"). DB above env var — see ``resolve_llm_provider`` for
    rationale. No enum validation — model names are provider-specific.
    "auto" is meaningful only for the ollama provider; cloud providers
    require an explicit name.
    """
    for value in (
        cli_value,
        config_store_value,
        os.environ.get(LLM_MODEL_ENV_VAR),
        _llm_config().llm_model,
        env_config_value,
    ):
        if value:
            return value
    return DEFAULT_LLM_MODEL


def resolve_llm_timeout(
    cli_value: int | None = None,
    config_store_value: int | None = None,
    env_config_value: int | None = None,
) -> int:
    """Resolve the effective LLM request timeout (seconds).

    Precedence: CLI flag > Config-page DB (``llm.llm_timeout``) >
    NORA_LLM_TIMEOUT env var > config/llm.json ``llm_timeout`` >
    EnvironmentConfig field (deprecated, back-compat only) > default
    (600). DB above env var — see ``resolve_llm_provider`` for
    rationale. Each layer's "0" is treated as unset and falls through.
    """
    if cli_value:
        return cli_value
    if config_store_value:
        try:
            n = int(config_store_value)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    env_raw = os.environ.get(LLM_TIMEOUT_ENV_VAR)
    if env_raw:
        try:
            n = int(env_raw)
            if n > 0:
                return n
        except ValueError:
            pass
    cfg_value = _llm_config().llm_timeout
    if cfg_value:
        return cfg_value
    if env_config_value:
        return env_config_value
    return DEFAULT_LLM_TIMEOUT


def resolve_llm_base_url(
    cli_value: str | None = None,
    config_store_value: str | None = None,
    env_config_value: str | None = None,
) -> str:
    """Resolve the effective LLM base URL (OpenAI-compatible providers).

    Precedence: CLI > Config-page DB (``llm.llm_base_url``) >
    ``NORA_LLM_BASE_URL`` env var > config/llm.json ``llm_base_url`` >
    EnvironmentConfig field > "". The empty default lets the caller
    (typically ``OpenAICompatibleProvider``) decide whether to raise
    or apply its own provider-specific fallback.
    """
    for value in (
        cli_value,
        config_store_value,
        os.environ.get(LLM_BASE_URL_ENV_VAR),
        _llm_config().llm_base_url,
        env_config_value,
    ):
        if value:
            return value.strip() if isinstance(value, str) else value
    return ""


def resolve_llm_api_key(
    cli_value: str | None = None,
    config_store_value: str | None = None,
    env_config_value: str | None = None,
) -> str:
    """Resolve the effective LLM API key (OpenAI-compatible providers).

    Precedence: CLI > Config-page DB (``llm.llm_api_key``) >
    ``NORA_LLM_API_KEY`` env var > config/llm.json ``llm_api_key`` >
    EnvironmentConfig field > "". Empty default = no auth (the
    provider decides whether to raise or send unauthenticated).
    """
    for value in (
        cli_value,
        config_store_value,
        os.environ.get(LLM_API_KEY_ENV_VAR),
        _llm_config().llm_api_key,
        env_config_value,
    ):
        if value:
            return value.strip() if isinstance(value, str) else value
    return ""


# ---------------------------------------------------------------------------
# Embedding provider / model selection — see core/src/vectorstore
# ---------------------------------------------------------------------------

EMBEDDING_PROVIDERS: tuple[str, ...] = ("sentence-transformers", "huggingface", "ollama")
DEFAULT_EMBEDDING_PROVIDER: str = "sentence-transformers"
DEFAULT_EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
EMBEDDING_PROVIDER_ENV_VAR: str = "NORA_EMBEDDING_PROVIDER"
EMBEDDING_MODEL_ENV_VAR: str = "NORA_EMBEDDING_MODEL"


def resolve_embedding_provider(
    cli_value: str | None = None,
    env_config_value: str | None = None,
) -> str:
    """Resolve the effective embedding provider.

    Precedence: CLI flag > NORA_EMBEDDING_PROVIDER env var >
    config/llm.json `embedding_provider` > EnvironmentConfig field
    (deprecated, back-compat) > default ("sentence-transformers").
    The "huggingface" alias is accepted but the canonical name is
    preserved (make_embedder normalizes aliases internally).
    """
    for label, value in (
        ("--embedding-provider", cli_value),
        (EMBEDDING_PROVIDER_ENV_VAR, os.environ.get(EMBEDDING_PROVIDER_ENV_VAR)),
        ("config/llm.json:embedding_provider", _llm_config().embedding_provider),
        ("EnvironmentConfig.embedding_provider", env_config_value),
    ):
        if value:
            if value not in EMBEDDING_PROVIDERS:
                raise ValueError(
                    f"{label}={value!r} not in {EMBEDDING_PROVIDERS}"
                )
            return value
    return DEFAULT_EMBEDDING_PROVIDER


def resolve_embedding_model(
    cli_value: str | None = None,
    env_config_value: str | None = None,
) -> str:
    """Resolve the effective embedding model name.

    Precedence: CLI flag > NORA_EMBEDDING_MODEL env var >
    config/llm.json `embedding_model` > EnvironmentConfig field
    (deprecated, back-compat) > default ("all-MiniLM-L6-v2"). No
    enum validation — model names are provider-specific.
    """
    for value in (
        cli_value,
        os.environ.get(EMBEDDING_MODEL_ENV_VAR),
        _llm_config().embedding_model,
        env_config_value,
    ):
        if value:
            return value
    return DEFAULT_EMBEDDING_MODEL


# ---------------------------------------------------------------------------
# Pipeline mode toggles — three-tier resolution (CLI > env var > config/llm.json)
# ---------------------------------------------------------------------------

SKIP_TAXONOMY_ENV_VAR: str = "NORA_SKIP_TAXONOMY"
SKIP_GRAPH_ENV_VAR: str = "NORA_SKIP_GRAPH"
SKIP_RESOLVE_ENV_VAR: str = "NORA_SKIP_RESOLVE"
SKIP_STANDARDS_ENV_VAR: str = "NORA_SKIP_STANDARDS"
RERANKER_ENABLED_ENV_VAR: str = "NORA_RERANKER_ENABLED"
RERANKER_MODEL_ENV_VAR: str = "NORA_RERANKER_MODEL"
RERANKER_PROVIDER_ENV_VAR: str = "NORA_RERANKER_PROVIDER"
RERANKER_OLLAMA_URL_ENV_VAR: str = "NORA_RERANKER_OLLAMA_URL"
RAG_ONLY_ENV_VAR: str = "NORA_RAG_ONLY"

DEFAULT_RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L6-v2"
"""Sentence-transformers cross-encoder model id (or local filesystem
path). When the work-PC corpus is firewalled from HuggingFace, pre-
download via ``huggingface-cli download <id> --local-dir <path>`` and
set this to the local-dir path. For provider=ollama, set to an
Ollama-pulled reranker model name like
``bbjson/bge-reranker-base:latest`` instead."""

DEFAULT_RERANKER_PROVIDER: str = "huggingface"
"""Which reranker backend to instantiate. "huggingface" uses
sentence_transformers.CrossEncoder (loads from HF cache). "ollama"
uses OllamaReranker (calls a local Ollama server). For deployments
on proxy-restricted machines without HF access but with Ollama
running locally, "ollama" lets you reuse a model already pulled with
`ollama pull <name>` — no separate HF cache to manage."""

DEFAULT_RERANKER_OLLAMA_URL: str = "http://localhost:11434"
"""Base URL for the local Ollama server when provider=ollama. Same
default as OllamaEmbedder."""


def _truthy(value: str | None) -> bool:
    """Treat env-var strings as bool. `1 / true / yes / on` → True."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_skip_taxonomy(
    cli_value: bool | None = None,
    env_config_value: bool | None = None,
) -> bool:
    """Resolve whether to skip the taxonomy stage.

    Precedence: --skip-taxonomy / --rag-only CLI flag > NORA_SKIP_TAXONOMY
    or NORA_RAG_ONLY env var > config/llm.json `skip_taxonomy` > env
    config `skip_taxonomy` (back-compat) > default (False)."""
    if cli_value is not None:
        return cli_value
    if _truthy(os.environ.get(SKIP_TAXONOMY_ENV_VAR)):
        return True
    if _truthy(os.environ.get(RAG_ONLY_ENV_VAR)):
        return True
    cfg = _llm_config()
    if cfg.skip_taxonomy:
        return True
    if env_config_value is not None:
        return env_config_value
    return False


def resolve_skip_graph(
    cli_value: bool | None = None,
    env_config_value: bool | None = None,
) -> bool:
    """Resolve whether to skip the knowledge-graph stage.

    Precedence: --skip-graph / --rag-only CLI flag > NORA_SKIP_GRAPH or
    NORA_RAG_ONLY env var > config/llm.json `skip_graph` > env config
    `skip_graph` (back-compat) > default (False).

    Implies `skip_taxonomy` semantically — taxonomy output is consumed
    only by the graph stage, so skipping graph makes taxonomy moot.
    Callers can still set `skip_taxonomy=False` explicitly if they
    want the LLM-extracted taxonomy artifact for some other purpose."""
    if cli_value is not None:
        return cli_value
    if _truthy(os.environ.get(SKIP_GRAPH_ENV_VAR)):
        return True
    if _truthy(os.environ.get(RAG_ONLY_ENV_VAR)):
        return True
    cfg = _llm_config()
    if cfg.skip_graph:
        return True
    if env_config_value is not None:
        return env_config_value
    return False


def resolve_skip_resolve(
    cli_value: bool | None = None,
    env_config_value: bool | None = None,
) -> bool:
    """Resolve whether to skip the cross-reference resolve stage.

    Precedence: --skip-resolve CLI flag > NORA_SKIP_RESOLVE env var >
    config/llm.json `skip_resolve` > env config `skip_resolve` >
    default (False).

    Implies `skip_standards` semantically — the standards stage reads
    resolve's manifests as input, so skipping resolve forces standards
    off too. Callers should pair the two skips when invoking the
    pipeline; the run_cli applies the cascade automatically."""
    if cli_value is not None:
        return cli_value
    if _truthy(os.environ.get(SKIP_RESOLVE_ENV_VAR)):
        return True
    cfg = _llm_config()
    if cfg.skip_resolve:
        return True
    if env_config_value is not None:
        return env_config_value
    return False


def resolve_skip_standards(
    cli_value: bool | None = None,
    env_config_value: bool | None = None,
) -> bool:
    """Resolve whether to skip the standards (3GPP spec download) stage.

    Precedence: --skip-standards CLI flag > NORA_SKIP_STANDARDS env
    var > config/llm.json `skip_standards` > env config
    `skip_standards` > default (False).

    Skipping standards omits the 3GPP / GSMA spec download + extract
    pass. Graph and vectorstore tolerate missing standards artifacts
    (no spec-section nodes / chunks)."""
    if cli_value is not None:
        return cli_value
    if _truthy(os.environ.get(SKIP_STANDARDS_ENV_VAR)):
        return True
    cfg = _llm_config()
    if cfg.skip_standards:
        return True
    if env_config_value is not None:
        return env_config_value
    return False


def resolve_reranker_enabled(
    config_store_value: bool | None = None,
    env_config_value: bool | None = None,
) -> bool:
    """Resolve whether to attach the cross-encoder reranker at query time.

    Precedence: NORA_RERANKER_ENABLED env var > Config-page DB
    (``llm.reranker_enabled``) > config/llm.json ``reranker_enabled``
    > env config ``reranker_enabled`` > default (False).

    False (default) → ``MockReranker`` passthrough — current production
    behavior. True → ``CrossEncoderReranker(model)`` is constructed and
    plumbed into ``QueryPipeline``; the per-query-type
    ``_TYPE_RERANK_ENABLED`` gate still decides which intents actually
    trigger reranking."""
    raw_env = os.environ.get(RERANKER_ENABLED_ENV_VAR)
    if raw_env is not None and raw_env.strip() != "":
        return _truthy(raw_env)
    if config_store_value is not None:
        return bool(config_store_value)
    cfg = _llm_config()
    if cfg.reranker_enabled:
        return True
    if env_config_value is not None:
        return env_config_value
    return False


def resolve_reranker_model(
    config_store_value: str | None = None,
    env_config_value: str | None = None,
) -> str:
    """Resolve the cross-encoder model id / path.

    Precedence: NORA_RERANKER_MODEL env var > Config-page DB
    (``llm.reranker_model``) > config/llm.json ``reranker_model`` >
    env config ``reranker_model`` > built-in
    ``DEFAULT_RERANKER_MODEL``.

    Accepts either a HuggingFace model id (e.g.
    ``BAAI/bge-reranker-base``) or a local filesystem path (e.g.
    ``~/work/models/bge-reranker-base``). Local paths sidestep the
    online HF download when the host can't reach huggingface.co."""
    raw_env = (os.environ.get(RERANKER_MODEL_ENV_VAR) or "").strip()
    if raw_env:
        return raw_env
    if config_store_value:
        return str(config_store_value).strip()
    cfg = _llm_config()
    if cfg.reranker_model:
        return cfg.reranker_model
    if env_config_value:
        return env_config_value.strip()
    return DEFAULT_RERANKER_MODEL


def resolve_reranker_provider(
    config_store_value: str | None = None,
) -> str:
    """Resolve the reranker backend: "huggingface" or "ollama".

    Precedence: NORA_RERANKER_PROVIDER env var > Config-page DB
    (``llm.reranker_provider``) > config/llm.json ``reranker_provider``
    > built-in ``DEFAULT_RERANKER_PROVIDER`` ("huggingface").

    Unknown values fall back to the default with a warning. Lets
    deployments on proxy-restricted machines without HF access use
    a locally-pulled Ollama model (e.g. ``bbjson/bge-reranker-base``)
    via the OllamaReranker backend instead of sentence_transformers
    + HF cache."""
    raw_env = (os.environ.get(RERANKER_PROVIDER_ENV_VAR) or "").strip().lower()
    if raw_env in {"huggingface", "ollama"}:
        return raw_env
    if config_store_value:
        v = str(config_store_value).strip().lower()
        if v in {"huggingface", "ollama"}:
            return v
    cfg = _llm_config()
    if getattr(cfg, "reranker_provider", "") in {"huggingface", "ollama"}:
        return cfg.reranker_provider
    return DEFAULT_RERANKER_PROVIDER


def resolve_reranker_ollama_url(
    config_store_value: str | None = None,
) -> str:
    """Resolve the Ollama server URL for provider=ollama.

    Precedence: NORA_RERANKER_OLLAMA_URL env var > Config-page DB >
    config/llm.json ``reranker_ollama_url`` > built-in
    ``DEFAULT_RERANKER_OLLAMA_URL``."""
    raw_env = (os.environ.get(RERANKER_OLLAMA_URL_ENV_VAR) or "").strip()
    if raw_env:
        return raw_env
    if config_store_value:
        return str(config_store_value).strip()
    cfg = _llm_config()
    if getattr(cfg, "reranker_ollama_url", ""):
        return cfg.reranker_ollama_url
    return DEFAULT_RERANKER_OLLAMA_URL


# ---------------------------------------------------------------------------
# Pipeline stage registry — single source of truth for names and ordering
# ---------------------------------------------------------------------------

PIPELINE_STAGES: list[tuple[str, str]] = [
    ("extract", "Document content extraction"),
    ("profile", "Document profiling"),
    ("parse", "Structural parsing"),
    ("resolve", "Cross-reference resolution"),
    ("taxonomy", "Feature taxonomy extraction"),
    ("standards", "Standards ingestion"),
    ("graph", "Knowledge graph construction"),
    ("vectorstore", "Vector store construction"),
    ("eval", "Evaluation"),
]

STAGE_NAMES: list[str] = [s[0] for s in PIPELINE_STAGES]
STAGE_NUM: dict[str, int] = {name: i + 1 for i, (name, _) in enumerate(PIPELINE_STAGES)}
NUM_STAGE: dict[int, str] = {i + 1: name for i, (name, _) in enumerate(PIPELINE_STAGES)}
STAGE_DESC: dict[str, str] = {name: desc for name, desc in PIPELINE_STAGES}


def resolve_stage(value: str) -> str:
    """Convert a stage number or name to a canonical stage name."""
    if value.isdigit():
        num = int(value)
        if num not in NUM_STAGE:
            raise ValueError(
                f"Stage number {num} out of range (1-{len(PIPELINE_STAGES)})"
            )
        return NUM_STAGE[num]
    if value in STAGE_NUM:
        return value
    raise ValueError(
        f"Unknown stage '{value}'. Valid: {', '.join(STAGE_NAMES)} or 1-{len(PIPELINE_STAGES)}"
    )


# ---------------------------------------------------------------------------
# Per-environment directory layout (D-022)
# ---------------------------------------------------------------------------

ENV_DIR_DIRS = {
    "input": "Source documents organized as <MNO>/<release>/*",
    "out": "Pipeline outputs (auto-created per stage)",
    "state": "Runtime SQLite databases (job queue, metrics)",
    "corrections": "User-corrected artifacts (profile.json, taxonomy.json)",
    "reports": "Pipeline reports (auto-created)",
    "eval": "User-supplied Q&A eval pairs (Excel)",
}


# ---------------------------------------------------------------------------
# Environment config
# ---------------------------------------------------------------------------

@dataclass
class EnvironmentConfig:
    """Configuration for a pipeline environment."""

    name: str
    description: str
    created_by: str
    member: str
    env_dir: str

    # Stages to run
    stage_start: str = "extract"
    stage_end: str = "eval"

    # Scope
    mnos: list[str] = field(default_factory=lambda: ["VZW"])
    releases: list[str] = field(default_factory=lambda: ["Feb2026"])
    doc_types: list[str] = field(default_factory=lambda: ["requirements"])

    # Objectives (human-readable)
    objectives: list[str] = field(default_factory=list)

    # LLM config
    model_provider: str = "ollama"
    model_name: str = "auto"
    model_timeout: int = 600

    # Embedding config (local providers only)
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER
    embedding_model: str = DEFAULT_EMBEDDING_MODEL

    # Standards source: "huggingface" (default) | "3gpp"
    standards_source: str = DEFAULT_STANDARDS_SOURCE

    # Skip the taxonomy stage entirely. Graph and vectorstore stages
    # tolerate missing taxonomy (no feature: nodes, no maps_to edges).
    # Useful when taxonomy LLM output is noisy or non-deterministic.
    skip_taxonomy: bool = False

    # Skip the knowledge-graph stage entirely. Implies pure-RAG
    # retrieval at query time — pipeline detects missing graph and
    # builds a stub from vectorstore metadata so the QueryPipeline
    # can still construct, with `_bypass_graph=True`. Pairs with
    # `skip_taxonomy` for a fully RAG-only pipeline (set both via
    # `--rag-only` or `NORA_RAG_ONLY=1`).
    skip_graph: bool = False

    # Skip the cross-reference resolve stage. Pipeline still produces
    # parsed trees; downstream graph + vectorstore tolerate missing
    # xref manifests (no resolved_internal / resolved_standards edges).
    # Implies skip_standards (the standards stage reads resolve's
    # manifest dir to build the spec reference index).
    skip_resolve: bool = False

    # Skip the standards (3GPP / GSMA) download + extract stage. No
    # spec-section nodes get added to the graph and no spec chunks
    # land in the vectorstore. Useful when running offline or when
    # standards content isn't needed for the corpus.
    skip_standards: bool = False

    # Cross-encoder reranker (query-time). When `reranker_enabled` is
    # True, `_get_or_build_pipeline` constructs a
    # `CrossEncoderReranker(model)` and plumbs it into `QueryPipeline`;
    # the per-query-type `_TYPE_RERANK_ENABLED` gate still decides
    # which intents actually trigger reranking. `reranker_model`
    # accepts either a HuggingFace id (e.g. `BAAI/bge-reranker-base`)
    # or a local filesystem path — local paths sidestep the online HF
    # fetch in firewalled environments.
    reranker_enabled: bool = False
    reranker_model: str = ""

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))

    # --- Serialization ---

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load_json(cls, path: Path) -> EnvironmentConfig:
        with open(path) as f:
            data = json.load(f)
        return cls(**data)

    # --- Validation ---

    def validate(self) -> list[str]:
        """Return list of validation errors (empty = valid)."""
        errors: list[str] = []
        if self.stage_start not in STAGE_NAMES:
            errors.append(f"Unknown start stage: {self.stage_start}")
        if self.stage_end not in STAGE_NAMES:
            errors.append(f"Unknown end stage: {self.stage_end}")
        if (
            self.stage_start in STAGE_NUM
            and self.stage_end in STAGE_NUM
            and STAGE_NUM[self.stage_start] > STAGE_NUM[self.stage_end]
        ):
            errors.append(
                f"Start stage '{self.stage_start}' ({STAGE_NUM[self.stage_start]}) "
                f"is after end stage '{self.stage_end}' ({STAGE_NUM[self.stage_end]})"
            )
        if not self.name:
            errors.append("Environment name is required")
        if not self.env_dir:
            errors.append("env_dir is required")
        if not self.mnos:
            errors.append("At least one MNO must be specified")
        if not self.releases:
            errors.append("At least one release must be specified")
        if self.standards_source not in STANDARDS_SOURCES:
            errors.append(
                f"Unknown standards_source: {self.standards_source!r} "
                f"(valid: {', '.join(STANDARDS_SOURCES)})"
            )
        if self.model_provider not in LLM_PROVIDERS:
            errors.append(
                f"Unknown model_provider: {self.model_provider!r} "
                f"(valid: {', '.join(LLM_PROVIDERS)})"
            )
        if self.embedding_provider not in EMBEDDING_PROVIDERS:
            errors.append(
                f"Unknown embedding_provider: {self.embedding_provider!r} "
                f"(valid: {', '.join(EMBEDDING_PROVIDERS)})"
            )
        return errors

    # --- Derived paths ---

    @property
    def active_stages(self) -> list[str]:
        """Stage names that will run, in order."""
        start = STAGE_NUM.get(self.stage_start, 1) - 1
        end = STAGE_NUM.get(self.stage_end, len(PIPELINE_STAGES))
        return STAGE_NAMES[start:end]

    @property
    def env_dir_path(self) -> Path:
        # expanduser() handles `~` / `~user` from stored configs or quoted CLI;
        # resolve() then makes it absolute so downstream rglob / mkdir work
        # regardless of cwd.
        return Path(self.env_dir).expanduser().resolve()

    def path(self, key: str) -> Path:
        """Get a standard subdirectory under env_dir (generic accessor)."""
        return self.env_dir_path / key

    def input_path(self, mno: str, release: str) -> Path:
        """Get input directory for a specific MNO and release (D-023)."""
        return self.env_dir_path / "input" / mno / release

    def out_path(self, stage: str) -> Path:
        """Get output directory for a specific pipeline stage."""
        return self.env_dir_path / "out" / stage

    def state_path(self) -> Path:
        """Get the state directory (runtime SQLite DBs)."""
        return self.env_dir_path / "state"

    def corrections_path(self) -> Path:
        """Get the corrections directory."""
        return self.env_dir_path / "corrections"

    def correction_file(self, artifact: str) -> Path | None:
        """Get path to a correction file if it exists, else None."""
        p = self.corrections_path() / artifact
        return p if p.exists() else None

    def reports_path(self) -> Path:
        """Get the reports directory."""
        return self.env_dir_path / "reports"

    def eval_path(self) -> Path:
        """Get the eval directory (user-supplied Q&A pairs)."""
        return self.env_dir_path / "eval"

    def init_directories(self) -> list[str]:
        """Create the standard directory structure. Returns created dirs."""
        created: list[str] = []
        for dirname in ENV_DIR_DIRS:
            p = self.env_dir_path / dirname
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created.append(str(p))
        # Stage-specific output dirs under out/
        for stage in self.active_stages:
            p = self.out_path(stage)
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created.append(str(p))
        return created
