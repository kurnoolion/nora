"""File browser page and routes."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.web.path_mapper import PathMapper

logger = logging.getLogger(__name__)

router = APIRouter()


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _build_breadcrumbs(linux_path: str, root_linux: str, root_label: str) -> list[dict]:
    crumbs = [{"name": root_label, "path": root_linux}]
    relative = os.path.relpath(linux_path, root_linux)
    if relative == ".":
        return crumbs
    parts = relative.split(os.sep)
    current = root_linux
    for part in parts:
        current = os.path.join(current, part)
        crumbs.append({"name": part, "path": current})
    return crumbs


def _find_root_label(path_mapper: PathMapper, linux_path: str) -> tuple[str, str]:
    for root in path_mapper.list_roots():
        root_linux = root["linux"].rstrip("/")
        resolved = Path(linux_path).resolve()
        root_resolved = Path(root_linux).resolve()
        try:
            resolved.relative_to(root_resolved)
            return root_linux, root["label"]
        except ValueError:
            continue
    return linux_path, "Root"


@router.get("/files", response_class=HTMLResponse)
async def files_page(request: Request):
    from src.web.app import _template_response

    path_mapper: PathMapper = request.app.state.path_mapper
    roots = path_mapper.list_roots()

    return _template_response(request, "files.html", {
        "roots": roots,
        "browsing": False,
    })


@router.get("/files/browse", response_class=HTMLResponse)
async def browse(request: Request, path: str = ""):
    from src.web.app import _template_response

    path_mapper: PathMapper = request.app.state.path_mapper

    if not path:
        return _template_response(request, "files.html", {
            "roots": path_mapper.list_roots(),
            "browsing": False,
        })

    resolved = Path(path).resolve()

    if not path_mapper.is_within_roots(resolved):
        return _template_response(request, "files.html", {
            "roots": path_mapper.list_roots(),
            "browsing": False,
            "error": "Access denied: path is outside configured roots.",
        })

    if not resolved.exists():
        return _template_response(request, "files.html", {
            "roots": path_mapper.list_roots(),
            "browsing": False,
            "error": f"Path not found: {path}",
        })

    if not resolved.is_dir():
        return _template_response(request, "files.html", {
            "roots": path_mapper.list_roots(),
            "browsing": False,
            "error": "Path is not a directory.",
        })

    linux_path = str(resolved)
    windows_path = path_mapper.to_windows(linux_path) or "-"

    root_linux, root_label = _find_root_label(path_mapper, linux_path)
    breadcrumbs = _build_breadcrumbs(linux_path, root_linux, root_label)

    entries = []
    try:
        for entry in sorted(resolved.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                stat = entry.stat()
                is_dir = entry.is_dir()
                entries.append({
                    "name": entry.name,
                    "is_dir": is_dir,
                    "size": _human_size(stat.st_size) if not is_dir else "-",
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M"),
                    "path": str(entry),
                    "windows_path": path_mapper.to_windows(str(entry)) or "-",
                })
            except (PermissionError, OSError):
                continue
    except PermissionError:
        return _template_response(request, "files.html", {
            "roots": path_mapper.list_roots(),
            "browsing": False,
            "error": "Permission denied reading directory.",
        })

    return _template_response(request, "files.html", {
        "roots": path_mapper.list_roots(),
        "browsing": True,
        "current_path": linux_path,
        "windows_path": windows_path,
        "breadcrumbs": breadcrumbs,
        "entries": entries,
    })


@router.get("/api/files/listing", response_class=HTMLResponse)
async def file_listing_partial(request: Request, path: str = ""):
    from src.web.app import _template_response

    path_mapper: PathMapper = request.app.state.path_mapper

    if not path:
        return HTMLResponse("<p class='text-muted'>No path specified.</p>")

    resolved = Path(path).resolve()

    if not path_mapper.is_within_roots(resolved):
        return HTMLResponse("<div class='alert alert-danger'>Access denied: path is outside configured roots.</div>")

    if not resolved.exists():
        return HTMLResponse(f"<div class='alert alert-warning'>Path not found: {path}</div>")

    if not resolved.is_dir():
        return HTMLResponse("<div class='alert alert-warning'>Path is not a directory.</div>")

    linux_path = str(resolved)
    windows_path = path_mapper.to_windows(linux_path) or "-"

    root_linux, root_label = _find_root_label(path_mapper, linux_path)
    breadcrumbs = _build_breadcrumbs(linux_path, root_linux, root_label)

    entries = []
    try:
        for entry in sorted(resolved.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                stat = entry.stat()
                is_dir = entry.is_dir()
                entries.append({
                    "name": entry.name,
                    "is_dir": is_dir,
                    "size": _human_size(stat.st_size) if not is_dir else "-",
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M"),
                    "path": str(entry),
                    "windows_path": path_mapper.to_windows(str(entry)) or "-",
                })
            except (PermissionError, OSError):
                continue
    except PermissionError:
        return HTMLResponse("<div class='alert alert-danger'>Permission denied.</div>")

    return _template_response(request, "partials/file_listing.html", {
        "current_path": linux_path,
        "windows_path": windows_path,
        "breadcrumbs": breadcrumbs,
        "entries": entries,
    })
