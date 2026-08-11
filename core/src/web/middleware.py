"""Request timing middleware for NORA Web UI.

Records every HTTP request's endpoint, method, status code, and response
time to the MetricsStore. Uses fire-and-forget recording so that metric
failures never block or crash the request handler.
"""

from __future__ import annotations

import asyncio
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

logger = logging.getLogger(__name__)


def _route_path(request: Request) -> str:
    """Request path with the reverse-proxy prefix (scope root_path) removed.

    Starlette expects the ASGI path to INCLUDE root_path — the proxy passes
    the prefix through and routing strips it. Middleware runs on the raw
    path, so any path comparison here must strip the prefix itself or a
    prefixed deployment mis-classifies every request (the team gate would
    redirect `<prefix>/test` to itself forever)."""
    path = request.url.path
    root = request.scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root):] or "/"
    return path


class TeamModeMiddleware(BaseHTTPMiddleware):
    """When NORA_WEB_TEAM_MODE is on, redirect gated team members (no admin
    cookie) away from any non-`/test` path to `/test`. No-op when the gate is
    off or the request is the admin. See `core.src.web.team_mode`."""

    async def dispatch(self, request: Request, call_next) -> Response:
        from core.src.web import team_mode as tm
        if tm.TEAM_MODE and tm.team_restricted(request) and not tm.path_allowed_for_team(
            _route_path(request)
        ):
            # Reverse-proxy safe: scope["root_path"] carries the proxy
            # prefix (FastAPI root_path) — a bare "/test" would bounce
            # gated users to the proxy root, off the app entirely.
            root = request.scope.get("root_path", "")
            return RedirectResponse(f"{root}/test", status_code=302)
        return await call_next(request)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Fire-and-forget metric recording
        try:
            metrics_store = getattr(request.app.state, "metrics", None)
            if metrics_store is not None:
                asyncio.create_task(
                    _record_request_metric(
                        metrics_store,
                        method=request.method,
                        path=_route_path(request),
                        status_code=response.status_code,
                        elapsed_ms=elapsed_ms,
                    )
                )
        except Exception:
            pass

        return response


async def _record_request_metric(
    metrics_store,
    method: str,
    path: str,
    status_code: int,
    elapsed_ms: float,
) -> None:
    try:
        await metrics_store.record(
            category="request",
            name="response_time",
            value=elapsed_ms,
            unit="ms",
            tags={
                "method": method,
                "endpoint": path,
                "status": status_code,
            },
        )
        if status_code >= 400:
            await metrics_store.record(
                category="request",
                name="error_count",
                value=1,
                unit="count",
                tags={
                    "method": method,
                    "endpoint": path,
                    "status": status_code,
                },
            )
    except Exception as exc:
        logger.debug("Failed to record request metric: %s", exc)
