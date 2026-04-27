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
from starlette.responses import Response

logger = logging.getLogger(__name__)


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
                        path=request.url.path,
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
