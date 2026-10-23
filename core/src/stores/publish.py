"""Publish pipeline (strand corp-db-layer phase 4b; publish-design.md §4).

Moves verified build output into a serving root as versioned store files
and records them in the registry (``registry.py``), then gates, flips and
rolls back release sets, and garbage-collects old versions.

    python -m core.src.stores.publish publish --serve-root <root> --label <label>
        [--cells-src <dir>] [--trees-src <dir>] [--env-dir <dir>]
        [--sira-root <dir> [--enrich-run NAME]] [--base <label>|none]
        [--note TEXT] [--prompt-scheme TAG] [--dry-run]
    python -m core.src.stores.publish gate     --serve-root <root> --label <label>
        --kind golden-stage1|golden-stage2|manual --verdict pass|fail|waived
        [--run-ref ID] [--note TEXT]
    python -m core.src.stores.publish flip     --serve-root <root> --label <label>
        [--mode staged|expedited|override] [--force --reason TEXT]
        [--note TEXT] [--store-url URL]
    python -m core.src.stores.publish rollback --serve-root <root> --to <label>
        [--note TEXT] [--store-url URL] [--force]
    python -m core.src.stores.publish gc       --serve-root <root> --keep N [--dry-run]
    python -m core.src.stores.publish status   --serve-root <root> [--label <label>]

Units and identity (design §2): Cells and Trees are published per
``<mno>__<release>`` cell, History per MNO. A unit whose content identity
(the file's ``source_fingerprint``, plus its schema version) already has a
live version is **reused** — no file is written. A release set is always
complete: units this publish does not rebuild are inherited from the base
set (``--base``, default the active set), so a flip makes ``active`` equal
to the set. History is rebuilt for every MNO whose Trees members changed
(design §8.2) and reused otherwise.

Staging (§4.2): each new file is copied to ``<root>/<store>/.staging/``,
hashed, verified and renamed into place while the registry write lock is
held; a failure removes the staged files and rolls the registry back, so
no partial set is ever visible. Transfer is pluggable (``Transfer``):
``LocalTransfer`` is a same-filesystem copy.

Prints counts and identities only — never content. Error codes PUB-E001..
PUB-E010 (``core.src.pipeline.error_codes``).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from core.src.pipeline.error_codes import PipelineError
from core.src.stores.registry import (
    FLIP_MODES,
    GATE_KINDS,
    STORES,
    VERDICTS,
    NewUnit,
    Registry,
    RegistryError,
    StoreVersion,
    history_fingerprint,
    now_iso,
    registry_path,
    split_unit,
    unit_name,
)

_PREFIX = {"cells": "cell", "trees": "trees", "history": "history"}
_CHUNK = 1 << 20


# ---------------------------------------------------------------------------
# unit sources

@dataclass
class UnitSource:
    """A built unit file the publisher may stage."""
    store: str
    unit: str
    fingerprint: str
    schema_version: str
    path: Path
    meta: dict = field(default_factory=dict)


def read_unit_meta(path: Path) -> dict[str, str]:
    """The ``meta`` table of a store file (every store has one)."""
    uri = f"file:{quote(str(Path(path).resolve()))}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise PipelineError("PUB-E002", {"path": str(path), "reason": type(exc).__name__}) from exc
    try:
        return dict(con.execute("SELECT key, value FROM meta"))
    except sqlite3.Error as exc:
        raise PipelineError("PUB-E002", {"path": str(path), "reason": type(exc).__name__}) from exc
    finally:
        con.close()


def unit_source(store: str, path: Path) -> UnitSource:
    """Identify one file by its ``meta`` (never by its name)."""
    meta = read_unit_meta(path)
    if store == "cells":
        mno, rel = meta.get("cell_mno", ""), meta.get("cell_release", "")
    elif store == "trees":
        mno, rel = meta.get("mno", ""), meta.get("release", "")
    else:
        mno, rel = meta.get("mno", ""), ""
    fp = meta.get("source_fingerprint", "")
    if store == "history":
        try:
            fp = history_fingerprint(json.loads(meta.get("source", "") or "{}"))
        except ValueError:
            fp = ""
    if not mno or (store != "history" and not rel) or not fp:
        raise PipelineError("PUB-E002", {"path": str(path), "reason": "meta lacks identity"})
    return UnitSource(store, unit_name(mno, rel), fp, meta.get("schema_version", ""),
                      Path(path), meta)


def scan_units(store: str, src_dir: "str | Path") -> list[UnitSource]:
    """Every ``<prefix>-*.db`` under ``src_dir`` as unit sources (sorted by
    unit). A second file for one unit is an error."""
    d = Path(src_dir)
    out: dict[str, UnitSource] = {}
    for p in sorted(d.glob(f"{_PREFIX[store]}-*.db")):
        us = unit_source(store, p)
        if us.unit in out:
            raise PipelineError("PUB-E002", {"path": str(p), "reason": "duplicate unit"})
        out[us.unit] = us
    return [out[u] for u in sorted(out)]


# ---------------------------------------------------------------------------
# transfer

class Transfer(Protocol):
    """How a staged file reaches the serving root (design §4.2)."""

    def exists(self, rel: str) -> bool: ...

    def put(self, src: Path, rel: str) -> tuple[str, int]:
        """Copy ``src`` to ``rel`` under the root atomically; returns
        ``(sha256, bytes)`` of the file as placed."""
        ...

    def remove(self, rel: str) -> None: ...


def sha256_file(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


class LocalTransfer:
    """Same-filesystem copy: ``<root>/<store>/.staging/<name>`` → fsync →
    sha256 verified against the source → rename into ``<root>/<rel>``."""

    def __init__(self, root: "str | Path"):
        self.root = Path(root)

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def put(self, src: Path, rel: str) -> tuple[str, int]:
        dest = self.root / rel
        staging = dest.parent / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        tmp = staging / dest.name
        src_sha, _ = sha256_file(src)
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                shutil.copyfileobj(fin, fout, _CHUNK)
                fout.flush()
                os.fsync(fout.fileno())
            sha, n = sha256_file(tmp)
            if sha != src_sha:
                raise PipelineError("PUB-E007", {"file": rel, "reason": "sha256 mismatch after copy"})
            os.replace(tmp, dest)
        except OSError as exc:
            raise PipelineError("PUB-E007", {"file": rel, "reason": type(exc).__name__}) from exc
        finally:
            if tmp.exists():
                tmp.unlink()
        return sha, n

    def remove(self, rel: str) -> None:
        p = self.root / rel
        if p.exists():
            p.unlink()


# ---------------------------------------------------------------------------
# plan

@dataclass
class PlannedUnit:
    source: UnitSource
    version: int
    rel_file: str


@dataclass
class Plan:
    label: str
    base_label: str
    new: list[PlannedUnit] = field(default_factory=list)
    reused: list[StoreVersion] = field(default_factory=list)
    history_built: list[str] = field(default_factory=list)   # MNOs rebuilt

    def members_by_key(self) -> dict[tuple[str, str], object]:
        out: dict[tuple[str, str], object] = {m.key: m for m in self.reused}
        out.update({(p.source.store, p.source.unit): p for p in self.new})
        return out

    def summary(self) -> dict:
        per: dict[str, dict[str, int]] = {s: {"new": 0, "reused": 0} for s in STORES}
        for p in self.new:
            per[p.source.store]["new"] += 1
        for r in self.reused:
            per[r.store]["reused"] += 1
        return {"label": self.label, "base": self.base_label, "stores": per,
                "history_rebuilt": list(self.history_built),
                "new_units": [f"{p.source.store}/{p.source.unit}@v{p.version}" for p in self.new],
                "reused_units": [f"{r.store}/{r.unit}@v{r.version}" for r in self.reused]}


def _rel_file(store: str, unit: str, version: int) -> str:
    mno, rel = split_unit(unit)
    tag = f"{mno}-{rel}" if rel else mno
    return f"{store}/{_PREFIX[store]}-{tag}-v{version}.db"


def _trees_fps(plan_members: dict[tuple[str, str], object]) -> dict[str, dict[str, str]]:
    """``{mno: {release: trees source_fingerprint}}`` over the set's Trees."""
    out: dict[str, dict[str, str]] = {}
    for (store, unit), m in plan_members.items():
        if store != "trees":
            continue
        mno, rel = split_unit(unit)
        fp = m.source.fingerprint if isinstance(m, PlannedUnit) else m.fingerprint
        out.setdefault(mno, {})[rel] = fp
    return out


