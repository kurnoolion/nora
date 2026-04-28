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

    # Model config
    model_provider: str = "ollama"
    model_name: str = "auto"
    model_timeout: int = 600

    # Scope
    mnos: list[str] = field(default_factory=lambda: ["VZW"])
    releases: list[str] = field(default_factory=lambda: ["Feb2026"])

    # Standards ingestion source: "huggingface" | "3gpp"
    standards_source: str = "huggingface"

    # Accumulated state between stages (paths, intermediate data)
    state: dict = field(default_factory=dict)

    def stage_output(self, stage: str) -> Path:
        """Get the output directory for a stage."""
        return self.stage_dirs[stage]

    def correction(self, filename: str) -> Path | None:
        """Get a correction file path if it exists."""
        if not self.corrections_dir:
            return None
        p = self.corrections_dir / filename
        return p if p.exists() else None

    def create_llm_provider(self, require_real: bool = False):
        """Create an LLM provider based on config.

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
                    provider = OpenAICompatibleProvider(
                        model=self.model_name,
                        timeout=self.model_timeout,
                    )
                    logger.info(f"Using OpenAI-compatible LLM: {self.model_name}")
                    return provider
                except (ValueError, ConnectionError, RuntimeError, Exception) as e:
                    if require_real:
                        raise
                    logger.warning(
                        f"OpenAI-compatible provider unavailable ({e}), falling back to mock"
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
            documents_dir=env.env_dir_path / "input",
            corrections_dir=env.corrections_path(),
            eval_dir=env.eval_path(),
            verbose=False,
            stage_dirs=stage_dirs,
            model_provider=env.model_provider,
            model_name=env.model_name,
            model_timeout=env.model_timeout,
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
