"""Store routes hosted by nora-store (strand corp-db-layer S6;
store-split-design §5, D-DRAFT-25/28): `/stores/{cells,trees,history,
overlay}/v1`, JSON only.

The Trees, History and Overlay routers are the generic ones in
`core.src.stores.api` (hostable by `core.src.stores.serve` on their own);
this module adds the Cells router, which needs the service's loaded
`_cells` state, and `mount` wires all four onto the service app.

- cells — served from `_cells` (meta, corpus rows, raw SIRA phrases,
  composed effective sets). No index blobs: the index is this service's
  own consumer. Cells stay with the query service for now.
- overlay — this service is the Overlay AUTHORITY (its store is opened
  read-write) unless `NORA_OVERLAY_URL` names a peer: then reads are
  proxied through the client and writes answer 409.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, FastAPI, HTTPException, Query

from core.src.stores import api
from core.src.stores.api import PREFIX, is_overlay_authority  # noqa: F401  (re-export)


def cells_router(get_cells: Callable[[], dict],
                 get_variant: "Callable[[tuple, str], Any] | None" = None
                 ) -> APIRouter:
    """Over the service's loaded cells: `get_cells()` is the `_cells`
    dict `{(mno, release): CellState}`; `get_variant((mno, release),
    label)` resolves a label view (None → the default state)."""
    r = APIRouter(prefix=f"{PREFIX}/cells/v1", tags=["stores:cells"])

    def state(mno: str, release: str, label: str = "") -> Any:
        key = (mno, release)
        st = get_cells().get(key)
        if st is None:
            raise HTTPException(status_code=404, detail="unknown cell")
        if label and get_variant is not None:
            st = get_variant(key, label) or st
        return st

    def summary(st: Any) -> dict:
        return {"mno": st.cell[0], "release": st.cell[1],
                "requirements": len(st.doc_ids), "loaded_at": st.loaded_at,
                "engine": st.engine, "composed": st.composed,
                "schema_version": (st.db_meta or {}).get("schema_version", "")}

    def ids(st: Any, req_id: list[str]) -> list[str]:
        return [rid for rid in req_id if rid in st.doc_id_to_idx] if req_id else st.doc_ids

    @r.get("/cells")
    def cells() -> list[dict]:
        return [summary(st) for _, st in sorted(get_cells().items())]

    @r.get("/cells/{mno}/{release}/meta")
    def meta(mno: str, release: str) -> dict:
        st = state(mno, release)
        return {**summary(st), "db_meta": dict(st.db_meta or {}),
                "enrich_model": st.enrich_model,
                "overlay_digest": st.overlay_digest,
                "corrections": dict(st.overlay_applied),
                "fingerprints": len(st.fingerprints)}

    @r.get("/cells/{mno}/{release}/rows")
    def rows(mno: str, release: str,
             req_id: list[str] = Query(default=[])) -> list[dict]:
        st = state(mno, release)
        return [{"req_id": rid, **(st.corpus_by_id.get(rid) or {}),
                 "fingerprint": st.fingerprints.get(rid, "")}
                for rid in ids(st, req_id)]

    @r.get("/cells/{mno}/{release}/phrases")
    def phrases(mno: str, release: str,
                req_id: list[str] = Query(default=[])) -> dict[str, list[str]]:
        """Raw SIRA phrases per requirement (pre-overlay)."""
        st = state(mno, release)
        return {rid: st.llm_words.get(rid, []) for rid in ids(st, req_id)
                if req_id or rid in st.llm_words}

    @r.get("/cells/{mno}/{release}/effective")
    def effective(mno: str, release: str, label: str = "",
                  req_id: list[str] = Query(default=[])) -> dict[str, dict]:
        """The composed view: effective phrases, held records, suppression."""
        st = state(mno, release, label.strip())
        return {rid: {"effective": st.effective_words.get(rid, []),
                      "held": st.held_by_id.get(rid, []),
                      "suppressed": rid in st.suppressed_ids}
                for rid in ids(st, req_id)
                if req_id or rid in st.effective_words or rid in st.held_by_id
                or rid in st.suppressed_ids}

    return r


def mount(app: FastAPI, *,
          overlay: Callable[[], Any],
          trees: Callable[[], Any],
          history: Callable[[], Any],
          cells: Callable[[], dict],
          variant: "Callable[[tuple, str], Any] | None" = None) -> None:
    """Mount the four routers; the store getters are called per request
    (nora-store opens its Overlay lazily so a peer URL and the backend
    stay the service's own decision)."""
    app.include_router(cells_router(cells, variant))
    app.include_router(api.trees_router(trees))
    app.include_router(api.history_router(history))
    app.include_router(api.overlay_router(
        overlay, lambda: is_overlay_authority(overlay())))