def _trees_path(root: Path, m: object) -> Path:
    return m.source.path if isinstance(m, PlannedUnit) else root / m.file


def build_history_units(root: Path, plan_members: dict[tuple[str, str], object],
                        mnos: list[str], work: Path) -> list[UnitSource]:
    """Rebuild History for ``mnos`` from the set's Trees members (reused
    files under the root, new files where they were built). The Trees
    store is handed an explicit per-MNO directory of links named as the
    builder expects."""
    from core.src.stores.history_build import build_history

    out: list[UnitSource] = []
    for mno in mnos:
        tdir = work / "trees-input" / mno
        tdir.mkdir(parents=True, exist_ok=True)
        for (store, unit), m in plan_members.items():
            if store != "trees" or split_unit(unit)[0] != mno:
                continue
            src = _trees_path(root, m)
            link = tdir / f"trees-{mno}-{split_unit(unit)[1]}.db"
            try:
                os.symlink(src.resolve(), link)
            except OSError:
                shutil.copyfile(src, link)
        hdir = work / "history" / mno
        hdir.mkdir(parents=True, exist_ok=True)
        build_history("", hdir, trees_root=tdir, mnos=[mno], force=True)
        built = scan_units("history", hdir)
        if len(built) != 1 or built[0].unit != mno:
            raise PipelineError("PUB-E002", {"path": str(hdir), "reason": "history build produced no unit"})
        out.append(built[0])
    return out


