"""Pipeline page and API routes."""

from __future__ import annotations

import asyncio
import logging
import traceback
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.src.env.config import (
    EnvironmentConfig,
    PIPELINE_STAGES,
    STAGE_NAMES,
    STAGE_NUM,
)
from core.src.pipeline.runner import PipelineContext, PipelineRunner
from core.src.web.jobs import JobQueue
from core.src.web.path_mapper import PathMapper

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
ENVIRONMENTS_DIR = PROJECT_ROOT / "environments"

router = APIRouter()


def _list_environments() -> list[dict]:
    """Scan environments/*.json and return summary dicts."""
    envs = []
    if not ENVIRONMENTS_DIR.is_dir():
        return envs
    for p in sorted(ENVIRONMENTS_DIR.glob("*.json")):
        try:
            cfg = EnvironmentConfig.load_json(p)
            envs.append({
                "name": cfg.name,
                "description": cfg.description,
                "member": cfg.member,
                "document_root": cfg.document_root,
                "stage_start": cfg.stage_start,
                "stage_end": cfg.stage_end,
                "file": p.name,
            })
        except Exception as exc:
            logger.warning("Failed to load environment %s: %s", p.name, exc)
    return envs


def _stages_for_template() -> list[dict]:
    """Build stage list for dropdown rendering."""
    return [
        {"num": i + 1, "name": name, "desc": desc}
        for i, (name, desc) in enumerate(PIPELINE_STAGES)
    ]


# -- Pages ------------------------------------------------------------------

@router.get("/pipeline", response_class=HTMLResponse)
async def pipeline_page(request: Request):
    from core.src.web.app import _template_response
    return _template_response(request, "pipeline.html", {
        "environments": _list_environments(),
        "stages": _stages_for_template(),
    })


# -- API --------------------------------------------------------------------

@router.post("/api/pipeline/submit")
async def submit_pipeline(request: Request):
    from core.src.web.app import _template_response, config

    job_queue: JobQueue = request.app.state.job_queue
    path_mapper: PathMapper = request.app.state.path_mapper

    form = await request.form()
    mode = form.get("mode", "standalone")
    submitted_by = form.get("submitted_by", "").strip() or "anonymous"

    env_config: EnvironmentConfig | None = None
    stages: list[str] = []
    document_dir: Path | None = None

    if mode == "environment":
        env_name = form.get("environment", "").strip()
        if not env_name:
            return _template_response(request, "pipeline.html", {
                "environments": _list_environments(),
                "stages": _stages_for_template(),
                "error": "Please select an environment.",
            })
        env_path = ENVIRONMENTS_DIR / f"{env_name}.json"
        if not env_path.exists():
            return _template_response(request, "pipeline.html", {
                "environments": _list_environments(),
                "stages": _stages_for_template(),
                "error": f"Environment '{env_name}' not found.",
            })
        env_config = EnvironmentConfig.load_json(env_path)
        errors = env_config.validate()
        if errors:
            return _template_response(request, "pipeline.html", {
                "environments": _list_environments(),
                "stages": _stages_for_template(),
                "error": "Environment validation failed: " + "; ".join(errors),
            })
        stages = env_config.active_stages
        label = env_name

    else:
        doc_dir_raw = form.get("document_dir", "").strip()
        stage_start = form.get("stage_start", "extract").strip()
        stage_end = form.get("stage_end", "eval").strip()

        if not doc_dir_raw:
            return _template_response(request, "pipeline.html", {
                "environments": _list_environments(),
                "stages": _stages_for_template(),
                "error": "Document directory is required.",
            })

        try:
            document_dir = path_mapper.resolve(doc_dir_raw)
        except ValueError as exc:
            return _template_response(request, "pipeline.html", {
                "environments": _list_environments(),
                "stages": _stages_for_template(),
                "error": str(exc),
            })

        if stage_start not in STAGE_NAMES:
            return _template_response(request, "pipeline.html", {
                "environments": _list_environments(),
                "stages": _stages_for_template(),
                "error": f"Unknown start stage: {stage_start}",
            })
        if stage_end not in STAGE_NAMES:
            return _template_response(request, "pipeline.html", {
                "environments": _list_environments(),
                "stages": _stages_for_template(),
                "error": f"Unknown end stage: {stage_end}",
            })
        if STAGE_NUM[stage_start] > STAGE_NUM[stage_end]:
            return _template_response(request, "pipeline.html", {
                "environments": _list_environments(),
                "stages": _stages_for_template(),
                "error": f"Start stage ({stage_start}) must come before end stage ({stage_end}).",
            })

        start_idx = STAGE_NUM[stage_start] - 1
        end_idx = STAGE_NUM[stage_end]
        stages = STAGE_NAMES[start_idx:end_idx]
        label = f"standalone:{doc_dir_raw}"

    job = await job_queue.submit(
        job_type="pipeline",
        submitted_by=submitted_by,
        environment=label,
        stages=stages,
    )

    asyncio.create_task(
        run_pipeline_background(job.id, stages, job_queue, env_config, document_dir, request.app)
    )

    return RedirectResponse(
        url=f"{config.root_path}/jobs/{job.id}",
        status_code=303,
    )


