"""Enable HuggingFace Hub offline mode when the model is already cached.

Works around a bug in ``huggingface_hub._http_backoff_base``: on
``httpx.ConnectError`` (common on restricted networks), the function closes
the shared ``_GLOBAL_CLIENT`` and then retries using the same local
reference, which now raises ``RuntimeError: Cannot send a request, as the
client has been closed.`` That error is not in the retry set and propagates
up as PIP-E001 in the pipeline.

Setting ``HF_HUB_OFFLINE=1`` when the model is on disk skips the revision
HEAD call entirely, avoiding the bug path.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _hf_cache_root() -> Path:
    """Resolve the HuggingFace hub cache directory."""
    cache = os.environ.get("HF_HUB_CACHE")
    if cache:
        return Path(cache)
    hf_home = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    return Path(hf_home) / "hub"


def _cache_has_snapshot(repo_id: str) -> bool:
    """Return True if the HF hub cache has a usable snapshot for repo_id."""
    cache_dir = _hf_cache_root() / f"models--{repo_id.replace('/', '--')}"
    snapshots = cache_dir / "snapshots"
    if not snapshots.is_dir():
        return False
    for snap in snapshots.iterdir():
        if snap.is_dir() and (snap / "config.json").exists():
            return True
    return False


def enable_offline_if_cached(model_name: str) -> bool:
    """If the model is already cached locally, enable HF Hub offline mode.

    Returns True if offline mode is now active (either pre-set or enabled here).
    """
    if os.environ.get("HF_HUB_OFFLINE") or os.environ.get("TRANSFORMERS_OFFLINE"):
        _patch_constants_if_loaded()
        return True

    if Path(model_name).is_dir():
        return False

    candidates = [model_name]
    if "/" not in model_name:
        candidates.append(f"sentence-transformers/{model_name}")

    for repo_id in candidates:
        if _cache_has_snapshot(repo_id):
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            _patch_constants_if_loaded()
            logger.info(
                "HF model '%s' found in cache — HF_HUB_OFFLINE=1 enabled", repo_id
            )
            return True

    return False


def _patch_constants_if_loaded() -> None:
    # huggingface_hub reads HF_HUB_OFFLINE at import time and caches it in
    # constants.HF_HUB_OFFLINE. If it's already imported, a late env-var
    # change is ignored — patch the module attribute instead.
    hf_constants = sys.modules.get("huggingface_hub.constants")
    if hf_constants is not None:
        hf_constants.HF_HUB_OFFLINE = True
