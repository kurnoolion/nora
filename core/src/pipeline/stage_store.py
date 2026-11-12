"""The host's stage store: write-once outputs by fingerprint (strand
stage-graph, design §4, step 2).

Layout under the stages root (``NORA_STAGES_ROOT``)::

    <stage>/<MNO>/<release>/<fp>/           the output, as the stage wrote it
    <stage>/<MNO>/<release>/<fp>.json       provenance, written after the rename
    <stage>/<MNO>/<release>/<fp>.building   marker while a producer computes it

A build keeps its own ``out/<stage>/<MNO>/<release>/`` tree, because the
SIRA lane, promote, the stores builders and the review pages read that
layout. The store is where a build *finds* an output another build (of
any stack on the host) already computed, and where it *puts* each output
it completes cleanly. Moving a tree in either direction hard-links each
file when the two paths share a mount and copies it otherwise (the batch
containers mount the build and the store separately, and the kernel
refuses a link across mounts).

Write-once: an output is cloned under a temp name beside its target and
renamed into place; its files are made read-only; nothing edits it after.
On a race the first rename wins and the loser's copy is discarded. A
build tree that shares inodes with the store is made private again
(`privatize`) before a stage writes into it, so a re-run never edits a
stored file, nor a promoted label that hard-links the build.

Removing a store entry never breaks a build or a label (they hold their
own links or copies); it only loses the sharing. `gc` (step 3, slice 4)
removes the entries no build manifest, label and release set on the host
names; it is a dry run unless told to apply::

    python3 -m core.src.pipeline.stage_store gc <stages-root> \
        --keep-from <builds-root> --keep-from <labels-root> \
        --registry <serve-registry>/registry.db [--min-age-hours 24] [--apply]
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import sqlite3
import stat
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.src.pipeline.cells import Cell
from core.src.pipeline.error_codes import PipelineError

#: Seconds a producer waits on another producer's ``.building`` marker; a
#: marker older than this is treated as abandoned.
DEFAULT_WAIT_SECONDS = 3600.0
WAIT_SECONDS_ENV = "NORA_STAGES_WAIT_SECONDS"
POLL_SECONDS = 5.0

#: Errors on which a hard link falls back to a copy.
_LINK_FALLBACK = {errno.EXDEV, errno.EPERM, errno.EMLINK, errno.EACCES, errno.ENOTSUP}

_WRITE_BITS = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError as e:
        if e.errno not in _LINK_FALLBACK:
            raise
        shutil.copy2(src, dst)


def clone_tree(src: Path, dst: Path) -> None:
    """Recreate `src` at `dst` (which must not exist): directories made,
    files hard-linked where possible, copied otherwise. Symlinks are
    copied as links."""
    dst.mkdir(parents=True)
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        for d in dirs:
            (dst / rel / d).mkdir(exist_ok=True)
        for f in files:
            s = Path(root) / f
            t = dst / rel / f
            if s.is_symlink():
                os.symlink(os.readlink(s), t)
            else:
                _link_or_copy(s, t)


def _files(tree: Path):
    for root, _dirs, files in os.walk(tree):
        for f in files:
            p = Path(root) / f
            if not p.is_symlink():
                yield p


def make_readonly(tree: Path) -> None:
    """Clear the write bits of every file under `tree` (directories keep
    theirs, so the tree can still be removed)."""
    for p in _files(tree):
        mode = p.stat().st_mode
        if mode & _WRITE_BITS:
            os.chmod(p, mode & ~_WRITE_BITS)


def privatize(tree: Path) -> int:
    """Give every file under `tree` that shares its inode (or is read-only)
    a private, writable copy, so a write cannot reach the store or a label.
    Returns the number of files replaced. Modification times are kept (the
    extract stage's per-document reuse compares them)."""
    n = 0
    if not tree.is_dir():
        return 0
    for p in list(_files(tree)):
        st = p.stat()
        if st.st_nlink > 1 or not (st.st_mode & stat.S_IWUSR):
            tmp = p.with_name(f".{p.name}.priv-{uuid.uuid4().hex[:8]}")
            shutil.copy2(p, tmp)
            os.chmod(tmp, (st.st_mode | stat.S_IWUSR) & 0o7777)
            os.replace(tmp, p)
            n += 1
    return n


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


class StageStore:
    """One host's stage store rooted at `root`."""

    def __init__(self, root: Path, wait_seconds: float | None = None,
                 poll_seconds: float = POLL_SECONDS, sleep=time.sleep):
        self.root = Path(root)
        if wait_seconds is None:
            try:
                wait_seconds = float(os.getenv(WAIT_SECONDS_ENV, "") or DEFAULT_WAIT_SECONDS)
            except ValueError:
                wait_seconds = DEFAULT_WAIT_SECONDS
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds
        self._sleep = sleep

    # -- paths ---------------------------------------------------------------

    def cell_dir(self, stage: str, cell: Cell) -> Path:
        return self.root / stage / cell.mno / cell.release

    def path(self, stage: str, cell: Cell, fp: str) -> Path:
        return self.cell_dir(stage, cell) / fp

    def provenance_path(self, stage: str, cell: Cell, fp: str) -> Path:
        return self.cell_dir(stage, cell) / f"{fp}.json"

    def marker_path(self, stage: str, cell: Cell, fp: str) -> Path:
        return self.cell_dir(stage, cell) / f"{fp}.building"

    # -- reads ---------------------------------------------------------------

    def has(self, stage: str, cell: Cell, fp: str) -> bool:
        """True when a complete output for `fp` is in the store (the rename
        into place is atomic, so the directory's presence is enough)."""
        return self.path(stage, cell, fp).is_dir()

    def entries(self, stage: str, cell: Cell) -> list[tuple[str, dict]]:
        """``(fp, provenance)`` of every complete output stored for the
        cell, newest first (by ``stored_at``)."""
        d = self.cell_dir(stage, cell)
        if not d.is_dir():
            return []
        out = [(p.stem, self.provenance(stage, cell, p.stem)) for p in d.glob("*.json")
               if self.has(stage, cell, p.stem)]
        return sorted(out, key=lambda e: str(e[1].get("stored_at", "")), reverse=True)

    def provenance(self, stage: str, cell: Cell, fp: str) -> dict:
        try:
            return json.loads(self.provenance_path(stage, cell, fp).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # -- the building marker -------------------------------------------------

    def mark_building(self, stage: str, cell: Cell, fp: str, build: str) -> None:
        """Announce that `build` computes `fp`. Best effort: a marker that
        cannot be written only costs another producer the wait."""
        m = self.marker_path(stage, cell, fp)
        try:
            m.parent.mkdir(parents=True, exist_ok=True)
            m.write_text(json.dumps({"build": build, "host": socket.gethostname(),
                                     "pid": os.getpid(), "at": _now_iso()}) + "\n",
                         encoding="utf-8")
        except OSError:
            pass

    def release(self, stage: str, cell: Cell, fp: str, build: str) -> None:
        """Remove the marker if `build` owns it."""
        m = self.marker_path(stage, cell, fp)
        try:
            if json.loads(m.read_text(encoding="utf-8")).get("build") == build:
                m.unlink()
        except (OSError, ValueError):
            pass

    def _foreign_marker(self, stage: str, cell: Cell, fp: str, build: str) -> bool:
        """A live marker of another build: present, not ours, younger than
        the wait."""
        m = self.marker_path(stage, cell, fp)
        try:
            age = time.time() - m.stat().st_mtime
            owner = json.loads(m.read_text(encoding="utf-8")).get("build")
        except (OSError, ValueError):
            return False
        return owner != build and age < self.wait_seconds

    def wait_for(self, stage: str, cell: Cell, fp: str, build: str) -> bool:
        """Wait while another build computes `fp`. Returns True when the
        output appeared, False when there is nothing to wait for or the
        wait ran out (the caller then computes it)."""
        deadline = time.monotonic() + self.wait_seconds
        while self._foreign_marker(stage, cell, fp, build):
            if self.has(stage, cell, fp):
                return True
            if time.monotonic() >= deadline:
                return False
            self._sleep(self.poll_seconds)
        return self.has(stage, cell, fp)

    # -- writes --------------------------------------------------------------

    def commit(self, stage: str, cell: Cell, fp: str, src: Path, provenance: dict) -> bool:
        """Put the complete output at `src` into the store under `fp`.
        Returns True when this call placed it, False when the store already
        held `fp` (another producer won; the store is not touched)."""
        dest = self.path(stage, cell, fp)
        if dest.is_dir():
            return False
        tmp = dest.with_name(f"{fp}.tmp-{uuid.uuid4().hex[:12]}")
        try:
            clone_tree(src, tmp)
            make_readonly(tmp)
            os.rename(tmp, dest)
        except OSError as e:
            _remove_tree(tmp)
            if dest.is_dir():
                return False
            raise PipelineError("PIP-E005", {"path": str(dest), "reason": str(e)}) from e
        pp = self.provenance_path(stage, cell, fp)
        ptmp = pp.with_name(pp.name + f".tmp-{uuid.uuid4().hex[:8]}")
        try:
            ptmp.write_text(json.dumps(dict(provenance, stored_at=_now_iso()),
                                       indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(ptmp, pp)
        except OSError:
            ptmp.unlink(missing_ok=True)   # the output stands; provenance is a record
        return True


# -- garbage collection (step 3, slice 4) ------------------------------------

MANIFEST_NAME = "STAGES.json"   # pipeline/manifest.py; repeated so this stays importable alone
DEFAULT_MIN_AGE_HOURS = 24.0


def _manifest_keys(path: Path) -> set[tuple[str, str, str]]:
    """``(stage, "<MNO>/<release>", fp)`` for every per-cell entry of one
    ``STAGES.json``. An unreadable manifest refuses the collection: what it
    names cannot be known."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stages = data.get("stages", {})
        out = set()
        for stage, cells in stages.items():
            for key, entry in cells.items():
                fp = entry.get("fp") if isinstance(entry, dict) else None
                if key != "*" and fp:
                    out.add((stage, key, fp))
        return out
    except (OSError, ValueError, AttributeError) as e:
        raise PipelineError("PIP-E006", {"reason": f"manifest {path} unreadable ({e})"}) from e


def _registry_keys(db: Path) -> set[tuple[str, str, str]]:
    """The same triples from every release set's ``stage_fps`` in one
    registry (read only; a registry from before the column names none)."""
    if not db.is_file():
        raise PipelineError("PIP-E006", {"reason": f"registry {db} not found"})
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(release_set)")}
            rows = con.execute("SELECT stage_fps FROM release_set").fetchall() if "stage_fps" in cols else []
        finally:
            con.close()
    except sqlite3.Error as e:
        raise PipelineError("PIP-E006", {"reason": f"registry {db} unreadable ({e})"}) from e
    out = set()
    for (raw,) in rows:
        if not raw:
            continue
        try:
            for stage, cells in json.loads(raw).items():
                for key, fp in cells.items():
                    if key != "*" and fp:
                        out.add((stage, key, fp))
        except (ValueError, AttributeError) as e:
            raise PipelineError("PIP-E006", {"reason": f"registry {db}: a stage_fps value is unreadable ({e})"}) from e
    return out


def keep_set(keep_from: list[Path], registries: list[Path],
             empty: list[str] | None = None) -> tuple[set[tuple[str, str, str]], int]:
    """Every stored output something on the host names, and the number of
    manifests read. Each ``keep_from`` directory contributes its own
    ``STAGES.json`` and its children's (a builds root holds builds, a
    labels root holds labels). A directory holding none is appended to
    `empty` (a mistyped root protects nothing; builds from before the
    manifest hold none either, so it is a warning, not a refusal)."""
    if not keep_from and not registries:
        raise PipelineError("PIP-E006", {"reason": "no --keep-from or --registry given; every entry would go"})
    keep: set[tuple[str, str, str]] = set()
    n = 0
    for d in keep_from:
        d = Path(d)
        if not d.is_dir():
            raise PipelineError("PIP-E006", {"reason": f"{d} is not a directory"})
        found = 0
        for m in [d / MANIFEST_NAME] + sorted(d.glob(f"*/{MANIFEST_NAME}")):
            if m.is_file():
                keep |= _manifest_keys(m)
                found += 1
        n += found
        if not found and empty is not None:
            empty.append(str(d))
    for db in registries:
        keep |= _registry_keys(Path(db))
    return keep, n


def _tree_bytes(path: Path) -> tuple[int, int]:
    """(bytes freed by removing `path`, bytes still held by other links)."""
    freed = shared = 0
    files = [path] if path.is_file() else list(_files(path))
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_nlink > 1:
            shared += st.st_size
        else:
            freed += st.st_size
    return freed, shared


@dataclass
class GcReport:
    kept: int = 0
    young: int = 0                 # unnamed, but younger than the minimum age
    building: int = 0              # unnamed, under a live building marker
    removed: list[str] = field(default_factory=list)   # "<stage>/<MNO>/<release>/<fp>"
    debris: list[str] = field(default_factory=list)    # temp dirs, orphan provenance, stale markers
    freed_bytes: int = 0
    shared_bytes: int = 0
    manifests: int = 0
    empty_sources: list[str] = field(default_factory=list)   # --keep-from dirs naming nothing


def gc(store: StageStore, keep_from: list[Path], registries: list[Path],
       min_age_hours: float = DEFAULT_MIN_AGE_HOURS, apply: bool = False,
       now: float | None = None) -> GcReport:
    """Remove (with `apply`) or list the store entries nothing names.

    Kept: every entry a manifest or release set names; an unnamed entry
    stored less than `min_age_hours` ago (a build may have found it and
    not yet recorded it); an entry under a live building marker. Debris
    (a temp dir of a failed commit or view, a provenance file without its
    output, a marker older than the store's wait) goes when older than
    the minimum age too. Nothing a build or label holds is touched: they
    own their links or copies."""
    empty: list[str] = []
    keep, n = keep_set(keep_from, registries, empty)
    now = time.time() if now is None else now
    min_age = min_age_hours * 3600.0
    rep = GcReport(manifests=n, empty_sources=empty)

    def age(p: Path) -> float:
        try:
            return now - p.stat().st_mtime
        except OSError:
            return 0.0

    def drop(p: Path, label: str, bucket: list[str]) -> None:
        f, sh = _tree_bytes(p)
        rep.freed_bytes += f
        rep.shared_bytes += sh
        bucket.append(label)
        if apply:
            if p.is_dir():
                _remove_tree(p)
            else:
                p.unlink(missing_ok=True)

    if not store.root.is_dir():
        return rep
    for stage_dir in sorted(x for x in store.root.iterdir() if x.is_dir() and not x.name.startswith(".")):
        for mno_dir in sorted(x for x in stage_dir.iterdir() if x.is_dir()):
            for rel_dir in sorted(x for x in mno_dir.iterdir() if x.is_dir()):
                cell = Cell(mno_dir.name, rel_dir.name)
                key = f"{cell.mno}/{cell.release}"
                where = f"{stage_dir.name}/{key}"
                outputs = set()
                for e in sorted(rel_dir.iterdir()):
                    name = e.name
                    if e.is_dir() and "." not in name:
                        outputs.add(name)
                        if (stage_dir.name, key, name) in keep:
                            rep.kept += 1
                        elif store._foreign_marker(stage_dir.name, cell, name, build=""):
                            rep.building += 1
                        elif age(e) < min_age:
                            rep.young += 1
                        else:
                            drop(e, f"{where}/{name}", rep.removed)
                            if apply:
                                store.provenance_path(stage_dir.name, cell, name).unlink(missing_ok=True)
                    elif ".tmp-" in name or ".view-" in name or ".old-" in name:
                        if age(e) >= min_age:
                            drop(e, f"{where}/{name}", rep.debris)
                for e in sorted(rel_dir.iterdir()):
                    if e.suffix == ".json" and e.stem not in outputs and age(e) >= min_age:
                        drop(e, f"{where}/{e.name}", rep.debris)
                    elif e.suffix == ".building" and age(e) >= max(store.wait_seconds, min_age):
                        drop(e, f"{where}/{e.name}", rep.debris)
                if apply:
                    for d in (rel_dir, mno_dir, stage_dir):
                        try:
                            d.rmdir()          # only when empty
                        except OSError:
                            break
    return rep


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return str(n)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="stage_store", description="The host's stage store.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gc", help="remove the entries no manifest, label or release set names (dry run by default)")
    g.add_argument("root", type=Path)
    g.add_argument("--keep-from", action="append", default=[], type=Path,
                   help="a builds root, a labels root or one build/label; repeat for every stack on the host")
    g.add_argument("--registry", action="append", default=[], type=Path,
                   help="a serve registry.db; repeat for every stack")
    g.add_argument("--min-age-hours", type=float, default=DEFAULT_MIN_AGE_HOURS)
    g.add_argument("--apply", action="store_true", help="remove; without it, only list")
    g.add_argument("-v", "--verbose", action="store_true", help="list every entry removed")
    args = ap.parse_args(argv)
    try:
        rep = gc(StageStore(args.root), args.keep_from, args.registry,
                 min_age_hours=args.min_age_hours, apply=args.apply)
    except PipelineError as e:
        print(f"{e}\n  fix: {e.hint}", file=sys.stderr)
        return 1
    for d in rep.empty_sources:
        print(f"stage_store gc: warning: {d} holds no {MANIFEST_NAME}; check the path", file=sys.stderr)
    verb = "removed" if args.apply else "to_remove"
    if args.verbose:
        for x in rep.removed + rep.debris:
            print(f"  {'removed' if args.apply else 'would remove'} {x}")
    print(f"stage_store gc: manifests={rep.manifests} kept={rep.kept} young={rep.young} "
          f"building={rep.building} {verb}={len(rep.removed)} debris={len(rep.debris)} "
          f"freed={_human(rep.freed_bytes)} still_linked={_human(rep.shared_bytes)}"
          + ("" if args.apply else " (dry run; --apply to remove)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
