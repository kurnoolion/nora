"""Webhook delivery for ingest state changes (standard library).

The payload is one JSON object per event: `{"event": "ingest.<state>",
"ingest_id", "mno", "release", "state", "stage", "verdict", "label",
"previous_label", "error", "at"}`. Signed with HMAC-SHA256 over the raw
body when a secret is configured (`X-NORA-Signature: sha256=<hex>`).
Three attempts with short waits; delivery never fails the job.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from typing import Callable

Poster = Callable[[str, bytes, dict[str, str], float], int]

_WAITS = (0.0, 2.0, 10.0)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def http_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (allowlisted host)
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        return 0


def deliver(url: str, payload: dict, *, secret: str = "", timeout: float = 5.0,
            post: Poster = http_post, sleep: Callable[[float], None] | None = None) -> tuple[bool, str]:
    """POST the payload; `(delivered, note)`. Retries on a connection
    failure or a 5xx; a 4xx is final."""
    import time
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "nora-ingest-runner",
               "X-NORA-Event": str(payload.get("event", ""))}
    if secret:
        headers["X-NORA-Signature"] = sign(secret, body)
    wait = sleep or time.sleep
    last = 0
    for i, w in enumerate(_WAITS):
        if w:
            wait(w)
        last = post(url, body, headers, timeout)
        if 200 <= last < 300:
            return True, f"delivered ({last}) on attempt {i + 1}"
        if 400 <= last < 500:
            return False, f"refused ({last}) — not retried"
    return False, f"not delivered after {len(_WAITS)} attempts (last status {last})"