def plan_release_set(reg: Registry, label: str, *, cells: list[UnitSource],
                     trees: list[UnitSource], base_label: str, work: Path,
                     con: "sqlite3.Connection | None" = None,
                     build: bool = True) -> Plan:
    """Decide new vs reused per unit, inherit the rest from the base set,
    rebuild History where its Trees inputs changed (``build=False`` only
    lists the MNOs it would rebuild). ``con`` (an open write) makes
    version numbers authoritative; without it they are a preview."""
    if reg.release_set(label) is not None:
        raise PipelineError("PUB-E001", {"label": label})
    plan = Plan(label=label, base_label=base_label)
    base: dict[tuple[str, str], StoreVersion] = {}
    if base_label:
        base = {m.key: m for m in reg.members(base_label)}

    assigned: dict[tuple[str, str], int] = {}

    def place(us: UnitSource) -> None:
        existing = reg.find_version(us.store, us.unit, us.fingerprint, us.schema_version)
        if existing is not None:
            plan.reused.append(existing)
            return
        if con is not None:
            ver = reg.next_version(con, us.store, us.unit)
        else:
            vs = reg.versions(us.store, us.unit)
            ver = (vs[-1].version + 1) if vs else 1
        assigned[(us.store, us.unit)] = ver
        plan.new.append(PlannedUnit(us, ver, _rel_file(us.store, us.unit, ver)))

    for us in cells + trees:
        place(us)
    touched = {(p.source.store, p.source.unit) for p in plan.new} | {r.key for r in plan.reused}
    for key, m in base.items():
        if key not in touched and key[0] != "history":
            plan.reused.append(m)

    # History: identity from the set's Trees members.
    members = plan.members_by_key()
    fps = _trees_fps(members)
    base_history = {split_unit(k[1])[0]: m for k, m in base.items() if k[0] == "history"}
    to_build: list[str] = []
    for mno in sorted(fps):
        fp = history_fingerprint(fps[mno])
        existing = reg.find_version("history", mno, fp, schema_version=None)
        if existing is not None:
            plan.reused.append(existing)
        else:
            to_build.append(mno)
    for mno, m in base_history.items():            # MNOs with no Trees in the set
        if mno not in fps:
            plan.reused.append(m)
    if to_build:
        plan.history_built = to_build
        if build:
            for us in build_history_units(reg.root, members, to_build, work):
                place(us)
    if not plan.new and not plan.reused:
        raise PipelineError("PUB-E003", {"label": label})
    return plan


