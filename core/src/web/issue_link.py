"""Prefilled "Report issue" links (opt-in): one per stored answer, and a
general one in the top bar of every page.

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


def _base() -> str:
    base = os.getenv(ENV_URL, "").strip()
    return base if base.lower().startswith(("https://", "http://")) else ""


def _with_query(base: str, title: str, body: str) -> str:
    query = urlencode({"title": title, "body": body, "labels": "bug"})
    return base + ("&" if "?" in base else "?") + query


def page_issue_url(path: str) -> str:
    """The top bar's general new-issue URL for the page at `path`, or "" when
    the switch is unset. Takes the path only: a query string can carry the
    question text, which must not reach a public repo."""
    base = _base()
    if not base:
        return ""
    body = "\n".join(["## What is wrong?", "", "<!-- What did you expect, and what did you get? -->",
                      "", "## Page", "", f"- Path: {path}"])
    return _with_query(base, f"Issue: {path}", body)


def issue_url(*, row_id: "int | None", lane: str, share_url: str, intent: str = "",
              question: str = "", cells: Iterable[str] = (), model: str = "") -> str:
    """The prefilled new-issue URL for one stored answer, or "" when the
    switch is unset, the URL is not http(s), or the answer was not stored."""
    base = _base()
    if not base or not row_id:
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
    return _with_query(base, f"Answer issue: row {row_id}", "\n".join(lines))
