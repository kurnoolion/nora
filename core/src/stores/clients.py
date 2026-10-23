"""HTTP clients for the stores nora-store hosts (strand corp-db-layer S6;
store-split-design §5, D-DRAFT-25/28).

One client per store, each implementing the store's protocol over the
`/stores/<store>/v1` JSON routes (`nora_store/stores_api.py`), so a
consumer built on the protocol does not know which backend it has:

- `HttpOverlayStore(base_url)` — reads, raw writes (`put_entry`,
  `set_*`) and `run_op(name, payload)`, which sends one edit-semantics
  op (`overlay_ops.STORE_OPS`) to the server's `edit` route. The server
  holds the lock, so the web never does a read-modify-write over the
  wire; `transaction()` is a no-op for the same reason.
- `HttpTreeStore(base_url)`, `HttpHistoryStore(base_url)` — reads.

Errors: `StoreUnavailable` when the service cannot answer (connection,
timeout, 5xx); a 400 carries a `ValueError` back (History's unknown
release); a 409 on an Overlay write is `OverlayReadOnlyError` (the
service is not the Overlay authority). Timeouts are short — a store
call is a page's dependency, not a batch job. `store_backend_kind` names
a store's backend for health reporting.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import httpx

from core.src.stores.errors import StoreUnavailable
from core.src.stores.overlay import OverlayReadOnlyError

DEFAULT_TIMEOUT = 10.0


def store_backend_kind(store: Any) -> str:
    """``http`` for a client, ``local`` for a backend, ``off`` for None."""
    if store is None:
        return "off"
    return "http" if isinstance(store, _HttpStore) else "local"


class _HttpStore:
    """Shared transport: JSON in, JSON out, errors mapped once."""

    path_prefix = ""

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT,
                 client: "httpx.Client | None" = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # an injected client (tests: FastAPI's TestClient) is owned by the
        # caller and keeps its own timeout; otherwise one pooled client
        # per store instance
        self._client = client or httpx.Client(timeout=timeout)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{self.path_prefix}{path}"

    def _call(self, method: str, path: str, params: "dict | None" = None,
              body: Any = None) -> Any:
        try:
            r = self._client.request(method, self._url(path),
                                     params=self._params(params), json=body)
        except httpx.HTTPError as exc:
            raise StoreUnavailable(
                f"{self.path_prefix} store at {self.base_url}: "
                f"{type(exc).__name__}") from exc
        if r.status_code >= 500:
            raise StoreUnavailable(
                f"{self.path_prefix} store at {self.base_url}: HTTP {r.status_code}")
        if r.status_code == 409:
            raise OverlayReadOnlyError(self._detail(r))
        if r.status_code == 400:
            raise ValueError(self._detail(r))
        if r.status_code >= 400:
            raise StoreUnavailable(
                f"{self.path_prefix} store at {self.base_url}: HTTP "
                f"{r.status_code} ({self._detail(r)})")
        return r.json()

    @staticmethod
    def _params(params: "dict | None") -> dict:
        """Empty strings travel as-is (a required `req_id` may be "");
        only None is dropped."""
        return {k: v for k, v in (params or {}).items() if v is not None}

    @staticmethod
    def _detail(r: httpx.Response) -> str:
        try:
            return str(r.json().get("detail", ""))
        except Exception:
            return r.text[:200]

    def _get(self, path: str, **params: Any) -> Any:
        return self._call("GET", path, params)

    def close(self) -> None:
        self._client.close()


# ── Overlay ─────────────────────────────────────────────────────────


class HttpOverlayStore(_HttpStore):
    """`OverlayStore` over `/stores/overlay/v1`. Edits that read-modify-
    write (`EnrichOverlayStore`'s ops) go through `run_op`, one request
    each, executed inside the server's transaction."""

    path_prefix = "/stores/overlay/v1"

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT,
                 client: "httpx.Client | None" = None, read_only: bool = False):
        super().__init__(base_url, timeout, client)
        self.read_only = read_only

    # reads
    def get_overlay(self, mno: str) -> dict[str, Any]:
        return self._get(f"/overlay/{mno}")

    def get_entry(self, mno: str, req_id: str) -> "dict[str, Any] | None":
        return self._get(f"/overlay/{mno}/entry", req_id=req_id)

    def list_mnos(self) -> list[str]:
        return list(self._get("/mnos"))

    def version(self, mno: str) -> float:
        return float(self._get(f"/overlay/{mno}/version"))

    def digest(self, mno: str, label: str = "") -> str:
        """The server's label-view digest (`overlay_ops.view_digest`)."""
        return str(self._get(f"/overlay/{mno}/digest", label=label))

    def accepted_labels(self) -> set[str]:
        return set(self._get("/labels/accepted"))

    def disabled_labels(self) -> set[str]:
        return set(self._get("/labels/disabled"))

    def reason_categories(self) -> "list[str] | None":
        cats = self._get("/reasons")
        return list(cats) if cats else None

    # writes
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """No client-side transaction exists over HTTP: every write below
        is one atomic request, and multi-step edits run server-side via
        `run_op`. Provided so protocol callers need no special case."""
        if self.read_only:
            raise OverlayReadOnlyError("overlay store opened read-only")
        yield

    def _write(self, method: str, path: str, body: Any,
               params: "dict | None" = None) -> Any:
        if self.read_only:
            raise OverlayReadOnlyError("overlay store opened read-only")
        return self._call(method, path, params, body)

    def put_entry(self, mno: str, req_id: str,
                  entry: "dict[str, Any] | None") -> None:
        self._write("PUT", f"/overlay/{mno}/entry", {"entry": entry or None},
                    {"req_id": req_id})

    def set_accepted_labels(self, labels: set[str]) -> None:
        self._write("PUT", "/labels/accepted", {"labels": sorted(labels)})

    def set_disabled_labels(self, labels: set[str]) -> None:
        self._write("PUT", "/labels/disabled", {"labels": sorted(labels)})

    def set_reason_categories(self, categories: list[str]) -> None:
        self._write("PUT", "/reasons", {"categories": list(categories)})

    def run_op(self, name: str, payload: dict) -> Any:
        """One edit-semantics op executed on the authority
        (`overlay_ops.run_op`); returns its JSON result. `payload` holds
        the op's arguments (for `edit`, including the record op)."""
        return self._write("POST", "/edit", {"op": name, "args": payload})