# ---------------------------------------------------------------------------
# publish

def code_version() -> str:
    try:
        repo = Path(__file__).resolve().parents[3]
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - platform dependent
        return os.getenv("USER", "")


def convert_cells(sira_root: Path, out_dir: Path, *, enrich_run: str = "",
                  cells: "list[str] | None" = None, source_label: str = "") -> None:
    """Build Cells from flat SIRA cells with the converter (needs the
    sandbox venv: bm25x)."""
    from sandbox.cell_db_convert import ConvertError, convert
    try:
        convert(sira_root, out_dir, cells_filter=cells, enrich_run=enrich_run,
                force=True, source_label=source_label)
    except ConvertError as exc:
        raise PipelineError("PUB-E002", {"path": str(sira_root), "reason": exc.code}) from exc


def publish(serve_root: "str | Path", label: str, *, cells_src: str = "",
            trees_src: str = "", env_dir: str = "", sira_root: str = "",
            enrich_run: str = "", cells_filter: "list[str] | None" = None,
            base: "str | None" = None, note: str = "", prompt_scheme: str = "",
            dry_run: bool = False, transfer: "Transfer | None" = None,
            work_dir: "str | Path" = "") -> dict:
    """Steps 1–2 of the pipeline: build or take the units, plan the set,
    stage the new files and record the set. Returns the plan summary
    (counts and identities only)."""
    root = Path(serve_root)
    reg = Registry(registry_path(root), read_only=False)
    reg.ensure()
    transfer = transfer or LocalTransfer(root)
    work_ctx = tempfile.TemporaryDirectory(prefix=".publish-", dir=str(work_dir) if work_dir else None)
    with work_ctx as wd:
        work = Path(wd)
        if sira_root:
            cdir = work / "cells"
            cdir.mkdir()
            convert_cells(Path(sira_root), cdir, enrich_run=enrich_run,
                          cells=cells_filter, source_label=label)
            cells_src = str(cdir)
        if env_dir and not trees_src:
            from core.src.stores.trees_build import build_trees
            tdir = work / "trees"
            tdir.mkdir()
            build_trees(env_dir, tdir, cells=cells_filter or None, force=True)
            trees_src = str(tdir)
        cells = scan_units("cells", cells_src) if cells_src else []
        trees = scan_units("trees", trees_src) if trees_src else []
        if cells_filter:
            wanted = set(cells_filter)
            cells = [c for c in cells if c.unit in wanted]
            trees = [t for t in trees if t.unit in wanted]
        if base is None:
            base_label = reg.active_label()
        else:
            base_label = "" if base.lower() == "none" else base
        if base_label and reg.release_set(base_label) is None:
            raise PipelineError("PUB-E006", {"label": base_label})

        if dry_run:
            plan = plan_release_set(reg, label, cells=cells, trees=trees,
                                    base_label=base_label, work=work, build=False)
            return {**plan.summary(), "dry_run": True}

        con = reg.begin_write()
        staged: list[str] = []
        try:
            plan = plan_release_set(reg, label, cells=cells, trees=trees,
                                    base_label=base_label, work=work, con=con)
            new_units: list[NewUnit] = []
            for p in plan.new:
                if transfer.exists(p.rel_file):
                    raise PipelineError("PUB-E007", {"file": p.rel_file, "reason": "target exists"})
                sha, n = transfer.put(p.source.path, p.rel_file)
                staged.append(p.rel_file)
                new_units.append(NewUnit(p.source.store, p.source.unit, p.source.fingerprint,
                                         p.rel_file, sha, n, p.source.schema_version))
            reg.record_release_set(
                con, label, new_units, plan.reused,
                versions={(p.source.store, p.source.unit): p.version for p in plan.new},
                created_by=_user(), note=note, code_version=code_version(),
                prompt_scheme=prompt_scheme)
            reg.commit_write(con)
        except BaseException:
            reg.rollback_write(con)
            for rel in staged:
                try:
                    transfer.remove(rel)
                except OSError:
                    pass
            raise
    return {**plan.summary(), "staged_bytes": sum(u.bytes for u in new_units)}


