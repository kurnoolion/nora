"""Build the History store: one SQLite DB per MNO (strand corp-db-layer S5).

Reads every release of an MNO through ``open_tree_store`` (the SQLite Trees
store under ``--trees-root`` or ``$NORA_TREES_ROOT``, else the parse trees
under ``--env-dir``) and writes ``<out>/history-<mno>.db`` (schema in
``history.py``). The DB is written under a temp name, verified, then
renamed into place, so a reader never sees a partial file. Verify, through
``SqliteHistoryStore`` over the new file: the release list equals the
Trees store's; the current requirement count equals the latest release's;
for a sample of requirements, the latest version's title and text equal
the latest release's tree (same ``text_fp``). On a mismatch the temp file
is removed and nothing is replaced.

Usage:

    python -m core.src.stores.history_build --env-dir <dir> \
        [--trees-root <dir>] --out <dir> [--mno MNO,...] [--force]

Prints counts only — never requirement content.

Error codes (stable):
    HIS-E001  the Trees store has no cells
    HIS-E002  output DB exists and --force was not given
    HIS-E003  a requested MNO has no cells
    HIS-E004  SQLite write failure
    HIS-E005  verification mismatch
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from core.src.models.fingerprint import text_fp
from core.src.pipeline.error_codes import PipelineError
from core.src.stores.history import (
    REQUIREMENT,
    SqliteHistoryStore,
    db_name,
    release_nodes,
    write_history_db,
)
from core.src.stores.trees import (
    FileTreeStore,
    SqliteTreeStore,
    TreeStore,
    open_tree_store,
    parse_dir,
    source_fingerprint,
)

_SAMPLE = 50        # requirements whose latest version is checked per MNO


def build_history(env_dir: "str | Path", out_dir: "str | Path",
                  trees_root: "str | Path" = "", mnos: "list[str] | None" = None,
                  force: bool = False) -> dict:
    """Build + verify every selected MNO; returns the counts report.
    Raises PipelineError."""
    out_dir = Path(out_dir)
    store = open_tree_store(env_dir, trees_root)
    cells = store.list_cells()
    if not cells:
        raise PipelineError("HIS-E001", {"source": type(store).__name__})
    available = sorted({c["mno"] for c in cells})
    selected = mnos or available
    for mno in selected:
        if mno not in available:
            raise PipelineError("HIS-E003", {"mno": mno})
    for mno in selected:               # refuse before writing anything
        target = out_dir / db_name(mno)
        if target.exists() and not force:
            raise PipelineError("HIS-E002", {"path": str(target)})

    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"mnos": 0, "per_mno": {}}
    for mno in selected:
        rels = [c["release"] for c in cells if c["mno"] == mno]
        report["per_mno"][mno] = _build_mno(store, Path(env_dir), out_dir, mno, rels)
        report["mnos"] += 1
    return report


def _source(store: TreeStore, env_dir: Path, mno: str, rel: str) -> str:
    """The cell's Trees source fingerprint, '' when not known."""
    if isinstance(store, SqliteTreeStore):
        return store.cell_meta(mno, rel).get("source_fingerprint", "")
    if isinstance(store, FileTreeStore):
        return source_fingerprint(sorted((parse_dir(env_dir) / mno / rel)
                                         .glob("*_tree.json")))
    return ""


def _build_mno(store: TreeStore, env_dir: Path, out_dir: Path, mno: str,
               rels: list[str]) -> dict:
    states = [release_nodes(store, mno, rel) for rel in rels]
    source = {rel: _source(store, env_dir, mno, rel) for rel in rels}
    fd, tmp_name = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=out_dir)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.unlink()                    # sqlite creates it fresh
        try:
            counts = write_history_db(tmp, mno, rels, states, source)
        except sqlite3.Error as exc:
            raise PipelineError("HIS-E004", {"reason": type(exc).__name__}, exc) from exc
        _verify(tmp, mno, rels, states[-1])
        os.replace(tmp, out_dir / db_name(mno))
        return counts
    finally:
        if tmp.exists():
            tmp.unlink()


def _verify(db_path: Path, mno: str, rels: list[str],
            latest: dict[str, dict]) -> None:
    hist = SqliteHistoryStore(db_paths=[db_path])

    def fail(what: str, n: int = 1):
        raise PipelineError("HIS-E005", {"mno": mno, "what": what, "count": str(n)})

    if hist.releases(mno) != rels:
        fail("release list")
    reqs = sorted((n["plan_id"], n["local_id"], n["title"], n["text"])
                  for n in latest.values() if n["kind"] == REQUIREMENT)
    current = hist.diff_releases(mno, rels[-1], rels[-1])
    if current["changes"]:
        fail("self diff", len(current["changes"]))
    step = max(1, len(reqs) // _SAMPLE)
    bad = 0
    for plan, rid, title, text in reqs[::step]:
        vs = hist.req_history(mno, plan, rid)
        # canonical equality: content is stored once per text_fp, so a
        # whitespace-only edit keeps the first-seen display text
        if not vs or vs[-1]["valid_to"] != "" or \
                text_fp(vs[-1]["title"], vs[-1]["text"]) != text_fp(title, text):
            bad += 1
    if bad:
        fail("latest requirement text", bad)
    con = sqlite3.connect(str(db_path))
    try:
        (n_current,) = con.execute(
            "SELECT COUNT(*) FROM version v JOIN node n ON n.key = v.node_key"
            " WHERE n.kind = ? AND v.valid_to = ''", (REQUIREMENT,)).fetchone()
    finally:
        con.close()
    if n_current != len(reqs):
        fail("current requirement count", abs(len(reqs) - n_current))


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        prog="history_build",
        description="Build one History SQLite DB per MNO from the Trees store.")
    ap.add_argument("--env-dir", required=True,
                    help="env dir holding out/parse/ (file Trees backend)")
    ap.add_argument("--trees-root", default="",
                    help="Trees SQLite root (default: $NORA_TREES_ROOT, else --env-dir)")
    ap.add_argument("--out", required=True, help="directory for history-<mno>.db")
    ap.add_argument("--mno", default="",
                    help="comma-separated MNOs (default: every MNO in the Trees store)")
    ap.add_argument("--force", action="store_true", help="replace existing DBs")
    args = ap.parse_args(argv)
    mnos = [m.strip() for m in args.mno.split(",") if m.strip()] or None
    try:
        report = build_history(args.env_dir, args.out, trees_root=args.trees_root,
                               mnos=mnos, force=args.force)
    except PipelineError as exc:
        print(f"FAILED {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"  hint: {exc.hint}", file=sys.stderr)
        return 1
    print(f"built {report['mnos']} history DB(s)")
    for mno, c in sorted(report["per_mno"].items()):
        print(f"  {mno}: {c['n_releases']} releases, {c['n_plans']} plans, "
              f"{c['n_sections']} sections, {c['n_requirements']} requirements, "
              f"{c['n_versions']} versions, {c['n_content']} content rows")
    print("verified: release lists, self-diffs and sampled latest texts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
