"""Golden-set release map maintenance (strand golden-eval, release-handling
design): show the map, write its first version, advance an MNO's ``N``
after an ingestion + flip, and clear review flags.

Usage:
    python -m core.src.eval.golden_release_cli show    --env-dir <env> [--version K]
    python -m core.src.eval.golden_release_cli migrate --env-dir <env> [--n MNO=REL ...] [--yes]
    python -m core.src.eval.golden_release_cli advance --env-dir <env> --mno MNO --release REL [--yes]
    python -m core.src.eval.golden_release_cli review  --env-dir <env> [--clear SAMPLE_ID [--mno MNO]]

``--env-dir`` is the env whose parse trees (``out/parse/<mno>/<release>/``)
hold the requirement texts and whose ``eval/golden/`` holds the samples
and the map — the same env shape ``golden_cli`` uses, so on a docker
deployment it runs in the pipeline container where ``GOLDEN_DIR`` is
mounted at ``<env>/eval/golden``. ``migrate`` and ``advance`` print
their plan and change nothing without ``--yes``.

The text check: when an MNO's ``N`` moves, every standard entry of that
MNO whose stored release is older than the new ``N`` is compared by text
between the two releases — unchanged / changed / absent (not in the new
release) / unverified (not found in its stored release). Unchanged
entries have their stored release moved forward (it is provenance only;
standard samples match without it). A sample with any other outcome, and
every relative version sample of that MNO (its answer holds for one
release pair only), gets a review flag in ``golden_meta["review"]``. The
runner skips Stage 2 for flagged samples (GEV-W002); Stage 1 still
scores them.

Output names sample and requirement ids — local inspection material, not
chat-pasteable (NFR-8). The counts line at the end is.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.src.extraction.release_key import is_valid_release, release_order_key

from .golden import (
    KIND_STANDARD,
    KIND_VERSION,
    GoldenEvalError,
    GoldenSample,
    golden_root,
    is_relative_release,
    load_samples,
    save_sample,
)
from .release_map import ReleaseMap, load_release_map, save_release_map

# Same shape as the query analyzer's release patterns: a month name and a
# 20xx year in either order. Used only to LIST standard samples whose
# question names a release (they are probably version samples).
_NAMED_RELEASE_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+20\d{2}\b"
    r"|\b20\d{2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.I,
)


# ─── Parse-tree access ───────────────────────────────────────────────
#
# A deliberately small reader of the per-cell parse layout. The web
# module's req_tree is the full loader, but web depends on eval, so eval
# cannot import it. Only the per-cell layout matters here: a release
# exists only there.


def parse_cells(env_dir: Path) -> dict[str, list[str]]:
    """``{mno: [release, ...]}`` (oldest first) for every cell with at
    least one parsed tree under ``<env>/out/parse/<mno>/<release>/``."""
    root = Path(env_dir) / "out" / "parse"
    out: dict[str, list[str]] = {}
    if not root.is_dir():
        return out
    for mno_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        rels = [
            r.name for r in mno_dir.iterdir()
            if r.is_dir() and is_valid_release(r.name)
            and any(r.glob("*_tree.json"))
        ]
        if rels:
            out[mno_dir.name] = sorted(rels, key=release_order_key)
    return out


# Invisible characters that NFKC keeps but that never change meaning
# (field-found: a byte-order mark at the end of one line made an
# otherwise identical requirement compare "changed").
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))


def _norm(text: str) -> str:
    """Compatibility-normalise, drop invisible characters, collapse
    whitespace — so re-flowed or re-encoded text compares equal."""
    text = unicodedata.normalize("NFKC", text or "").translate(_INVISIBLE)
    return " ".join(text.split())


def cell_texts(
    env_dir: Path, mno: str, release: str
) -> dict[str, tuple[str, str]]:
    """``{req_id: (title, body)}`` for one cell, each normalised by
    ``_norm``. Title and body are kept apart so a title-only edit is its
    own class. The first occurrence of an id wins (the parser can emit
    duplicate rows)."""
    cell = Path(env_dir) / "out" / "parse" / mno / release
    texts: dict[str, tuple[str, str]] = {}
    for path in sorted(cell.glob("*_tree.json")):
        try:
            tree = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for req in tree.get("requirements", []):
            rid = str(req.get("req_id") or "")
            if rid and rid not in texts:
                texts[rid] = (_norm(str(req.get("title", "") or "")),
                              _norm(str(req.get("text", "") or "")))
    return texts


# ─── Text check ──────────────────────────────────────────────────────


@dataclass
class SampleCheck:
    sample_id: str
    unchanged: list[str] = field(default_factory=list)
    # Same body, different title (field-found: a title that dropped an
    # operator-name prefix). Carried over like unchanged, not flagged —
    # the body is what an answer is built from.
    title_only: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    # Stored id differs from the parse tree's only by case (field-found:
    # ids typed in another case read as "absent"). ``{stored: tree form}``;
    # --yes rewrites the stored id. Not flagged by itself — the entry is
    # then classified like any other.
    case: dict[str, str] = field(default_factory=dict)
    unverified: list[str] = field(default_factory=list)
    relative: bool = False      # relative version sample — always flagged
    from_releases: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return bool(self.relative or self.changed or self.absent
                    or self.unverified)


@dataclass
class CheckReport:
    mno: str
    to_release: str
    samples: list[SampleCheck] = field(default_factory=list)
    unattributed: int = 0       # standard entries of the MNO with no release

    def counts(self) -> dict[str, int]:
        c = {"unchanged": 0, "title_only": 0, "changed": 0, "absent": 0,
             "unverified": 0, "case": 0}
        for s in self.samples:
            for k in c:
                c[k] += len(getattr(s, k))
        c["flagged_samples"] = sum(1 for s in self.samples if s.flagged)
        c["relative_samples"] = sum(1 for s in self.samples if s.relative)
        c["unattributed_entries"] = self.unattributed
        return c


def text_check(
    samples: list[GoldenSample], env_dir: Path, mno: str, to_release: str,
    moving: bool = True,
) -> CheckReport:
    """Classify every entry of ``mno`` that moving its ``N`` to
    ``to_release`` affects. Read-only; ``apply_check`` writes.
    ``moving=False`` (first map, ``N`` not moving) leaves relative
    version samples alone — their binding does not change."""
    report = CheckReport(mno=mno, to_release=to_release)
    new_texts = cell_texts(env_dir, mno, to_release)
    new_ids = {rid.casefold(): rid for rid in new_texts}
    old_cache: dict[str, dict[str, tuple[str, str]]] = {}
    old_ids: dict[str, dict[str, str]] = {}
    to_key = release_order_key(to_release)
    for s in samples:
        entries = [e for e in s.ground_truth if e.mno == mno]
        if not entries:
            continue
        chk = SampleCheck(sample_id=s.sample_id)
        if s.kind == KIND_VERSION:
            if moving and any(is_relative_release(e.release) for e in entries):
                chk.relative = True
                report.samples.append(chk)
            continue            # absolute version samples are pinned
        for e in entries:
            if not e.release:
                report.unattributed += 1
                continue
            old_key = release_order_key(e.release)
            if old_key is None or to_key is None:
                continue
            if old_key >= to_key:
                # Already on (or past) the new release: no text to
                # compare, but a mis-cased id is still repaired.
                canon = new_ids.get(e.req_id.casefold())
                if old_key == to_key and canon and canon != e.req_id:
                    chk.case[e.req_id] = canon
                continue
            if e.release not in old_cache:
                old_cache[e.release] = cell_texts(env_dir, mno, e.release)
                old_ids[e.release] = {
                    rid.casefold(): rid for rid in old_cache[e.release]}
            if e.release not in chk.from_releases:
                chk.from_releases.append(e.release)
            # Look ids up by the tree's own casing (m3 matches either way;
            # the stored form is repaired on --yes).
            canon = (new_ids.get(e.req_id.casefold())
                     or old_ids[e.release].get(e.req_id.casefold()))
            if canon and canon != e.req_id:
                chk.case[e.req_id] = canon
            rid = canon or e.req_id
            old = old_cache[e.release].get(rid)
            new = new_texts.get(rid)
            if new is None:
                chk.absent.append(e.req_id)
            elif old is None:
                chk.unverified.append(e.req_id)
            elif old == new:
                chk.unchanged.append(e.req_id)
            elif old[1] == new[1]:
                chk.title_only.append(e.req_id)
            else:
                chk.changed.append(e.req_id)
        if chk.from_releases or chk.relative or chk.case:
            report.samples.append(chk)
    return report


def apply_check(
    samples: list[GoldenSample], report: CheckReport, env_dir: Path,
    now: str,
) -> set[str]:
    """Write the check's outcome into the samples: move unchanged (and
    title-only) entries' stored release to the new ``N``; flag samples
    that need review. Returns the ids of the samples written — a set, so
    callers applying several MNOs' reports count each file once."""
    by_id = {s.sample_id: s for s in samples}
    written: set[str] = set()
    for chk in report.samples:
        s = by_id[chk.sample_id]
        moved = set(chk.unchanged) | set(chk.title_only)
        for e in s.ground_truth:
            if e.mno != report.mno:
                continue
            if e.req_id in moved:
                e.release = report.to_release
            if e.req_id in chk.case:
                e.req_id = chk.case[e.req_id]
        if chk.flagged:
            review = dict(s.golden_meta.get("review") or {})
            review[report.mno] = {
                "from": chk.from_releases,
                "to": report.to_release,
                "changed": chk.changed,
                "absent": chk.absent,
                "unverified": chk.unverified,
                "relative": chk.relative,
                "flagged_at": now,
            }
            s.golden_meta["review"] = review
        if moved or chk.flagged or chk.case:
            save_sample(env_dir, s)
            written.add(s.sample_id)
    return written


