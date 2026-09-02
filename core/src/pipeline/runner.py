"""Pipeline orchestrator.

Runs pipeline stages in sequence, manages context between stages,
and collects results for reporting.

Usage:
    from core.src.pipeline.runner import PipelineContext, PipelineRunner

    ctx = PipelineContext.from_env(env_config)
    runner = PipelineRunner(ctx)
    results = runner.run(["extract", "profile", "parse"])
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.src.env.config import STAGE_NAMES
from core.src.pipeline.cells import Cell, enumerate_input_cells, is_per_cell_stage
from core.src.pipeline.stages import STAGE_FUNCS, StageResult

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """Shared context passed through all pipeline stages."""

    documents_dir: Path
    corrections_dir: Path | None
    eval_dir: Path | None
    verbose: bool

    # Stage output directories (pre-resolved)
    stage_dirs: dict[str, Path] = field(default_factory=dict)

    # LLM config
    model_provider: str = "ollama"
    model_name: str = "auto"
    model_timeout: int = 600
    llm_base_url: str = ""
    llm_api_key: str = ""
    # Reasoning effort for providers that support it ("none"/"low"/"medium"/
    # "high"). Empty = send nothing, i.e. the endpoint's own default.
    llm_reasoning: str = ""

    # Embedding config (local providers only)
    embedding_provider: str = "sentence-transformers"
    embedding_model: str = "all-MiniLM-L6-v2"

    # Scope
    mnos: list[str] = field(default_factory=lambda: ["VZW"])
    releases: list[str] = field(default_factory=lambda: ["Feb2026"])

    # Per-cell ingestion scope (D-DRAFT-8). Empty = all cells. Restrict which
    # `(mno, release)` cells the PER-CELL stages process; `force` ignores the
    # skip-if-unchanged check. Global stages always read the whole union.
    scope_mnos: list[str] = field(default_factory=list)
    scope_releases: list[str] = field(default_factory=list)
    force: bool = False

    # Standards ingestion source: "huggingface" | "3gpp"
    standards_source: str = "huggingface"

    # Accumulated state between stages (paths, intermediate data)
    state: dict = field(default_factory=dict)

    def stage_output(self, stage: str, cell: "Cell | None" = None) -> Path:
        """Get the output directory for a stage.

        With `cell` and a **per-cell** stage (D-DRAFT-6: extract / profile /
        parse / resolve / vectorstore), returns the cell subdirectory
        `out/<stage>/<mno>/<rel>/`. For a **global** stage, or when no `cell`
        is given, returns the flat `out/<stage>/` — so existing callers that
        pass no cell are unchanged.
        """
        base = self.stage_dirs[stage]
        if cell is not None and is_per_cell_stage(stage):
            return base / cell.relpath
        return base

    def cell_in_scope(self, mno: str, release: str) -> bool:
        """True iff `(mno, release)` passes the `--mno`/`--release` scope (D-DRAFT-8).

        Empty scope = all cells. MNO match is case-insensitive; release exact.
        """
        if self.scope_mnos and mno.upper() not in {m.upper() for m in self.scope_mnos}:
            return False
        if self.scope_releases and release not in self.scope_releases:
            return False
        return True

    def input_cells(self) -> "list[Cell]":
        """The in-scope `(MNO, release)` cells present under `input/`, validated + sorted."""
        return [
            c for c in enumerate_input_cells(self.documents_dir)
            if self.cell_in_scope(c.mno, c.release)
        ]

    def correction(self, filename: str) -> Path | None:
        """Get a correction file path if it exists."""
        if not self.corrections_dir:
            return None
        p = self.corrections_dir / filename
        return p if p.exists() else None

    def create_llm_provider(self, require_real: bool = False):
        """Create an LLM provider based on config, wrapped with the
        permanent-refusal fallback when one is configured.

        Every LLM-using pipeline stage (taxonomy, eval) and the debug /
        miner CLIs construct their provider here, so this is the single
        choke point that gives them refusal coverage — same env-var
        family as the web and golden-eval lanes
        (``NORA_LLM_REFUSAL_MARKERS`` + ``NORA_LLM_FALLBACK_*``).
        Unconfigured or mock providers are returned unwrapped.
        """
        from core.src.llm.refusal import maybe_wrap_with_refusal_fallback

        return maybe_wrap_with_refusal_fallback(
            self._construct_llm_provider(require_real=require_real),
            timeout=self.model_timeout,
        )

    def _construct_llm_provider(self, require_real: bool = False):
        """Provider construction proper (no refusal wrap).

        Dispatch on `model_provider`. Each branch falls back to MockLLMProvider
        on failure unless `require_real=True`.
        """
        # Explicit mock request — fast path before any model_name resolution.
        if self.model_provider == "mock" or self.model_name == "mock":
            logger.info("Using MockLLMProvider (explicit)")
            from core.src.llm.mock_provider import MockLLMProvider
            mock = MockLLMProvider()
            mock._is_mock = True
            return mock

        if self.model_provider == "ollama":
            resolved_model = self._resolve_model()
            try:
                from core.src.llm.ollama_provider import OllamaProvider
                provider = OllamaProvider(
                    model=resolved_model,
                    timeout=self.model_timeout,
                )
                logger.info(f"Using Ollama LLM: {resolved_model}")
                return provider
            except (ConnectionError, Exception) as e:
                if require_real:
                    raise
                logger.warning(f"Ollama unavailable ({e}), falling back to mock")

        elif self.model_provider == "openai-compatible":
            # Hardware-detection auto-pick is Ollama-only; cloud providers
            # require an explicit model tag.
            if self.model_name == "auto":
                msg = (
                    "model_provider=openai-compatible requires an explicit "
                    "model name (set NORA_LLM_MODEL or pass --model)."
                )
                if require_real:
                    raise ValueError(msg)
                logger.warning(f"{msg} Falling back to mock.")
            else:
                try:
                    from core.src.llm.openai_provider import OpenAICompatibleProvider
                    # Pass explicit base_url / api_key when resolved upstream
                    # (CLI > Config DB > env var > config/llm.json). Empty
                    # strings let the provider fall back to its own
                    # NORA_LLM_{BASE_URL,API_KEY} env-var defaults — preserves
                    # the legacy path for callers that never touched the new
                    # fields.
                    provider = OpenAICompatibleProvider(
                        model=self.model_name,
                        timeout=self.model_timeout,
                        base_url=self.llm_base_url or None,
                        api_key=self.llm_api_key or None,
                        reasoning=self.llm_reasoning or None,
                    )
                    logger.info(f"Using OpenAI-compatible LLM: {self.model_name}")
                    return provider
                except (ValueError, ConnectionError, RuntimeError, Exception) as e:
                    if require_real:
                        raise
                    logger.warning(
                        f"OpenAI-compatible provider unavailable ({e}), falling back to mock"
                    )

        # Fallthrough: every real-provider branch above returned its
        # own provider or logged a warning before falling here. Emit a
        # last-resort WARN so a silent mock substitution can never
        # mask a misconfigured model_provider — taxonomy + synthesis
        # would otherwise quietly return canned MockLLMProvider output
        # that looks superficially fine.
        logger.warning(
            "LLM provider could not be constructed (model_provider=%r, "
            "model_name=%r) — falling back to MockLLMProvider. Taxonomy "
            "and query synthesis will use canned responses.",
            self.model_provider, self.model_name,
        )
        from core.src.llm.mock_provider import MockLLMProvider
        mock = MockLLMProvider()
        mock._is_mock = True
        return mock

    def _resolve_model(self) -> str:
        """Resolve 'auto' model name using hardware detection."""
        if self.model_name != "auto":
            return self.model_name
        try:
            from core.src.llm.model_picker import detect_hardware, pick_model
            hw = detect_hardware()
            choice = pick_model(hw)
            logger.info(f"Auto-selected model: {choice.model} — {choice.reason}")
            return choice.model
        except Exception as e:
            logger.warning(f"Model auto-detection failed ({e}), defaulting to gemma4:e4b")
            return "gemma4:e4b"

    # --- Factory methods ---

    @classmethod
    def from_env(cls, env) -> PipelineContext:
        """Create context from an EnvironmentConfig."""
        stage_dirs = {stage: env.out_path(stage) for stage in STAGE_NAMES}
        return cls(
            documents_dir=env.input_root,
            corrections_dir=env.corrections_path(),
            eval_dir=env.eval_path(),
            verbose=False,
            stage_dirs=stage_dirs,
            model_provider=env.model_provider,
            model_name=env.model_name,
            model_timeout=env.model_timeout,
            embedding_provider=env.embedding_provider,
            embedding_model=env.embedding_model,
            mnos=env.mnos,
            releases=env.releases,
            standards_source=env.standards_source,
        )

    @classmethod
    def standalone(
        cls,
        env_dir: Path,
        profile_path: Path | None = None,
        model_provider: str = "ollama",
        model_name: str = "auto",
        model_timeout: int = 600,
        embedding_provider: str = "sentence-transformers",
        embedding_model: str = "all-MiniLM-L6-v2",
        standards_source: str = "huggingface",
    ) -> PipelineContext:
        """Create context for standalone (no EnvironmentConfig) mode.

        Derives the standard env_dir layout (D-022) from the supplied path:
        documents under <env_dir>/input/, outputs under <env_dir>/out/<stage>/,
        corrections under <env_dir>/corrections/, eval under <env_dir>/eval/.
        """
        # expanduser() handles a quoted `~/...` from CLI; resolve() absolutizes.
        env_dir = Path(env_dir).expanduser().resolve()
        stage_dirs = {stage: env_dir / "out" / stage for stage in STAGE_NAMES}
        ctx = cls(
            documents_dir=env_dir / "input",
            corrections_dir=env_dir / "corrections",
            eval_dir=env_dir / "eval",
            verbose=False,
            stage_dirs=stage_dirs,
            model_provider=model_provider,
            model_name=model_name,
            model_timeout=model_timeout,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            standards_source=standards_source,
        )
        if profile_path:
            ctx.state["profile_path"] = str(profile_path)
        return ctx


class PipelineRunner:
    """Orchestrates pipeline stage execution."""

    def __init__(self, ctx: PipelineContext):
        self.ctx = ctx
        self.results: list[StageResult] = []

    def run(self, stages: list[str], continue_on_error: bool = False) -> list[StageResult]:
        """Run the specified stages in order.

        Args:
            stages: List of stage names to run.
            continue_on_error: If True, continue to next stage on failure.

        Returns:
            List of StageResult objects.
        """
        self.results = []
        total_t0 = time.time()

        logger.info(f"Pipeline: running {len(stages)} stages: {', '.join(stages)}")

        for i, stage_name in enumerate(stages, 1):
            func = STAGE_FUNCS.get(stage_name)
            if not func:
                self.results.append(StageResult(
                    stage=stage_name, status="FAIL", elapsed_seconds=0,
                    error_code="PIP-E001", error_message=f"Unknown stage: {stage_name}",
                ))
                if not continue_on_error:
                    break
                continue

            logger.info(f"[{i}/{len(stages)}] {stage_name} ...")

            try:
                result = func(self.ctx)
            except Exception as e:
                result = StageResult(
                    stage=stage_name, status="FAIL", elapsed_seconds=0,
                    error_code="PIP-E001", error_message=f"Unhandled error: {e}",
                )

            self.results.append(result)

            # Log result
            status_icon = {"OK": "+", "WARN": "!", "FAIL": "X", "SKIP": "-"}.get(result.status, "?")
            logger.info(
                f"  [{status_icon}] {stage_name}: {result.status} "
                f"({result.elapsed_seconds:.1f}s) {result.stats}"
            )
            for w in result.warnings:
                logger.warning(f"    {w}")
            if result.error_message:
                logger.error(f"    {result.error_message}")

            if not result.ok and not continue_on_error:
                logger.error(f"Pipeline stopped at stage '{stage_name}' due to failure.")
                break

        total_elapsed = time.time() - total_t0
        passed = sum(1 for r in self.results if r.ok)
        logger.info(
            f"Pipeline complete: {passed}/{len(self.results)} stages OK "
            f"in {total_elapsed:.1f}s"
        )

        return self.results