# ── Trees ───────────────────────────────────────────────────────────


class HttpTreeStore(_HttpStore):
    """`TreeStore` over `/stores/trees/v1`."""

    path_prefix = "/stores/trees/v1"

    def list_cells(self) -> list[dict]:
        return self._get("/cells")

    def list_docs(self, mno: str = "", release: str = "") -> list[str]:
        return self._get("/docs", mno=mno, release=release)

    def load_tree(self, doc_id: str, mno: str = "", release: str = "") -> dict:
        return self._get("/tree", doc_id=doc_id, mno=mno, release=release)

    def plans_for_mno(self, mno: str) -> dict[str, list[str]]:
        return self._get("/plans", mno=mno)

    def find_req(self, req_id: str, mno: str = "", release: str = "",
                 ignore_case: bool = False) -> list[dict]:
        return self._get("/find", req_id=req_id, mno=mno, release=release,
                         ignore_case=ignore_case)

    def reqs_for_plan(self, mno: str, release: str, plan: str) -> list[dict]:
        return self._get("/reqs", mno=mno, release=release, plan=plan)

    def get_node(self, mno: str, release: str, req_id: str) -> "dict | None":
        return self._get("/node", mno=mno, release=release, req_id=req_id)

    def children(self, mno: str, release: str, req_id: str) -> list[dict]:
        return self._get("/children", mno=mno, release=release, req_id=req_id)

    def subtree(self, mno: str, release: str, req_id: str) -> list[dict]:
        return self._get("/subtree", mno=mno, release=release, req_id=req_id)

    def ancestors(self, mno: str, release: str, req_id: str) -> list[dict]:
        return self._get("/ancestors", mno=mno, release=release, req_id=req_id)

    def find_by_fingerprint(self, fingerprint: str, mno: str = "") -> list[dict]:
        return self._get("/fingerprint", fingerprint=fingerprint, mno=mno)


# ── History ─────────────────────────────────────────────────────────


class HttpHistoryStore(_HttpStore):
    """`HistoryStore` over `/stores/history/v1`."""

    path_prefix = "/stores/history/v1"

    def list_mnos(self) -> list[str]:
        return self._get("/mnos")

    def releases(self, mno: str) -> list[str]:
        return self._get("/releases", mno=mno)

    def req_history(self, mno: str, plan_id: str, req_id: str,
                    from_rel: str = "", to_rel: str = "") -> list[dict]:
        return self._get("/req", mno=mno, plan_id=plan_id, req_id=req_id,
                         from_rel=from_rel, to_rel=to_rel)

    def plan_history(self, mno: str, plan_id: str,
                     from_rel: str = "", to_rel: str = "") -> list[dict]:
        return self._get("/plan", mno=mno, plan_id=plan_id,
                         from_rel=from_rel, to_rel=to_rel)

    def last_changed(self, mno: str, plan_id: str, req_id: str) -> "str | None":
        return self._get("/req/last-changed", mno=mno, plan_id=plan_id,
                         req_id=req_id)

    def plan_last_changed(self, mno: str, plan_id: str) -> "str | None":
        return self._get("/plan/last-changed", mno=mno, plan_id=plan_id)

    def diff_releases(self, mno: str, rel_a: str, rel_b: str,
                      diffs: bool = False) -> dict:
        return self._get("/diff", mno=mno, rel_a=rel_a, rel_b=rel_b, diffs=diffs)

    def latest_changes(self, mno: str, diffs: bool = False) -> "dict | None":
        return self._get("/latest", mno=mno, diffs=diffs)
