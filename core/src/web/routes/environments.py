"""Environments page and API routes."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.src.env.config import (
    EnvironmentConfig,
    PIPELINE_STAGES,
    STAGE_NAMES,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
ENVIRONMENTS_DIR = PROJECT_ROOT / "environments"

router = APIRouter()


def _list_environments() -> list[dict]:
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
                "mnos": cfg.mnos,
                "releases": cfg.releases,
                "created_at": cfg.created_at,
                "file": p.name,
            })
        except Exception as exc:
            logger.warning("Failed to load environment %s: %s", p.name, exc)
    return envs


def _stages_for_template() -> list[dict]:
    return [
        {"num": i + 1, "name": name, "desc": desc}
        for i, (name, desc) in enumerate(PIPELINE_STAGES)
    ]


@router.get("/environments", response_class=HTMLResponse)
async def environments_list(request: Request):
    from core.src.web.app import _template_response

    return _template_response(request, "environments.html", {
        "environments": _list_environments(),
    })


@router.get("/environments/new", response_class=HTMLResponse)
async def environments_new(request: Request):
    from core.src.web.app import _template_response

    return _template_response(request, "environment_new.html", {
        "stages": _stages_for_template(),
    })


@router.post("/api/environments/create")
async def create_environment(request: Request):
    from core.src.web.app import _template_response, config

    form = await request.form()

    name = form.get("name", "").strip()
    description = form.get("description", "").strip()
    member = form.get("member", "").strip()
    document_root = form.get("document_root", "").strip()
    stage_start = form.get("stage_start", "extract").strip()
    stage_end = form.get("stage_end", "eval").strip()
    releases = form.get("releases", "").strip()
    objectives_raw = form.get("objectives", "").strip()
    model_provider = form.get("model_provider", "ollama").strip()
    model_name = form.get("model_name", "auto").strip()

    mnos = []
    for mno in ("VZW", "ATT", "TMO"):
        if form.get(f"mno_{mno}"):
            mnos.append(mno)

    if not mnos:
        mnos = ["VZW"]

    releases_list = [r.strip() for r in releases.split(",") if r.strip()] if releases else ["Feb2026"]

    objectives_list = [
        line.strip() for line in objectives_raw.splitlines() if line.strip()
    ]

    if not name:
        return _template_response(request, "environment_new.html", {
            "stages": _stages_for_template(),
            "error": "Environment name is required.",
        })

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)

    env_config = EnvironmentConfig(
        name=name,
        description=description or f"Environment: {name}",
        created_by=member or "anonymous",
        member=member or "anonymous",
        document_root=document_root,
        stage_start=stage_start,
        stage_end=stage_end,
        mnos=mnos,
        releases=releases_list,
        objectives=objectives_list,
        model_provider=model_provider,
        model_name=model_name or "auto",
    )

    errors = env_config.validate()
    if errors:
        return _template_response(request, "environment_new.html", {
            "stages": _stages_for_template(),
            "error": "Validation failed: " + "; ".join(errors),
        })

    ENVIRONMENTS_DIR.mkdir(parents=True, exist_ok=True)
    env_path = ENVIRONMENTS_DIR / f"{safe_name}.json"
    env_config.save_json(env_path)

    return RedirectResponse(
        url=f"{config.root_path}/environments",
        status_code=303,
    )


@router.delete("/api/environments/{name}")
async def delete_environment(request: Request, name: str):
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    env_path = ENVIRONMENTS_DIR / f"{safe_name}.json"

    if not env_path.exists():
        return JSONResponse({"error": "Environment not found."}, status_code=404)

    env_path.unlink()
    return JSONResponse({"ok": True})
