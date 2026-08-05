"""Golden eval set schema + persistence (FR-38, strand golden-eval).

Schema owner for ``<env_dir>/eval/golden/``. The web Eval Studio and the
golden runner both read and write samples exclusively through this module —
there is no parallel serialization path.

Layout (all proprietary content — never in the repo, per NFR-8):
    <env_dir>/eval/golden/samples/<sample_id>.json   one file per sample
    <env_dir>/eval/golden/runs/<run_id>/             run artifacts (runner)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STATUS_DRAFT = "draft"
STATUS_STAGE1_READY = "stage1-ready"
STATUS_GOLDEN_READY = "golden-ready"
STATUSES = (STATUS_DRAFT, STATUS_STAGE1_READY, STATUS_GOLDEN_READY)

_SAMPLE_ID_RE = re.compile(r"^gs-\d{4,}$")


class GoldenEvalError(Exception):
    """Structured golden-eval failure carrying a stable GEV- code.

    Codes are registered in ``core/src/pipeline/error_codes.py`` (the
    human-readable catalog). Defined here rather than imported because
    pipeline depends on eval — importing the other way would be a cycle.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


@dataclass
class GroundTruthEntry:
    """One expected requirement. Qualifiers are optional: picker-sourced
    entries carry all three; direct-entry may be bare (req_id only).

    ``plan`` is authoring metadata — recall matching uses (req_id, mno,
    release) because retrieval results don't carry a plan field.
    """

    req_id: str
    mno: str = ""
    release: str = ""
    plan: str = ""
    # How the entry was added: "picker" | "direct" | "retrieval". The QC
    # template's independent-source check counts non-"retrieval" entries.
    source: str = "direct"

    def to_dict(self) -> dict:
        return {
            "req_id": self.req_id,
            "mno": self.mno,
            "release": self.release,
            "plan": self.plan,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GroundTruthEntry":
        return cls(
            req_id=str(d.get("req_id", "")),
            mno=str(d.get("mno", "")),
            release=str(d.get("release", "")),
            plan=str(d.get("plan", "")),
            source=str(d.get("source", "direct")),
        )


@dataclass
class GoldenSample:
    """An expert-curated eval sample (FR-38)."""

    sample_id: str
    query: str
    area: str = ""
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    ground_truth: list[GroundTruthEntry] = field(default_factory=list)
    golden_response: str | None = None
    golden_meta: dict = field(default_factory=dict)
    status: str = STATUS_DRAFT

    def to_dict(self) -> dict:
        return {
            "sample_id": self.sample_id,
            "query": self.query,
            "area": self.area,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ground_truth": [e.to_dict() for e in self.ground_truth],
            "golden_response": self.golden_response,
            "golden_meta": self.golden_meta,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GoldenSample":
        return cls(
            sample_id=str(d.get("sample_id", "")),
            query=str(d.get("query", "")),
            area=str(d.get("area", "")),
            created_by=str(d.get("created_by", "")),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
            ground_truth=[
                GroundTruthEntry.from_dict(e) for e in d.get("ground_truth", [])
            ],
            golden_response=d.get("golden_response"),
            golden_meta=dict(d.get("golden_meta", {})),
            status=str(d.get("status", STATUS_DRAFT)),
        )


def validate_sample(sample: GoldenSample) -> list[str]:
    """Schema-level validation. Returns a list of problems (empty = valid).

    Store-resolution checks (does each req_id exist in a cell?) need a
    vector store and live with the caller — Eval Studio at save time,
    runner at run time (GEV-E001).
    """
    problems: list[str] = []
    if not _SAMPLE_ID_RE.match(sample.sample_id):
        problems.append(
            f"sample_id {sample.sample_id!r} does not match 'gs-NNNN'"
        )
    if not sample.query.strip():
        problems.append("query is empty")
    if sample.status not in STATUSES:
        problems.append(
            f"status {sample.status!r} not one of {'/'.join(STATUSES)}"
        )
    for i, entry in enumerate(sample.ground_truth):
        if not entry.req_id.strip():
            problems.append(f"ground_truth[{i}] has empty req_id")
    seen: set[tuple] = set()
    for entry in sample.ground_truth:
        key = (entry.req_id, entry.mno, entry.release)
        if key in seen:
            problems.append(f"duplicate ground_truth entry {entry.req_id!r}")
        seen.add(key)
    if sample.status in (STATUS_STAGE1_READY, STATUS_GOLDEN_READY):
        if not sample.ground_truth:
            problems.append(f"status {sample.status} requires ground_truth")
    if sample.status == STATUS_GOLDEN_READY:
        if not (sample.golden_response or "").strip():
            problems.append("status golden-ready requires golden_response")
    return problems


# ─── Persistence ────────────────────────────────────────────────────


def golden_root(env_dir: Path) -> Path:
    return Path(env_dir) / "eval" / "golden"


def samples_dir(env_dir: Path) -> Path:
    return golden_root(env_dir) / "samples"


def runs_dir(env_dir: Path) -> Path:
    return golden_root(env_dir) / "runs"


def sample_path(env_dir: Path, sample_id: str) -> Path:
    return samples_dir(env_dir) / f"{sample_id}.json"


def load_sample(path: Path) -> GoldenSample:
    """Load one sample file. Raises GoldenEvalError (GEV-E001) on
    malformed JSON or schema violations — never returns a broken sample.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenEvalError(
            "GEV-E001", f"unreadable sample file {Path(path).name}: {exc}"
        ) from exc
    sample = GoldenSample.from_dict(data)
    problems = validate_sample(sample)
    if problems:
        raise GoldenEvalError(
            "GEV-E001",
            f"invalid sample {Path(path).name}: " + "; ".join(problems),
        )
    return sample


def load_samples(env_dir: Path) -> list[GoldenSample]:
    """Load every sample under <env_dir>/eval/golden/samples/, sorted by
    sample_id. Fail-loud: one malformed file aborts the load (GEV-E001) —
    a silently dropped sample would skew every recall aggregate.
    """
    sdir = samples_dir(env_dir)
    if not sdir.is_dir():
        return []
    out = [load_sample(p) for p in sorted(sdir.glob("gs-*.json"))]
    out.sort(key=lambda s: s.sample_id)
    return out


def save_sample(
    env_dir: Path, sample: GoldenSample, now: datetime | None = None
) -> Path:
    """Validate + write one sample atomically (tmp + rename). Stamps
    updated_at (and created_at when blank). Returns the written path.
    """
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    if not sample.created_at:
        sample.created_at = stamp
    sample.updated_at = stamp
    problems = validate_sample(sample)
    if problems:
        raise GoldenEvalError(
            "GEV-E001",
            f"refusing to save invalid sample {sample.sample_id}: "
            + "; ".join(problems),
        )
    sdir = samples_dir(env_dir)
    sdir.mkdir(parents=True, exist_ok=True)
    path = sample_path(env_dir, sample.sample_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(sample.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def next_sample_id(env_dir: Path) -> str:
    """Allocate the next sequential id (gs-0001, gs-0002, ...) from the
    files on disk. Callers holding several unsaved samples must save
    between allocations — this reads disk state only.
    """
    sdir = samples_dir(env_dir)
    highest = 0
    if sdir.is_dir():
        for p in sdir.glob("gs-*.json"):
            m = re.match(r"^gs-(\d+)$", p.stem)
            if m:
                highest = max(highest, int(m.group(1)))
    return f"gs-{highest + 1:04d}"
