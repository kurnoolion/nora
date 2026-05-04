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
LLM_PROVIDER_ENV_VAR: str = "NORA_LLM_PROVIDER"


def resolve_llm_provider(
    cli_value: str | None = None,
    env_config_value: str | None = None,
) -> str:
    """Resolve the effective LLM provider.

    Precedence: CLI flag > NORA_LLM_PROVIDER env var > config/llm.json
    `llm_provider` > EnvironmentConfig field (deprecated, back-compat
    only) > default ("ollama"). Raises ValueError if any provided value
    is not in LLM_PROVIDERS.
    """
    for label, value in (
        ("--llm-provider", cli_value),
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
RAG_ONLY_ENV_VAR: str = "NORA_RAG_ONLY"


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