# -- Background execution ---------------------------------------------------

async def run_pipeline_background(
    job_id: str,
    stages: list[str],
    job_queue: JobQueue,
    env_config: EnvironmentConfig | None,
    document_dir: Path | None,
    request_app=None,
) -> None:
    """Execute pipeline stages in a background task."""
    try:
        await job_queue.update_status(job_id, "running")
        await job_queue.append_log(job_id, f"Starting pipeline: {', '.join(stages)}")

        if env_config is not None:
            env_config.init_directories()
            ctx = PipelineContext.from_env(env_config)
            await job_queue.append_log(job_id, f"Environment: {env_config.name}")
        else:
            ctx = PipelineContext.standalone(documents_dir=document_dir)
            await job_queue.append_log(job_id, f"Standalone: {document_dir}")

        runner = PipelineRunner(ctx)
        total = len(stages)

        for i, stage_name in enumerate(stages):
            progress = int((i / total) * 100)
            await job_queue.update_status(
                job_id, "running",
                current_stage=stage_name,
                progress=progress,
            )
            await job_queue.append_log(job_id, f"[{i+1}/{total}] Running stage: {stage_name}")

            result = await asyncio.to_thread(runner.run, [stage_name])
            stage_result = result[0] if result else None

            if stage_result is None:
                await job_queue.append_log(job_id, f"  Stage {stage_name}: no result returned")
                continue

            status_icon = {"OK": "+", "WARN": "!", "FAIL": "X", "SKIP": "-"}.get(
                stage_result.status, "?"
            )
            await job_queue.append_log(
                job_id,
                f"  [{status_icon}] {stage_name}: {stage_result.status} "
                f"({stage_result.elapsed_seconds:.1f}s) {stage_result.stats}",
            )
            for w in stage_result.warnings:
                await job_queue.append_log(job_id, f"    WARN: {w}")
            if stage_result.error_message:
                await job_queue.append_log(job_id, f"    ERROR: {stage_result.error_message}")

            # Record pipeline stage metrics
            await _record_stage_metrics(request_app, stage_result)

            if not stage_result.ok:
                await job_queue.update_status(
                    job_id, "failed",
                    current_stage=stage_name,
                    progress=progress,
                    error_message=f"Stage {stage_name} failed: {stage_result.error_message}",
                )
                await job_queue.append_log(job_id, f"Pipeline stopped at stage '{stage_name}'.")
                return

        await job_queue.update_status(
            job_id, "completed",
            progress=100,
            result_summary=f"All {total} stages completed successfully.",
        )
        await job_queue.append_log(job_id, f"Pipeline complete: {total}/{total} stages OK")

    except Exception as exc:
        logger.exception("Pipeline background task failed for job %s", job_id)
        try:
            await job_queue.update_status(
                job_id, "failed",
                error_message=f"Unexpected error: {exc}",
            )
            await job_queue.append_log(job_id, f"FATAL: {traceback.format_exc()}")
        except Exception:
            logger.exception("Failed to record error for job %s", job_id)


async def _record_stage_metrics(app, stage_result) -> None:
    """Record pipeline stage metrics to MetricsStore (fire-and-forget safe)."""
    try:
        metrics_store = getattr(app.state, "metrics", None) if app else None
        if metrics_store is None:
            return

        from core.src.web.metrics import MetricRecord, _now_iso
        ts = _now_iso()
        records = []

        # Stage duration
        records.append(MetricRecord(
            timestamp=ts,
            category="pipeline",
            name="stage_duration",
            value=stage_result.elapsed_seconds,
            unit="seconds",
            tags={
                "stage": stage_result.stage,
                "status": stage_result.status,
                "error_code": stage_result.error_code or "",
            },
        ))

        # Stage-specific stats as individual metrics
        for stat_key, stat_val in stage_result.stats.items():
            if isinstance(stat_val, (int, float)):
                records.append(MetricRecord(
                    timestamp=ts,
                    category="pipeline",
                    name=f"stage_stat_{stat_key}",
                    value=float(stat_val),
                    unit="count",
                    tags={"stage": stage_result.stage},
                ))

        # Extract LLM timing from stats if this was an LLM-using stage
        # (taxonomy, eval stages use LLM; timing comes via log parsing or
        # future direct instrumentation)

        await metrics_store.record_batch(records)
    except Exception as exc:
        logger.debug("Failed to record stage metrics: %s", exc)
