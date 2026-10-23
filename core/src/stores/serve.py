"""Standalone store host (strand corp-db-layer S6; store-split-design §5):
serve any subset of the Trees, History and Overlay stores as their own
service, from the same env vars nora-store reads.

    python -m core.src.stores.serve --stores trees,history,overlay --port PORT

Splitting a store out of nora-store is then configuration only: run this
host with that store, set `NORA_<STORE>_URL` on every consumer (nora-web,
and nora-store itself for the Overlay it composes with), no code change.
Cells stay with the query service: separating them needs an index-blob
endpoint, out of scope here.

Env: `NORA_ENV_DIR` (parse trees) / `NORA_TREES_ROOT` (SQLite cell DBs)
→ Trees; `NORA_HISTORY_ROOT` → History; `NORA_STORE_REGISTRY` (publish
registry, 4b) resolves both from the served release set when their roots
are unset; `NORA_STORE_CORRECTIONS_ROOT`
(JSON) / `NORA_OVERLAY_DB` (SQLite) → Overlay, opened read-write — this
host is the Overlay authority — unless `NORA_OVERLAY_URL` names a peer
(then reads proxy and writes answer 409). `/healthz` lists the hosted
stores and each one's backend.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from fastapi import FastAPI

from core.src.stores import api
from core.src.stores.clients import store_backend_kind
from core.src.stores.history import open_history_store
from core.src.stores.overlay import open_overlay_store
from core.src.stores.registry import REGISTRY_ENV, RELEASE_SET_ENV, open_registry
from core.src.stores.trees import open_tree_store


def open_stores(names: "list[str]", env: "dict[str, str] | None" = None
                ) -> dict[str, Any]:
    """The requested stores, opened from `env` (default `os.environ`);
    a store whose env is unset opens as None (its routes answer 404)."""
    env = os.environ if env is None else env
    get = lambda k: (env.get(k) or "").strip()  # noqa: E731
    out: dict[str, Any] = {}
    for name in names:
        if name == "trees":
            env_dir, root = get("NORA_ENV_DIR"), get("NORA_TREES_ROOT")
            out[name] = (open_tree_store(env_dir, root, url="")
                         if (env_dir or root or get(REGISTRY_ENV)) else None)
        elif name == "history":
            out[name] = open_history_store(get("NORA_HISTORY_ROOT"), url="")
        elif name == "overlay":
            peer = get("NORA_OVERLAY_URL")
            out[name] = (open_overlay_store(url=peer, read_only=True) if peer
                         else open_overlay_store(get("NORA_STORE_CORRECTIONS_ROOT"),
                                                 db_path=get("NORA_OVERLAY_DB"),
                                                 url=""))
        else:
            raise ValueError(f"unknown store: {name} (choose from "
                             f"{', '.join(api.STORE_NAMES)})")
    return out


def build_app(stores: "list[str] | dict[str, Any]",
              overlay_writable: "bool | None" = None) -> FastAPI:
    """An ASGI app mounting only the requested routers. `stores` is a
    list of names (opened from the env) or `{name: store}` (opened by the
    caller — tests, embedding). `overlay_writable` None = derived from
    the store (a local read-write backend is the authority)."""
    opened = open_stores(list(stores)) if isinstance(stores, list) else dict(stores)
    for name in opened:
        if name not in api.STORE_NAMES:
            raise ValueError(f"unknown store: {name}")
    app = FastAPI(title="NORA stores")
    if "trees" in opened:
        app.include_router(api.trees_router(opened["trees"]))
    if "history" in opened:
        app.include_router(api.history_router(opened["history"]))
    if "overlay" in opened:
        writable = (api.is_overlay_authority(opened["overlay"])
                    if overlay_writable is None else overlay_writable)
        app.include_router(api.overlay_router(opened["overlay"], writable))

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        body: dict[str, Any] = {
            "ok": True,
            "hosted": sorted(opened),
            "stores": {n: store_backend_kind(s) for n, s in sorted(opened.items())},
        }
        if "overlay" in opened:
            body["overlay_authority"] = bool(
                api.is_overlay_authority(opened["overlay"])
                if overlay_writable is None else overlay_writable)
        reg = open_registry()
        if reg is not None:
            try:
                body["registry_label"] = reg.serving_label(
                    os.getenv(RELEASE_SET_ENV, "").strip())
            except Exception as exc:  # registry unreadable: say so, stay up
                body["registry_label"] = ""
                body["registry_error"] = type(exc).__name__
        return body

    return app


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m core.src.stores.serve",
        description="Host a subset of the NORA stores as one service.")
    ap.add_argument("--stores", required=True,
                    help="comma-separated: trees,history,overlay (any subset)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args(argv)
    names = [s.strip() for s in args.stores.split(",") if s.strip()]
    app = build_app(names)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
