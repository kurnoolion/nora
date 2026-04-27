"""Metrics page and API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# -- Pages ------------------------------------------------------------------

@router.get("/metrics", response_class=HTMLResponse)
async def metrics_page(request: Request):
    from src.web.app import _template_response
    return _template_response(request, "metrics.html")


# -- API: summary data for the page -----------------------------------------

@router.get("/api/metrics/summary")
async def metrics_summary(request: Request):
    metrics_store = getattr(request.app.state, "metrics", None)
    if metrics_store is None:
        return {"error": "Metrics store not initialized"}

    from datetime import UTC, datetime, timedelta
    since_1h = (datetime.now(UTC) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    since_24h = (datetime.now(UTC) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )

    # Request metrics (last hour)
    req_1h = await metrics_store._agg_for("request", "response_time", since_1h)
    req_err_1h = await metrics_store._agg_for("request", "error_count", since_1h)

    # LLM metrics (last hour)
    llm_latency = await metrics_store._agg_for("llm", "latency", since_1h)
    llm_toks = await metrics_store._agg_for("llm", "tokens_per_second", since_1h)
    llm_tokens = await metrics_store._agg_for("llm", "eval_count", since_1h)

    # Pipeline stage summary (last 24h for broader view)
    pip_summary = await metrics_store._pipeline_stage_summary(since_24h)

    # Resource: latest values
    cpu = await metrics_store._latest_value("resource", "cpu_percent")
    ram_used = await metrics_store._latest_value("resource", "ram_used_gb")
    ram_pct = await metrics_store._latest_value("resource", "ram_percent")
    disk_used = await metrics_store._latest_value("resource", "disk_used_gb")
    gpu_pct = await metrics_store._latest_value("resource", "gpu_percent")
    gpu_mem = await metrics_store._latest_value("resource", "gpu_mem_used_gb")

    # Get tags for totals
    ram_total = None
    gpu_mem_total = None
    recent_ram = await metrics_store.query(category="resource", name="ram_used_gb", limit=1)
    if recent_ram:
        ram_total = recent_ram[0].tags.get("total_gb")
    recent_gpu = await metrics_store.query(category="resource", name="gpu_mem_used_gb", limit=1)
    if recent_gpu:
        gpu_mem_total = recent_gpu[0].tags.get("total_gb")

    return {
        "request": {
            "avg_ms": round(req_1h["avg"], 1),
            "p95_ms": round(req_1h["p95"], 1),
            "count": req_1h["count"],
            "error_count": int(req_err_1h["sum"]) if req_err_1h["count"] > 0 else 0,
            "error_rate": round(
                (req_err_1h["sum"] / req_1h["count"] * 100) if req_1h["count"] > 0 else 0, 1
            ),
        },
        "llm": {
            "avg_latency_s": round(llm_latency["avg"], 1),
            "p95_latency_s": round(llm_latency["p95"], 1),
            "avg_tok_s": round(llm_toks["avg"], 1) if llm_toks["count"] > 0 else None,
            "total_tokens": int(llm_tokens["sum"]) if llm_tokens["count"] > 0 else 0,
            "call_count": llm_latency["count"],
        },
        "pipeline": pip_summary,
        "resource": {
            "cpu_percent": round(cpu, 1) if cpu is not None else None,
            "ram_used_gb": round(ram_used, 1) if ram_used is not None else None,
            "ram_total_gb": ram_total,
            "ram_percent": round(ram_pct, 1) if ram_pct is not None else None,
            "disk_used_gb": round(disk_used, 1) if disk_used is not None else None,
            "gpu_percent": round(gpu_pct, 1) if gpu_pct is not None else None,
            "gpu_mem_used_gb": round(gpu_mem, 1) if gpu_mem is not None else None,
            "gpu_mem_total_gb": gpu_mem_total,
        },
    }


@router.get("/api/metrics/compact", response_class=PlainTextResponse)
async def metrics_compact(request: Request):
    metrics_store = getattr(request.app.state, "metrics", None)
    if metrics_store is None:
        return PlainTextResponse("MET error: store not initialized")
    report = await metrics_store.compact_report()
    return PlainTextResponse(report)


@router.get("/api/metrics/resource", response_class=HTMLResponse)
async def metrics_resource_partial(request: Request):
    """HTMX partial: refreshes the resource gauges."""
    from src.web.app import _template_response

    metrics_store = getattr(request.app.state, "metrics", None)
    data = {}
    if metrics_store:
        cpu = await metrics_store._latest_value("resource", "cpu_percent")
        ram_used = await metrics_store._latest_value("resource", "ram_used_gb")
        ram_pct = await metrics_store._latest_value("resource", "ram_percent")
        gpu_pct = await metrics_store._latest_value("resource", "gpu_percent")
        gpu_mem = await metrics_store._latest_value("resource", "gpu_mem_used_gb")

        ram_total = None
        gpu_mem_total = None
        recent_ram = await metrics_store.query(category="resource", name="ram_used_gb", limit=1)
        if recent_ram:
            ram_total = recent_ram[0].tags.get("total_gb")
        recent_gpu = await metrics_store.query(category="resource", name="gpu_mem_used_gb", limit=1)
        if recent_gpu:
            gpu_mem_total = recent_gpu[0].tags.get("total_gb")

        data = {
            "cpu_percent": round(cpu, 1) if cpu is not None else None,
            "ram_used_gb": round(ram_used, 1) if ram_used is not None else None,
            "ram_total_gb": ram_total,
            "ram_percent": round(ram_pct, 1) if ram_pct is not None else None,
            "gpu_percent": round(gpu_pct, 1) if gpu_pct is not None else None,
            "gpu_mem_used_gb": round(gpu_mem, 1) if gpu_mem is not None else None,
            "gpu_mem_total_gb": gpu_mem_total,
        }

    return _template_response(request, "partials/metrics_resource.html", {
        "resource": data,
    })
