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
own links or copies); it only loses the sharing.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import stat
import time
import uuid
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

    def link_view(self, stage: str, cell: Cell, fp: str, view: Path) -> None:
        """Make `view` (a build's cell directory) a clone of the stored
        output, replacing whatever it held."""
        src = self.path(stage, cell, fp)
        view.parent.mkdir(parents=True, exist_ok=True)
        tmp = view.with_name(f".{view.name}.view-{uuid.uuid4().hex[:8]}")
        clone_tree(src, tmp)
        old = None
        if view.exists():
            old = view.with_name(f".{view.name}.old-{uuid.uuid4().hex[:8]}")
            os.rename(view, old)
        os.rename(tmp, view)
        if old is not None:
            _remove_tree(old)
