"""Team-eval access gating (team-eval-pilot).

`NORA_WEB_TEAM_MODE=1` restricts the web app to the allowlisted surfaces
(`_TEAM_ALLOWED` below) for team members and locks the test page to the SIRA
lane only. The admin unlocks
full access for their own browser by visiting
`/admin-unlock?token=<NORA_WEB_ADMIN_TOKEN>`, which sets an HttpOnly cookie that
is checked on every request thereafter.

When `NORA_WEB_TEAM_MODE` is off (the default) the gate is a no-op — full access,
both lanes — so nothing changes for non-team deployments.

Security note: the cookie carries the admin token (HttpOnly, not JS-readable).
For an internal eval tool with a single trusted admin this is acceptable; it is
NOT a substitute for real auth on an untrusted network.
"""

from __future__ import annotations

import os

from starlette.requests import Request

ENV_TEAM_MODE = "NORA_WEB_TEAM_MODE"
ENV_ADMIN_TOKEN = "NORA_WEB_ADMIN_TOKEN"
ADMIN_COOKIE = "nora_admin"
# Self-serve ingestion (strand self-serve-ingestion): its own token, given
# to the requirements team, separate from the admin token. Set as a cookie
# by `/ingest-unlock?token=…`; checked by every `/ingest*` route whether
# team mode is on or off. Unset = the surface is closed.
ENV_INGEST_TOKEN = "NORA_WEB_INGEST_TOKEN"
INGEST_COOKIE = "nora_ingest"

TEAM_MODE = os.getenv(ENV_TEAM_MODE, "").strip().lower() in ("1", "true", "yes", "on")
ADMIN_TOKEN = os.getenv(ENV_ADMIN_TOKEN, "")
INGEST_TOKEN = os.getenv(ENV_INGEST_TOKEN, "")

# Path prefixes a gated team member may reach. Everything else → /test.
_TEAM_ALLOWED = ("/test", "/api/test", "/static", "/api/health", "/admin-unlock", "/favicon",
                 "/enrichment-review", "/api/enrich-review",
                 "/eval-studio", "/api/eval-studio",
                 # Shared-answer + history surfaces (strand ask-page-ux /
                 # ask-history): a shared link reproduces the NORMAL user
                 # view only (no engineering internals persisted — D-209),
                 # so gated team members may open teammates' links; history
                 # is browser-local. Merged without these — a gated expert
                 # opening a shared link was redirected, defeating the
                 # feature in exactly the deployments that gate.
                 "/ask/s/", "/api/ask/s/", "/ask/history",
                 # Requirement fragments behind the answer bubbles (strand
                 # req-id-bubbles). Parse-layer read only, and it backs the
                 # shared/history surfaces above — without it a gated expert
                 # opening a teammate's link gets bubbles that 302 to /test.
                 "/api/req/",
                 # Self-serve ingestion: the route checks the ingest cookie
                 # itself (is_ingest_user); the allowlist only lets a gated
                 # member holding it reach the page.
                 "/ingest", "/api/ingest")


def is_admin(request: Request) -> bool:
    """True when the request may see the full app: gate off (everyone), or a
    valid admin cookie is present."""
    if not TEAM_MODE:
        return True
    return bool(ADMIN_TOKEN) and request.cookies.get(ADMIN_COOKIE, "") == ADMIN_TOKEN


def is_ingest_user(request: Request) -> bool:
    """True when the request holds the ingest cookie (the requirements
    team's token) or a valid admin cookie. Independent of team mode: the
    ingest surface is token-gated in every deployment."""
    if INGEST_TOKEN and request.cookies.get(INGEST_COOKIE, "") == INGEST_TOKEN:
        return True
    return bool(ADMIN_TOKEN) and request.cookies.get(ADMIN_COOKIE, "") == ADMIN_TOKEN


def team_restricted(request: Request) -> bool:
    """True when this request is a gated team member (no admin cookie)."""
    return TEAM_MODE and not is_admin(request)


def path_allowed_for_team(path: str) -> bool:
    """Whitelist check for the gated surface. `/` is NOT allowed — gated members
    are redirected to `/test`."""
    return any(path.startswith(p) for p in _TEAM_ALLOWED)