# ---------------------------------------------------------------------------
# gate / flip / rollback / verify

def gate(serve_root: "str | Path", label: str, kind: str, verdict: str, *,
         run_ref: str = "", note: str = "") -> int:
    reg = Registry(registry_path(serve_root), read_only=False)
    try:
        return reg.record_gate(label, kind, verdict, run_ref=run_ref, by=_user(), note=note)
    except RegistryError as exc:
        if exc.code == "REG-E007":
            raise PipelineError("PUB-E006", {"label": label}) from exc
        raise


def _check_files(reg: Registry, label: str, members: list[StoreVersion]) -> None:
    for m in members:
        if not (reg.root / m.file).is_file():
            raise PipelineError("PUB-E005", {"label": label, "file": m.file})


def flip(serve_root: "str | Path", label: str, *, mode: str = "staged", note: str = "",
         force: bool = False, reason: str = "", store_url: str = "",
         kind: str = "flip") -> dict:
    """Step 4 (+5 with ``store_url``): refuse without a passing gate unless
    forced (which records a waived manual gate with the reason), write
    ``active`` and the flip row in one transaction, then optionally reload
    and verify the serving store."""
    reg = Registry(registry_path(serve_root), read_only=False)
    try:
        members = reg.members(label)
    except RegistryError as exc:
        raise PipelineError("PUB-E006", {"label": label}) from exc
    _check_files(reg, label, members)
    if kind == "flip":
        ok, detail = reg.gate_verdict(label)
        if not ok:
            if not force:
                raise PipelineError("PUB-E004", {
                    "label": label,
                    "detail": ", ".join(f"{k}={v}" for k, v in sorted(detail.items())) or "no gate run"})
            if not reason.strip():
                raise PipelineError("PUB-E004", {"label": label, "detail": "--force needs --reason"})
            reg.record_gate(label, "manual", "waived", by=_user(), note=reason)
            mode = "override"
            note = note or reason
    else:
        if not reg.served_before(label) and not force:
            raise PipelineError("PUB-E009", {"label": label})
        mode = "expedited"
    result = reg.flip(label, kind=kind, mode=mode, by=_user(), note=note)
    result["members"] = {f"{m.store}/{m.unit}": m.version for m in members}
    if store_url:
        result["verify"] = verify_serving(reg, label, store_url)
    return result


def rollback(serve_root: "str | Path", to_label: str, *, note: str = "",
             force: bool = False, store_url: str = "") -> dict:
    return flip(serve_root, to_label, note=note, force=force, store_url=store_url,
                kind="rollback")


