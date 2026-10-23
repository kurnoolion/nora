"""Store routes — one `APIRouter` per store, hostable by any service
(strand corp-db-layer S6; store-split-design §5, D-DRAFT-25/28).

`trees_router(store)`, `history_router(store)` and `overlay_router(store,
writable)` serve a store's full protocol as JSON under
`/stores/<store>/v1`; the HTTP clients in `clients.py` are their
consumers. nora-store mounts them beside its Cells routes
(`nora_store/stores_api.py`); `serve.py` hosts any subset on its own,
so splitting a store out of nora-store is a deployment choice: run the
host, set `NORA_<STORE>_URL` on every consumer, no code change.

`store` is the store instance, or a zero-argument callable returning
one for services that open lazily (nora-store resolves its Overlay per
request; None → 404 "not configured"). `writable` (Overlay) likewise:
True when this host is the Overlay AUTHORITY — a local backend opened
read-write; a peer that reads through `HttpOverlayStore` answers 409.
`POST /edit` runs one `overlay_ops` op inside the backend transaction,
so nora-web never does a read-modify-write over the wire.

Versioned in the path: additive fields within `v1`; a breaking change
mounts `v2` beside it (design §9). Routes return data and log nothing.
No `nora_store` import: this module depends on the stores package only.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.src.stores import overlay_ops
from core.src.stores.errors import StoreUnavailable
from core.src.stores.history import HistoryStore
from core.src.stores.overlay import OverlayReadOnlyError, OverlayStore
from core.src.stores.trees import TreeStore

PREFIX = "/stores"
STORE_NAMES = ("trees", "history", "overlay")


def _resolve(store: Any) -> Any:
    return store() if callable(store) else store


def _require(store: Any, name: str) -> Any:
    store = _resolve(store)
    if store is None:
        raise HTTPException(status_code=404,
                            detail=f"{name} store is not configured on this service")
    return store


def _guard(fn: Callable[[], Any]) -> Any:
    """Map store errors to HTTP: a peer store that cannot answer is 503,
    a bad argument (unknown release) is 400."""
    try:
        return fn()
    except StoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def is_overlay_authority(store: Any) -> bool:
    """True when this process writes the Overlay: a local backend opened
    read-write. A client (peer read) or a read-only open is not."""
    from core.src.stores.clients import store_backend_kind
    return (store is not None and store_backend_kind(store) != "http"
            and not getattr(store, "read_only", False))


# ── trees ───────────────────────────────────────────────────────────


def trees_router(store: "TreeStore | Callable[[], TreeStore | None] | None") -> APIRouter:
    r = APIRouter(prefix=f"{PREFIX}/trees/v1", tags=["stores:trees"])

    def st() -> TreeStore:
        return _require(store, "trees")

    @r.get("/cells")
    def cells() -> list[dict]:
        return _guard(lambda: st().list_cells())

    @r.get("/docs")
    def docs(mno: str = "", release: str = "") -> list[str]:
        return _guard(lambda: st().list_docs(mno, release))

    @r.get("/tree")
    def tree(doc_id: str, mno: str = "", release: str = "") -> dict:
        return _guard(lambda: st().load_tree(doc_id, mno, release))

    @r.get("/plans")
    def plans(mno: str) -> dict[str, list[str]]:
        return _guard(lambda: st().plans_for_mno(mno))

    @r.get("/find")
    def find(req_id: str, mno: str = "", release: str = "",
             ignore_case: bool = False) -> list[dict]:
        return _guard(lambda: st().find_req(req_id, mno, release,
                                            ignore_case=ignore_case))

    @r.get("/reqs")
    def reqs(mno: str, release: str, plan: str = "") -> list[dict]:
        return _guard(lambda: st().reqs_for_plan(mno, release, plan))

    @r.get("/node")
    def node(mno: str, release: str, req_id: str) -> "dict | None":
        return _guard(lambda: st().get_node(mno, release, req_id))

    @r.get("/children")
    def children(mno: str, release: str, req_id: str) -> list[dict]:
        return _guard(lambda: st().children(mno, release, req_id))

    @r.get("/subtree")
    def subtree(mno: str, release: str, req_id: str) -> list[dict]:
        return _guard(lambda: st().subtree(mno, release, req_id))

    @r.get("/ancestors")
    def ancestors(mno: str, release: str, req_id: str) -> list[dict]:
        return _guard(lambda: st().ancestors(mno, release, req_id))

    @r.get("/fingerprint")
    def fingerprint(fingerprint: str, mno: str = "") -> list[dict]:
        return _guard(lambda: st().find_by_fingerprint(fingerprint, mno))

    return r


# ── history ─────────────────────────────────────────────────────────


def history_router(store: "HistoryStore | Callable[[], HistoryStore | None] | None"
                   ) -> APIRouter:
    r = APIRouter(prefix=f"{PREFIX}/history/v1", tags=["stores:history"])

    def st() -> HistoryStore:
        return _require(store, "history")

    @r.get("/mnos")
    def mnos() -> list[str]:
        return _guard(lambda: st().list_mnos())

    @r.get("/releases")
    def releases(mno: str) -> list[str]:
        return _guard(lambda: st().releases(mno))

    @r.get("/req")
    def req(mno: str, plan_id: str, req_id: str, from_rel: str = "",
            to_rel: str = "") -> list[dict]:
        return _guard(lambda: st().req_history(mno, plan_id, req_id,
                                               from_rel, to_rel))

    @r.get("/plan")
    def plan(mno: str, plan_id: str, from_rel: str = "",
             to_rel: str = "") -> list[dict]:
        return _guard(lambda: st().plan_history(mno, plan_id, from_rel, to_rel))

    @r.get("/req/last-changed")
    def req_last_changed(mno: str, plan_id: str, req_id: str) -> "str | None":
        return _guard(lambda: st().last_changed(mno, plan_id, req_id))

    @r.get("/plan/last-changed")
    def plan_last_changed(mno: str, plan_id: str) -> "str | None":
        return _guard(lambda: st().plan_last_changed(mno, plan_id))

    @r.get("/diff")
    def diff(mno: str, rel_a: str = "", rel_b: str = "",
             diffs: bool = False) -> dict:
        return _guard(lambda: st().diff_releases(mno, rel_a, rel_b, diffs))

    @r.get("/latest")
    def latest(mno: str, diffs: bool = False) -> "dict | None":
        return _guard(lambda: st().latest_changes(mno, diffs))

    return r


# ── overlay ─────────────────────────────────────────────────────────


class _EntryBody(BaseModel):
    entry: "dict[str, Any] | None" = None


class _LabelsBody(BaseModel):
    labels: list[str]


class _ReasonsBody(BaseModel):
    categories: list[str]


def overlay_router(store: "OverlayStore | Callable[[], OverlayStore | None] | None",
                   writable: "bool | Callable[[], bool]") -> APIRouter:
    r = APIRouter(prefix=f"{PREFIX}/overlay/v1", tags=["stores:overlay"])

    def st() -> OverlayStore:
        return _require(store, "overlay")

    def writer() -> OverlayStore:
        s = st()
        if not (writable() if callable(writable) else writable):
            raise HTTPException(
                status_code=409,
                detail="not the overlay authority: this service reads the "
                       "Overlay from a peer (NORA_OVERLAY_URL) — send writes "
                       "to that store")
        return s

    def _write(fn: Callable[[], Any]) -> Any:
        try:
            return _guard(fn)
        except OverlayReadOnlyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # reads
    @r.get("/mnos")
    def mnos() -> list[str]:
        return _guard(lambda: st().list_mnos())

    @r.get("/overlay/{mno}")
    def overlay(mno: str) -> dict:
        return _guard(lambda: st().get_overlay(mno))

    @r.get("/overlay/{mno}/entry")
    def entry(mno: str, req_id: str) -> "dict | None":
        return _guard(lambda: st().get_entry(mno, req_id))

    @r.get("/overlay/{mno}/version")
    def version(mno: str) -> float:
        return _guard(lambda: st().version(mno))

    @r.get("/overlay/{mno}/digest")
    def digest(mno: str, label: str = "") -> str:
        def run() -> str:
            s = st()
            if hasattr(s, "digest"):            # peer: its authority computes
                return s.digest(mno, label)
            filtered = overlay_ops.filter_overlay(
                s.get_overlay(mno),
                overlay_ops.allowed_labels(s.accepted_labels(), label))
            return overlay_ops.view_digest(filtered)
        return _guard(run)

    @r.get("/labels/accepted")
    def accepted() -> list[str]:
        return _guard(lambda: sorted(st().accepted_labels()))

    @r.get("/labels/disabled")
    def disabled() -> list[str]:
        return _guard(lambda: sorted(st().disabled_labels()))

    @r.get("/reasons")
    def reasons() -> "list[str] | None":
        return _guard(lambda: st().reason_categories())

    # writes (authority only)
    @r.put("/overlay/{mno}/entry")
    def put_entry(mno: str, req_id: str, body: _EntryBody) -> dict:
        _write(lambda: writer().put_entry(mno, req_id, body.entry))
        return {"ok": True}

    @r.put("/labels/accepted")
    def put_accepted(body: _LabelsBody) -> dict:
        _write(lambda: writer().set_accepted_labels(set(body.labels)))
        return {"ok": True}

    @r.put("/labels/disabled")
    def put_disabled(body: _LabelsBody) -> dict:
        _write(lambda: writer().set_disabled_labels(set(body.labels)))
        return {"ok": True}

    @r.put("/reasons")
    def put_reasons(body: _ReasonsBody) -> dict:
        _write(lambda: writer().set_reason_categories(list(body.categories)))
        return {"ok": True}

    @r.post("/edit")
    def edit(body: dict) -> Any:
        """One edit-semantics op (`overlay_ops.STORE_OPS`) inside the
        backend transaction: `{"op": <store op>, "args": {...}}` (for
        `edit`, `args` carries the record op, words, stamp fields); the
        response is the op's result (the req's entry for `edit`)."""
        name = str(body.get("op") or "")
        if name not in overlay_ops.STORE_OPS:
            raise HTTPException(status_code=400, detail=f"unknown store op: {name}")
        args = body.get("args") or {}
        if not isinstance(args, dict):
            raise HTTPException(status_code=400, detail="args must be an object")
        return _write(lambda: overlay_ops.run_op(writer(), name, args))

    return r
