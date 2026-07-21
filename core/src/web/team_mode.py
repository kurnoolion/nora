"""Team-eval access gating (team-eval-pilot).

`NORA_WEB_TEAM_MODE=1` restricts the web app to the `/test` eval surface for
team members and locks the test page to the SIRA lane only. The admin unlocks
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

TEAM_MODE = os.getenv(ENV_TEAM_MODE, "").strip().lower() in ("1", "true", "yes", "on")
ADMIN_TOKEN = os.getenv(ENV_ADMIN_TOKEN, "")

# Path prefixes a gated team member may reach. Everything else → /test.
_TEAM_ALLOWED = ("/test", "/api/test", "/static", "/api/health", "/admin-unlock", "/favicon",
                 "/enrichment-review", "/api/enrich-review")


def is_admin(request: Request) -> bool:
    """True when the request may see the full app: gate off (everyone), or a
    valid admin cookie is present."""
    if not TEAM_MODE:
        return True
    return bool(ADMIN_TOKEN) and request.cookies.get(ADMIN_COOKIE, "") == ADMIN_TOKEN


def team_restricted(request: Request) -> bool:
    """True when this request is a gated team member (no admin cookie)."""
    return TEAM_MODE and not is_admin(request)


def path_allowed_for_team(path: str) -> bool:
    """Whitelist check for the gated surface. `/` is NOT allowed — gated members
    are redirected to `/test`."""
    return any(path.startswith(p) for p in _TEAM_ALLOWED)