def _http_json(method: str, url: str, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - caller-supplied store URL
            return json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise PipelineError("PUB-E008", {"label": "", "what": f"{method} {url}: {type(exc).__name__}"}) from exc


def verify_serving(reg: Registry, label: str, store_url: str) -> dict:
    """Reload the serving store from the registry and compare its healthz
    with the registry's view of the set (design §4.5)."""
    base = store_url.rstrip("/")
    try:
        _http_json("POST", f"{base}/registry/reload")
        hz = _http_json("GET", f"{base}/healthz")
    except PipelineError as exc:
        raise PipelineError("PUB-E008", {"label": label, "what": exc.context.get("what", "")}) from exc
    want = {f"{m.store}/{m.unit}": m.version for m in reg.members(label)}
    got_label = str(hz.get("registry_label", ""))
    got = {}
    for store, units in (hz.get("registry_versions") or {}).items():
        for unit, info in (units or {}).items():
            got[f"{store}/{unit}"] = int(info.get("version", 0)) if isinstance(info, dict) else int(info)
    problems = []
    if got_label != label:
        problems.append(f"registry_label={got_label or '<none>'}")
    if got != want:
        missing = sorted(set(want) - set(got))
        extra = sorted(set(got) - set(want))
        wrong = sorted(k for k in set(want) & set(got) if want[k] != got[k])
        problems.append(f"versions: {len(missing)} missing, {len(extra)} extra, {len(wrong)} wrong")
    if problems:
        raise PipelineError("PUB-E008", {"label": label, "what": "; ".join(problems)})
    return {"registry_label": got_label, "units": len(got),
            "data_fingerprint": str(hz.get("data_fingerprint", ""))[:12]}


# ---------------------------------------------------------------------------
# gc / status

def gc(serve_root: "str | Path", keep: int, *, dry_run: bool = False,
       transfer: "Transfer | None" = None) -> dict:
    """Step 6: remove the files of versions outside the retention set
    (``Registry.gc_plan``); rows stay with ``removed_at`` set."""
    root = Path(serve_root)
    reg = Registry(registry_path(root), read_only=False)
    transfer = transfer or LocalTransfer(root)
    victims = reg.gc_plan(keep)
    removed: list[str] = []
    freed = 0
    for v in victims:
        if not dry_run:
            transfer.remove(v.file)
            reg.mark_removed(v.store, v.unit, v.version)
        removed.append(f"{v.store}/{v.unit}@v{v.version}")
        freed += v.bytes
    # Files under <root>/<store>/ no row knows (a crash between rename and
    # commit): reported, never deleted here.
    known = {v.file for v in reg.versions()}
    orphans = []
    for store in STORES:
        d = root / store
        if d.is_dir():
            orphans.extend(f"{store}/{p.name}" for p in d.glob(f"{_PREFIX[store]}-*.db")
                           if f"{store}/{p.name}" not in known)
    return {"keep": keep, "dry_run": dry_run, "removed": removed, "freed_bytes": freed,
            "protected_labels": sorted(reg.protected_labels(keep)), "orphan_files": sorted(orphans)}


def status(serve_root: "str | Path", label: str = "") -> dict:
    reg = Registry(registry_path(serve_root), read_only=True)
    out: dict = {"registry": str(reg.path), "active_label": reg.active_label(),
                 "active": {f"{r['store']}/{r['unit']}": r["version"] for r in reg.active()}}
    sets = []
    for rs in reg.release_sets():
        ok, detail = reg.gate_verdict(rs["label"])
        sets.append({"label": rs["label"], "created_at": rs["created_at"],
                     "code_version": rs["code_version"], "gate_ok": ok, "gates": detail,
                     "served_before": reg.served_before(rs["label"])})
    out["release_sets"] = sets
    out["flips"] = [{k: f[k] for k in ("label", "kind", "from_label", "at", "mode")}
                    for f in reg.flips(limit=10)]
    if label:
        out["members"] = {f"{m.store}/{m.unit}": {"version": m.version,
                                                  "fingerprint": m.fingerprint[:12],
                                                  "file": m.file}
                          for m in reg.members(label)}
    return out


# ---------------------------------------------------------------------------
# CLI

def _add_root(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--serve-root", required=True,
                    help="serving root; the registry is <root>/registry.db")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.src.stores.publish",
                                 description="Publish store versions into a serving root.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("publish", help="build/take units, stage new ones, record the release set")
    _add_root(p)
    p.add_argument("--label", required=True, help="release-set name (the build label)")
    p.add_argument("--cells-src", default="", help="dir of converter output cell-*.db")
    p.add_argument("--trees-src", default="", help="dir of trees_build output trees-*.db")
    p.add_argument("--env-dir", default="", help="build Trees from this env dir's parse output")
    p.add_argument("--sira-root", default="", help="build Cells from these flat SIRA cells (needs bm25x)")
    p.add_argument("--enrich-run", default="", help="pinned doc-enrich run for --sira-root")
    p.add_argument("--cells", default="", help="comma-separated <mno>__<release> filter")
    p.add_argument("--base", default=None, help="set to inherit untouched units from (default: active); 'none' = nothing")
    p.add_argument("--note", default="")
    p.add_argument("--prompt-scheme", default="")
    p.add_argument("--work-dir", default="", help="where temporary build output goes")
    p.add_argument("--dry-run", action="store_true")

    g = sub.add_parser("gate", help="record a gate run for a release set")
    _add_root(g)
    g.add_argument("--label", required=True)
    g.add_argument("--kind", required=True, choices=GATE_KINDS)
    g.add_argument("--verdict", required=True, choices=VERDICTS)
    g.add_argument("--run-ref", default="", help="eval run id")
    g.add_argument("--note", default="")

    f = sub.add_parser("flip", help="activate a gated release set")
    _add_root(f)
    f.add_argument("--label", required=True)
    f.add_argument("--mode", default="staged", choices=FLIP_MODES)
    f.add_argument("--force", action="store_true", help="flip without a passing gate (records a waived manual gate)")
    f.add_argument("--reason", default="", help="why the gate is waived (required with --force)")
    f.add_argument("--note", default="")
    f.add_argument("--store-url", default="", help="nora-store base URL to reload and verify")

    r = sub.add_parser("rollback", help="re-activate a previously served release set")
    _add_root(r)
    r.add_argument("--to", required=True, dest="label")
    r.add_argument("--force", action="store_true", help="allow a set that never served")
    r.add_argument("--note", default="")
    r.add_argument("--store-url", default="")

    c = sub.add_parser("gc", help="remove files of versions outside the retention set")
    _add_root(c)
    c.add_argument("--keep", type=int, required=True, help="release sets (and flips) to keep")
    c.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("status", help="registry summary")
    _add_root(s)
    s.add_argument("--label", default="")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "publish":
            cells = [x.strip() for x in args.cells.split(",") if x.strip()] or None
            out = publish(args.serve_root, args.label, cells_src=args.cells_src,
                          trees_src=args.trees_src, env_dir=args.env_dir,
                          sira_root=args.sira_root, enrich_run=args.enrich_run,
                          cells_filter=cells, base=args.base, note=args.note,
                          prompt_scheme=args.prompt_scheme, dry_run=args.dry_run,
                          work_dir=args.work_dir)
        elif args.cmd == "gate":
            out = {"gate_id": gate(args.serve_root, args.label, args.kind, args.verdict,
                                   run_ref=args.run_ref, note=args.note)}
        elif args.cmd == "flip":
            out = flip(args.serve_root, args.label, mode=args.mode, note=args.note,
                       force=args.force, reason=args.reason, store_url=args.store_url)
            out["rollback"] = (f"python -m core.src.stores.publish rollback --serve-root "
                               f"{args.serve_root} --to {out['from_label'] or '<label>'}")
        elif args.cmd == "rollback":
            out = rollback(args.serve_root, args.label, note=args.note, force=args.force,
                           store_url=args.store_url)
        elif args.cmd == "gc":
            out = gc(args.serve_root, args.keep, dry_run=args.dry_run)
        else:
            out = status(args.serve_root, args.label)
    except PipelineError as exc:
        print(f"[{exc.code}] {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"  hint: {exc.hint}", file=sys.stderr)
        return 1
    except RegistryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
