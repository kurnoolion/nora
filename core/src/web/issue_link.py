"""Prefilled "Report issue" link for an answer card (opt-in).

The link opens the configured repo's new-issue page in the user's browser,
filled in from the answer; the user reviews, edits and submits it under their
own login. Nothing is filed from the server and no token is held.

    NORA_ISSUE_URL               the new-issue page, e.g.
                                 https://<git-host>/<org>/<repo>/issues/new.
                                 Unset: no button. Differs per deployment
                                 (internal vs public host).
    NORA_ISSUE_INCLUDE_CONTEXT   on: also prefill the question, the searched
                                 cells and the answer model. Off (default):
                                 metadata and the share link only, so nothing
                                 from the corpus reaches a public repo.

Both are read per request.
"""
from __future__ import annotations

import os
from typing import Iterable
from urllib.parse import urlencode

ENV_URL = "NORA_ISSUE_URL"
ENV_CONTEXT = "NORA_ISSUE_INCLUDE_CONTEXT"


def issue_url(*, row_id: "int | None", lane: str, share_url: str, intent: str = "",
              question: str = "", cells: Iterable[str] = (), model: str = "") -> str:
    """The prefilled new-issue URL for one stored answer, or "" when the
    switch is unset, the URL is not http(s), or the answer was not stored."""
    base = os.getenv(ENV_URL, "").strip()
    if not base or not row_id or not base.lower().startswith(("https://", "http://")):
        return ""
    lines = ["## What is wrong?", "", "<!-- What did you expect, and what did you get? -->", "",
             "## Answer", "", f"- Link: {share_url}", f"- Row: {row_id}", f"- Lane: {lane}"]
    if intent:
        lines.append(f"- Intent: {intent}")
    if os.getenv(ENV_CONTEXT, "").strip().lower() in ("on", "true", "1"):
        if model:
            lines.append(f"- Answer model: {model}")
        cells = list(cells)
        if cells:
            lines.append(f"- Cells: {', '.join(cells)}")
        if question:
            lines += ["", "## Question", "", question]
    query = urlencode({"title": f"Answer issue: row {row_id}", "body": "\n".join(lines),
                       "labels": "bug"})
    return base + ("&" if "?" in base else "?") + query
