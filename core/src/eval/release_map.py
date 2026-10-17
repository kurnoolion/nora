"""Golden-set release map — what ``N`` means for each MNO (strand
golden-eval, release-handling design).

The map is the only source of an MNO's current release ``N`` for the
golden eval. It is explicit and versioned rather than read from a
stack: every stack shares one golden set but flips at its own time, and
a stack stuck on an old label reports that label as its own latest — so
a check against the stack's own latest would pass it.

Layout (golden-set data, next to the samples — never in the repo,
NFR-8; a frozen copy of the set includes it):

    <golden>/release-map.json            current version
    <golden>/release-map.history.jsonl   every version, one JSON per line

Per MNO, releases are listed oldest first: ``N`` is the last item and
``N-k`` is ``k`` places before it.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from core.src.extraction.release_key import is_valid_release, release_order_key

from .golden import GoldenEvalError, relative_offset

MAP_FILE = "release-map.json"
HISTORY_FILE = "release-map.history.jsonl"


@dataclass
class ReleaseMap:
    version: int
    mnos: dict[str, list[str]] = field(default_factory=dict)
    updated_at: str = ""
    updated_by: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "mnos": {m: list(r) for m, r in sorted(self.mnos.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReleaseMap":
        rmap = cls(
            version=int(d.get("version", 0)),
            mnos={str(m): [str(r) for r in rels]
                  for m, rels in dict(d.get("mnos", {})).items()},
            updated_at=str(d.get("updated_at", "")),
            updated_by=str(d.get("updated_by", "")),
        )
        problems = rmap.validate()
        if problems:
            raise GoldenEvalError(
                "GEV-E001", "invalid release map: " + "; ".join(problems)
            )
        return rmap

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.version < 1:
            problems.append(f"version {self.version} < 1")
        for mno, rels in self.mnos.items():
            if not rels:
                problems.append(f"{mno}: empty release list")
                continue
            bad = [r for r in rels if not is_valid_release(r)]
            if bad:
                problems.append(f"{mno}: not MMMYYYY: {', '.join(bad)}")
                continue
            if len(set(rels)) != len(rels) or rels != sorted(
                rels, key=release_order_key
            ):
                problems.append(f"{mno}: releases must be unique, oldest first")
        return problems

    def current(self, mno: str) -> str | None:
        """The MNO's ``N``, or None when the map does not list it."""
        rels = self.mnos.get(mno)
        return rels[-1] if rels else None

    def resolve(self, mno: str, ref: str) -> str:
        """Absolute label for a relative reference. Raises GoldenEvalError
        GEV-E005 when the MNO is unlisted or ``N-k`` is out of range."""
        k = relative_offset(ref)
        rels = self.mnos.get(mno, [])
        if k >= len(rels):
            raise GoldenEvalError(
                "GEV-E005",
                f"relative release {ref} for {mno} is out of range "
                f"(release map v{self.version} lists {len(rels)})",
            )
        return rels[-1 - k]

    def digest(self) -> str:
        blob = json.dumps(self.to_dict()["mnos"], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()


def load_release_map(
    golden_dir: Path, version: int | None = None
) -> ReleaseMap | None:
    """The current map, or the pinned ``version`` from the history.

    Returns None when the set has no map yet (it is optional until the
    migration writes v1 — runs then fall back to map-less behaviour).
    Raises GoldenEvalError GEV-E001 on an unreadable map or a pinned
    version the history does not hold.
    """
    golden_dir = Path(golden_dir)
    if version is None:
        path = golden_dir / MAP_FILE
        if not path.is_file():
            return None
        try:
            return ReleaseMap.from_dict(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError) as exc:
            raise GoldenEvalError(
                "GEV-E001", f"unreadable release map {path.name}: {exc}"
            ) from exc
    hist = golden_dir / HISTORY_FILE
    if hist.is_file():
        for line in hist.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except ValueError as exc:
                raise GoldenEvalError(
                    "GEV-E001", f"unreadable line in {hist.name}: {exc}"
                ) from exc
            if int(d.get("version", 0)) == version:
                return ReleaseMap.from_dict(d)
    raise GoldenEvalError(
        "GEV-E001", f"release map version {version} not in {HISTORY_FILE}"
    )


def save_release_map(golden_dir: Path, rmap: ReleaseMap) -> Path:
    """Write the map as current (atomically) and append it to the history.
    Refuses an invalid map or a version that does not advance."""
    problems = rmap.validate()
    if problems:
        raise GoldenEvalError(
            "GEV-E001", "refusing to save release map: " + "; ".join(problems)
        )
    golden_dir = Path(golden_dir)
    prev = load_release_map(golden_dir)
    if prev is not None and rmap.version <= prev.version:
        raise GoldenEvalError(
            "GEV-E001",
            f"release map version {rmap.version} does not advance "
            f"v{prev.version}",
        )
    golden_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rmap.to_dict(), sort_keys=True)
    with (golden_dir / HISTORY_FILE).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    path = golden_dir / MAP_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rmap.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