def print_check(report: CheckReport) -> None:
    for chk in report.samples:
        if not (chk.flagged or chk.unchanged or chk.title_only or chk.case):
            continue
        parts = [f"  {chk.sample_id}:"]
        if chk.relative:
            parts.append("relative version sample (re-curate)")
        for name in ("changed", "absent", "unverified"):
            ids = getattr(chk, name)
            if ids:
                parts.append(f"{name}={','.join(ids)}")
        if chk.unchanged:
            parts.append(f"unchanged={len(chk.unchanged)}")
        if chk.title_only:
            parts.append(f"title_only={','.join(chk.title_only)}")
        if chk.case:
            parts.append("case=" + ",".join(
                f"{a}->{b}" for a, b in sorted(chk.case.items())))
        parts.append("-> REVIEW" if chk.flagged else "-> carried over")
        print(" ".join(parts))
    c = report.counts()
    print(
        f"text check {report.mno} -> {report.to_release}: "
        + " ".join(f"{k}={v}" for k, v in c.items())
    )


# ─── Commands ────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _user(args) -> str:
    if args.by:
        return args.by
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 — no login name on some hosts
        return ""


def cmd_show(args, golden_dir: Path) -> int:
    rmap = load_release_map(golden_dir, args.version)
    if rmap is None:
        print(f"No release map in {golden_dir} — run `migrate` first")
        return 1
    print(f"release map v{rmap.version} "
          f"(updated {rmap.updated_at or '-'} by {rmap.updated_by or '-'})")
    for mno, rels in sorted(rmap.mnos.items()):
        older = ", ".join(reversed(rels[:-1]))
        print(f"  {mno}: N={rels[-1]}" + (f"  older: {older}" if older else ""))
    return 0


