"""Pipeline orchestrator.

Runs pipeline stages in sequence, manages context between stages,
and collects results for reporting.

Usage:
    from src.pipeline.runner import PipelineContext, PipelineRunner

    ctx = PipelineContext.from_env(env_config)
    runner = PipelineRunner(ctx)
    results = runner.run(["extract", "profile", "parse"])
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.env.config import STAGE_NAMES
from src.pipeline.stages import STAGE_FUNCS, StageResult

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

        Returns MockLLMProvider if model is "mock" or Ollama is unavailable.
        """
        resolved_model = self._resolve_model()

        # Explicit mock request
        if resolved_model == "mock" or self.model_provider == "mock":
            logger.info("Using MockLLMProvider (explicit)")
            from src.llm.mock_provider import MockLLMProvider
            mock = MockLLMProvider()
            mock._is_mock = True
            return mock

        if self.model_provider == "ollama":
            try:
                from src.llm.ollama_provider import OllamaProvider
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

        from src.llm.mock_provider import MockLLMProvider
        mock = MockLLMProvider()
        mock._is_mock = True
        return mock

    def _resolve_model(self) -> str:
        """Resolve 'auto' model name using hardware detection."""
        if self.model_name != "auto":
            return self.model_name
        try:
            from src.llm.model_picker import detect_hardware, pick_model
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
        stage_dirs = {stage: env.output_path(stage) for stage in STAGE_NAMES}
        return cls(
            documents_dir=env.doc_root / "documents",
            corrections_dir=env.doc_root / "corrections",
            eval_dir=env.doc_root / "eval",
            verbose=False,
            stage_dirs=stage_dirs,
            model_provider=env.model_provider,
            model_name=env.model_name,
            model_timeout=env.model_timeout,
            mnos=env.mnos,
            releases=env.releases,
        )

    @classmethod
    def standalone(
        cls,
        documents_dir: Path,
        output_base: Path = Path("data"),
        profile_path: Path | None = None,
        model_name: str = "auto",
        model_timeout: int = 600,
    ) -> PipelineContext:
        """Create context for standalone (non-environment) mode."""
        stage_dirs = {
            "extract": output_base / "extracted",
            "profile": Path("profiles"),
            "parse": output_base / "parsed",
            "resolve": output_base / "resolved",
            "taxonomy": output_base / "taxonomy",
            "standards": output_base / "standards",
            "graph": output_base / "graph",
            "vectorstore": output_base / "vectorstore",
            "eval": output_base / "eval",
        }
        ctx = cls(
            documents_dir=documents_dir,
            corrections_dir=None,
            eval_dir=None,
            verbose=False,
            stage_dirs=stage_dirs,
            model_name=model_name,
            model_timeout=model_timeout,
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
