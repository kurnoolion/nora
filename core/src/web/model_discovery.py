"""Discovered model lists for roster providers (strand llm-model-discovery).

The Ask page offers every model a provider's `GET /v1/models` lists. Lists
are cached in-process per provider id for `model_discovery_ttl_s`:

  * fresh           -> served from cache;
  * stale / absent  -> refetched; a failed refetch keeps the last good list
                       (logged) so a blip does not shrink the dropdown;
  * always          -> the entry's configured `model` is present, because it
                       is the default and the only model known without a fetch.

A failed attempt still stamps the time, so a down endpoint is retried once
per TTL rather than on every page load. The answer path uses
`allowed_models()`, which never fetches.
"""

from __future__ import annotations

import logging
import threading
import time

from core.src.env.config import resolve_model_discovery_ttl_s
from core.src.llm.openai_provider import list_models

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_S = 10

# provider id -> (checked_at, models from the last SUCCESSFUL fetch or None)
_cache: dict[str, tuple[float, list[str] | None]] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _with_default(entry, models: list[str] | None) -> list[str]:
    models = list(models or [])
    return models if entry.model in models else [entry.model, *models]


def _lock_for(provider_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(provider_id, threading.Lock())


def models_for(entry) -> tuple[list[str], bool]:
    """`(models, discovered)` for one roster entry, fetching when stale."""
    with _lock_for(entry.id):
        checked_at, models = _cache.get(entry.id, (None, None))
        stale = checked_at is None or _now() - checked_at >= resolve_model_discovery_ttl_s()
        if stale:
            try:
                models = list_models(entry.base_url, entry.api_key,
                                     timeout=DISCOVERY_TIMEOUT_S)
            except RuntimeError as e:
                logger.warning("Model discovery failed for provider %r: %s — %s",
                               entry.id, e,
                               "keeping last good list" if models else "default model only")
            _cache[entry.id] = (_now(), models)
        return _with_default(entry, models), models is not None


def allowed_models(entry) -> list[str]:
    """Models the answer path may use for `entry`. Cache only — never fetches."""
    _, models = _cache.get(entry.id, (None, None))
    return _with_default(entry, models)


def _reset_cache() -> None:
    """Test hook."""
    _cache.clear()