def _parse_n(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values or []:
        mno, sep, rel = v.partition("=")
        if not sep or not mno or not is_valid_release(rel):
            raise SystemExit(f"--n expects MNO=MMMYYYY, got {v!r}")
        out[mno] = rel
    return out


def cmd_migrate(args, golden_dir: Path) -> int:
    if load_release_map(golden_dir) is not None:
        print("A release map already exists — use `advance`")
        return 1
    cells = parse_cells(args.env_dir)
    overrides = _parse_n(args.n)
    mnos: dict[str, list[str]] = {}
    for mno, rels in cells.items():
        n = overrides.get(mno, rels[-1])
        if n not in rels:
            print(f"{mno}: --n {n} has no parse trees in {args.env_dir}")
            return 1
        mnos[mno] = [r for r in rels if release_order_key(r)
                     <= release_order_key(n)]
    missing = sorted(set(overrides) - set(mnos))
    if missing:
        print(f"--n names MNOs with no parse trees: {', '.join(missing)}")
        return 1
    if not mnos:
        print(f"No per-cell parse trees under {args.env_dir}/out/parse")
        return 1
    rmap = ReleaseMap(version=1, mnos=mnos, updated_at=_now(),
                      updated_by=_user(args))
    print("proposed release map v1:")
    for mno, rels in sorted(mnos.items()):
        print(f"  {mno}: N={rels[-1]}  ({len(rels)} release(s))")

    samples = load_samples(args.env_dir)
    named = [s.sample_id for s in samples if s.kind == KIND_STANDARD
             and _NAMED_RELEASE_RE.search(s.query)]
    if named:
        print("standard samples whose question names a release "
              "(probably version samples — expert to decide):")
        print("  " + ", ".join(named))
    reports = [text_check(samples, args.env_dir, mno, rels[-1], moving=False)
               for mno, rels in sorted(mnos.items())]
    for r in reports:
        print_check(r)
    if not args.yes:
        print("dry run — nothing written (re-run with --yes)")
        return 0
    save_release_map(golden_dir, rmap)
    now = _now()
    written: set[str] = set()
    for r in reports:
        written |= apply_check(samples, r, args.env_dir, now)
    print(f"wrote release map v1 and {len(written)} sample file(s)")
    return 0


def cmd_advance(args, golden_dir: Path) -> int:
    rmap = load_release_map(golden_dir)
    if rmap is None:
        print("No release map — run `migrate` first")
        return 1
    if not is_valid_release(args.release):
        print(f"--release {args.release!r} is not MMMYYYY")
        return 1
    current = rmap.current(args.mno)
    if current and release_order_key(args.release) <= release_order_key(current):
        print(f"{args.mno}: {args.release} is not newer than N={current}")
        return 1
    if args.release not in parse_cells(args.env_dir).get(args.mno, []):
        print(f"{args.mno} {args.release}: no parse trees in {args.env_dir} "
              "— the text check needs them")
        return 1
    mnos = {m: list(r) for m, r in rmap.mnos.items()}
    mnos.setdefault(args.mno, []).append(args.release)
    new_map = ReleaseMap(version=rmap.version + 1, mnos=mnos,
                         updated_at=_now(), updated_by=_user(args))
    print(f"{args.mno}: N {current or '-'} -> {args.release} "
          f"(map v{rmap.version} -> v{new_map.version})")
    samples = load_samples(args.env_dir)
    report = text_check(samples, args.env_dir, args.mno, args.release)
    print_check(report)
    if not args.yes:
        print("dry run — nothing written (re-run with --yes)")
        return 0
    save_release_map(golden_dir, new_map)
    written = apply_check(samples, report, args.env_dir, _now())
    print(f"wrote release map v{new_map.version} and {len(written)} "
          "sample file(s)")
    return 0


def cmd_review(args, golden_dir: Path) -> int:
    samples = load_samples(args.env_dir)
    if args.clear:
        s = next((x for x in samples if x.sample_id == args.clear), None)
        if s is None:
            print(f"no sample {args.clear}")
            return 1
        review = dict(s.golden_meta.get("review") or {})
        if not review:
            print(f"{s.sample_id} has no review flag")
            return 1
        if args.mno:
            if args.mno not in review:
                print(f"{s.sample_id} has no review flag for {args.mno}")
                return 1
            review.pop(args.mno)
        else:
            review = {}
        if review:
            s.golden_meta["review"] = review
        else:
            s.golden_meta.pop("review", None)
        save_sample(args.env_dir, s)
        print(f"cleared review flag on {s.sample_id}"
              + (f" for {args.mno}" if args.mno else ""))
        return 0
    flagged = [s for s in samples if s.golden_meta.get("review")]
    for s in flagged:
        for mno, r in sorted(s.golden_meta["review"].items()):
            what = ["relative"] if r.get("relative") else []
            what += [f"{k}={','.join(r[k])}" for k in
                     ("changed", "absent", "unverified") if r.get(k)]
            print(f"  {s.sample_id} {mno} {','.join(r.get('from') or [])}"
                  f" -> {r.get('to')}: {' '.join(what)}")
    print(f"{len(flagged)} sample(s) flagged for review")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Golden-set release map: show / migrate / advance / review"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--env-dir", required=True, type=Path,
                       help="env with out/parse/ (requirement texts) and "
                            "eval/golden/ (samples + release map)")

    p = sub.add_parser("show", help="print the release map")
    common(p)
    p.add_argument("--version", type=int, default=None)

    p = sub.add_parser("migrate", help="write release map v1 for an existing set")
    common(p)
    p.add_argument("--n", action="append", metavar="MNO=REL",
                   help="set an MNO's N (default: its latest parsed release)")
    p.add_argument("--by", default="")
    p.add_argument("--yes", action="store_true", help="write (default: dry run)")

    p = sub.add_parser("advance", help="move one MNO's N after ingestion + flip")
    common(p)
    p.add_argument("--mno", required=True)
    p.add_argument("--release", required=True)
    p.add_argument("--by", default="")
    p.add_argument("--yes", action="store_true", help="write (default: dry run)")

    p = sub.add_parser("review", help="list or clear review flags")
    common(p)
    p.add_argument("--clear", default="", metavar="SAMPLE_ID")
    p.add_argument("--mno", default="")

    args = parser.parse_args(argv)
    golden_dir = golden_root(args.env_dir)
    handlers = {"show": cmd_show, "migrate": cmd_migrate,
                "advance": cmd_advance, "review": cmd_review}
    try:
        return handlers[args.cmd](args, golden_dir)
    except GoldenEvalError as exc:
        print(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
