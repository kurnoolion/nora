"""Corrections routes — profile + taxonomy editors and compact FIX reports."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse

from src.corrections import (
    CorrectionStore,
    profile_fix_report,
    taxonomy_fix_report,
)
from src.env.config import EnvironmentConfig
from src.profiler.profile_schema import DocumentProfile
from src.taxonomy.schema import FeatureTaxonomy, TaxonomyFeature

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENVIRONMENTS_DIR = PROJECT_ROOT / "environments"

router = APIRouter()


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def _load_env(name: str) -> EnvironmentConfig | None:
    p = ENVIRONMENTS_DIR / f"{_safe_name(name)}.json"
    if not p.exists():
        return None
    try:
        return EnvironmentConfig.load_json(p)
    except Exception as exc:
        logger.warning("Failed to load env %s: %s", name, exc)
        return None


def _list_envs_with_status() -> list[dict]:
    rows = []
    if not ENVIRONMENTS_DIR.is_dir():
        return rows
    for p in sorted(ENVIRONMENTS_DIR.glob("*.json")):
        try:
            env = EnvironmentConfig.load_json(p)
        except Exception:
            continue
        store = CorrectionStore(env)
        rows.append({
            "name": env.name,
            "member": env.member,
            "document_root": env.document_root,
            "profile": store.profile_status(),
            "taxonomy": store.taxonomy_status(),
        })
    return rows


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------

@router.get("/corrections", response_class=HTMLResponse)
async def corrections_index(request: Request):
    from src.web.app import _template_response

    return _template_response(request, "corrections/index.html", {
        "envs": _list_envs_with_status(),
    })


# ---------------------------------------------------------------------------
# Profile editor
# ---------------------------------------------------------------------------

@router.get("/corrections/profile/{env_name}", response_class=HTMLResponse)
async def profile_editor(request: Request, env_name: str):
    from src.web.app import _template_response

    env = _load_env(env_name)
    if not env:
        return _template_response(request, "corrections/index.html", {
            "envs": _list_envs_with_status(),
            "error": f"Environment not found: {env_name}",
        })
    store = CorrectionStore(env)
    status = store.profile_status()
    effective = store.load_profile_effective()
    profile_dict = effective.to_dict() if effective else None

    return _template_response(request, "corrections/profile.html", {
        "env": env,
        "status": status,
        "profile": profile_dict,
        "has_profile": profile_dict is not None,
    })


@router.post("/corrections/profile/{env_name}/start")
async def profile_start(request: Request, env_name: str):
    from src.web.app import config
    env = _load_env(env_name)
    if not env:
        return JSONResponse({"error": "env not found"}, status_code=404)
    store = CorrectionStore(env)
    try:
        path = store.start_profile_correction()
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return RedirectResponse(
        url=f"{config.root_path}/corrections/profile/{env_name}",
        status_code=303,
    )


@router.post("/corrections/profile/{env_name}/discard")
async def profile_discard(request: Request, env_name: str):
    from src.web.app import config
    env = _load_env(env_name)
    if not env:
        return JSONResponse({"error": "env not found"}, status_code=404)
    CorrectionStore(env).discard_profile_correction()
    return RedirectResponse(
        url=f"{config.root_path}/corrections/profile/{env_name}",
        status_code=303,
    )


@router.post("/corrections/profile/{env_name}/save")
async def profile_save(request: Request, env_name: str):
    from src.web.app import config, _template_response
    env = _load_env(env_name)
    if not env:
        return JSONResponse({"error": "env not found"}, status_code=404)
    store = CorrectionStore(env)

    form = await request.form()
    raw = store.read_profile_correction_raw() or (
        store.load_profile_output().to_dict() if store.load_profile_output() else None
    )
    if raw is None:
        return _template_response(request, "corrections/profile.html", {
            "env": env,
            "status": store.profile_status(),
            "profile": None,
            "has_profile": False,
            "error": "No profile to save — run the profile stage first.",
        })

    # Scalar fields
    raw.setdefault("heading_detection", {})["numbering_pattern"] = form.get(
        "numbering_pattern", ""
    ).strip()

    raw.setdefault("requirement_id", {})["pattern"] = form.get("req_pattern", "").strip()

    # Components stored as simple key=value lines
    comp_text = form.get("req_components", "").strip()
    components: dict = {}
    for line in comp_text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if not k:
                continue
            # try to coerce int
            if v.isdigit():
                components[k] = int(v)
            else:
                components[k] = v
    if components:
        raw["requirement_id"]["components"] = components
    elif "components" in raw.get("requirement_id", {}):
        raw["requirement_id"]["components"] = {}

    # Header/footer
    hf = raw.setdefault("header_footer", {})
    hf["header_patterns"] = [
        s.strip() for s in form.get("header_patterns", "").splitlines() if s.strip()
    ]
    hf["footer_patterns"] = [
        s.strip() for s in form.get("footer_patterns", "").splitlines() if s.strip()
    ]
    hf["page_number_pattern"] = form.get("page_number_pattern", "").strip()

    # Cross-ref patterns
    xr = raw.setdefault("cross_reference_patterns", {})
    xr["standards_citations"] = [
        s.strip() for s in form.get("std_citations", "").splitlines() if s.strip()
    ]
    xr["internal_section_refs"] = form.get("internal_section_refs", "").strip()
    xr["requirement_id_refs"] = form.get("req_id_refs", "").strip()

    # Body text
    bt = raw.setdefault("body_text", {})
    try:
        bt["font_size_min"] = float(form.get("body_font_min", "0") or 0)
    except ValueError:
        bt["font_size_min"] = 0.0
    try:
        bt["font_size_max"] = float(form.get("body_font_max", "0") or 0)
    except ValueError:
        bt["font_size_max"] = 0.0
    bt["font_families"] = [
        s.strip() for s in form.get("body_font_families", "").split(",") if s.strip()
    ]

    # Zones — JSON array from hidden field (client maintains)
    zones_json = form.get("zones_json", "").strip()
    if zones_json:
        try:
            zones_list = json.loads(zones_json)
            if isinstance(zones_list, list):
                raw["document_zones"] = [
                    {
                        "section_pattern": z.get("section_pattern", "").strip(),
                        "zone_type": z.get("zone_type", "").strip(),
                        "description": z.get("description", "").strip(),
                        "heading_text": z.get("heading_text", "").strip(),
                    }
                    for z in zones_list
                    if z.get("zone_type", "").strip()
                ]
        except json.JSONDecodeError:
            pass

    store.write_profile_correction_raw(raw)
    return RedirectResponse(
        url=f"{config.root_path}/corrections/profile/{env_name}",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Taxonomy editor
# ---------------------------------------------------------------------------

@router.get("/corrections/taxonomy/{env_name}", response_class=HTMLResponse)
async def taxonomy_editor(request: Request, env_name: str):
    from src.web.app import _template_response
    env = _load_env(env_name)
    if not env:
        return _template_response(request, "corrections/index.html", {
            "envs": _list_envs_with_status(),
            "error": f"Environment not found: {env_name}",
        })
    store = CorrectionStore(env)
    status = store.taxonomy_status()
    effective = store.load_taxonomy_effective()
    tax_dict = effective.to_dict() if effective else None

    return _template_response(request, "corrections/taxonomy.html", {
        "env": env,
        "status": status,
        "taxonomy": tax_dict,
        "has_taxonomy": tax_dict is not None,
    })


@router.post("/corrections/taxonomy/{env_name}/start")
async def taxonomy_start(request: Request, env_name: str):
    from src.web.app import config
    env = _load_env(env_name)
    if not env:
        return JSONResponse({"error": "env not found"}, status_code=404)
    try:
        CorrectionStore(env).start_taxonomy_correction()
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return RedirectResponse(
        url=f"{config.root_path}/corrections/taxonomy/{env_name}",
        status_code=303,
    )


@router.post("/corrections/taxonomy/{env_name}/discard")
async def taxonomy_discard(request: Request, env_name: str):
    from src.web.app import config
    env = _load_env(env_name)
    if not env:
        return JSONResponse({"error": "env not found"}, status_code=404)
    CorrectionStore(env).discard_taxonomy_correction()
    return RedirectResponse(
        url=f"{config.root_path}/corrections/taxonomy/{env_name}",
        status_code=303,
    )


@router.post("/corrections/taxonomy/{env_name}/save")
async def taxonomy_save(request: Request, env_name: str):
    from src.web.app import config
    env = _load_env(env_name)
    if not env:
        return JSONResponse({"error": "env not found"}, status_code=404)
    store = CorrectionStore(env)

    form = await request.form()
    features_json = form.get("features_json", "").strip()
    if not features_json:
        return JSONResponse({"error": "no features payload"}, status_code=400)
    try:
        features = json.loads(features_json)
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"bad features JSON: {e}"}, status_code=400)

    base = store.read_taxonomy_correction_raw()
    if base is None:
        tax = store.load_taxonomy_output()
        base = tax.to_dict() if tax else {"features": [], "source_documents": []}

    new_features = []
    for f in features:
        if not isinstance(f, dict):
            continue
        fid = (f.get("feature_id") or "").strip()
        name = (f.get("name") or "").strip()
        if not fid and not name:
            continue
        if not fid:
            fid = re.sub(r"[^A-Z0-9_]", "_", name.upper())
        keywords = [
            k.strip() for k in (f.get("keywords") or []) if isinstance(k, str) and k.strip()
        ]
        new_features.append({
            "feature_id": fid,
            "name": name or fid,
            "description": (f.get("description") or "").strip(),
            "keywords": keywords,
            "mno_coverage": f.get("mno_coverage") or {},
            "source_plans": f.get("source_plans") or [],
            "depends_on_features": f.get("depends_on_features") or [],
            "is_primary_in": f.get("is_primary_in") or [],
            "is_referenced_in": f.get("is_referenced_in") or [],
        })

    base["features"] = new_features
    store.write_taxonomy_correction_raw(base)
    return RedirectResponse(
        url=f"{config.root_path}/corrections/taxonomy/{env_name}",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Report page
# ---------------------------------------------------------------------------

@router.get("/corrections/report/{env_name}", response_class=HTMLResponse)
async def report_page(request: Request, env_name: str):
    from src.web.app import _template_response
    env = _load_env(env_name)
    if not env:
        return _template_response(request, "corrections/index.html", {
            "envs": _list_envs_with_status(),
            "error": f"Environment not found: {env_name}",
        })
    store = CorrectionStore(env)

    profile_report = None
    if store.profile_correction_path().exists():
        profile_report = profile_fix_report(
            store.load_profile_output(),
            store.load_profile_correction(),
            env.name,
        ).to_text()

    taxonomy_report = None
    if store.taxonomy_correction_path().exists():
        taxonomy_report = taxonomy_fix_report(
            store.load_taxonomy_output(),
            store.load_taxonomy_correction(),
            env.name,
        ).to_text()

    return _template_response(request, "corrections/report.html", {
        "env": env,
        "profile_report": profile_report,
        "taxonomy_report": taxonomy_report,
    })


@router.get("/api/corrections/report/{env_name}", response_class=PlainTextResponse)
async def report_text(request: Request, env_name: str, artifact: str = "both"):
    env = _load_env(env_name)
    if not env:
        return PlainTextResponse(f"env not found: {env_name}", status_code=404)
    store = CorrectionStore(env)
    chunks = []
    if artifact in ("both", "profile") and store.profile_correction_path().exists():
        chunks.append(
            profile_fix_report(
                store.load_profile_output(),
                store.load_profile_correction(),
                env.name,
            ).to_text()
        )
    if artifact in ("both", "taxonomy") and store.taxonomy_correction_path().exists():
        chunks.append(
            taxonomy_fix_report(
                store.load_taxonomy_output(),
                store.load_taxonomy_correction(),
                env.name,
            ).to_text()
        )
    return PlainTextResponse("\n\n".join(chunks) if chunks else "(no corrections yet)")
