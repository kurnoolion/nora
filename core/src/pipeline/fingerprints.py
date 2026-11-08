"""Uniform stage fingerprints (strand stage-graph, D-DRAFT-2).

One rule for every stage::

    fp(stage, cell) = sha256(stage, code version, sorted params,
                             fp of each input stage for the cell,
                             fp of each external input)[:16]

The **code version** is the stage's own for a corpus-tier stage (a
``CODE_VERSION`` string the owning package exports and bumps when its
logic changes) and the image's ``NORA_CODE_VERSION`` for a variant-tier
stage. The **inputs** are read from the build manifest; an input with no
recorded output for the cell enters as ``"absent"``. A **global** stage
takes, for each per-cell input, the whole ``{cell: fp}`` map, so its
fingerprint changes when any cell changes or a cell is added.

Language-model outputs are not reproducible, so a fingerprint identifies
the *intent* of an output, not its bytes.

`Fingerprinter` binds the rule to a `PipelineContext` and its manifest;
stages ask it whether a cell is `unchanged` and `stamp` the cell when
they complete it cleanly.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from core.src.pipeline.cells import Cell
from core.src.pipeline.manifest import BuildManifest
from core.src.pipeline.stage_graph import CORPUS, GLOBAL_KEY, spec_of, tier_of

if TYPE_CHECKING:  # pragma: no cover
    from core.src.pipeline.runner import PipelineContext

FP_LEN = 16
#: An input stage with no recorded output for the cell.
ABSENT = "absent"
#: An external input that could not be resolved (the stage will fail loud).
UNRESOLVED = "unresolved"

VARIANT_CODE_VERSION_ENV = "NORA_CODE_VERSION"
DEFAULT_VARIANT_CODE_VERSION = "dev"

#: The package that owns each corpus stage's ``CODE_VERSION``.
CORPUS_VERSION_MODULES: dict[str, str] = {
    "extract": "core.src.extraction",
    "profile": "core.src.profiler",
    "parse": "core.src.parser",
    "resolve": "core.src.resolver",
    "standards": "core.src.standards",
}


# -- primitives ----------------------------------------------------------------

def canonical(obj) -> bytes:
    """Canonical JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()[:FP_LEN]


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:FP_LEN]


def hash_files(files: Iterable[Path], rel_to: Path | None = None) -> str:
    """Hash a file set: each file's path (relative to `rel_to`, else its
    name) and its bytes, in path order. Additions, removals and edits all
    change the result."""
    h = hashlib.sha256()
    keyed: list[tuple[str, Path]] = []
    for f in files:
        try:
            key = f.relative_to(rel_to).as_posix() if rel_to else f.name
        except ValueError:
            key = f.name
        keyed.append((key, f))
    for key, f in sorted(keyed):
        h.update(key.encode("utf-8"))
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()[:FP_LEN]


def stage_fingerprint(
    stage: str, code_version: str, params: dict, input_fps: dict, external_fps: dict,
) -> str:
    """The rule. `input_fps` values are strings (per-cell) or ``{cell: fp}``
    maps (a global stage over a per-cell input)."""
    return digest({
        "stage": stage,
        "code": code_version,
        "params": params,
        "inputs": input_fps,
        "externals": external_fps,
    })


def code_version(stage: str) -> str:
    """A corpus stage's own ``CODE_VERSION``; the image version otherwise."""
    if tier_of(stage) == CORPUS:
        module = importlib.import_module(CORPUS_VERSION_MODULES[stage])
        return str(module.CODE_VERSION)
    return os.getenv(VARIANT_CODE_VERSION_ENV, "").strip() or DEFAULT_VARIANT_CODE_VERSION


def cell_key(stage: str, cell: Cell | None) -> str:
    """The manifest key: ``<MNO>/<release>`` for a per-cell stage, ``*`` for
    a global one (or when no cell is given)."""
    if cell is None or not spec_of(stage).per_cell:
        return GLOBAL_KEY
    return f"{cell.mno}/{cell.release}"


# -- the binding to a build ---------------------------------------------------

class Fingerprinter:
    """Computes, compares and records stage fingerprints for one build."""

    def __init__(self, ctx: "PipelineContext", manifest: BuildManifest):
        self.ctx = ctx
        self.manifest = manifest

    # -- parts ---------------------------------------------------------------

    def params(self, stage: str) -> dict:
        return {p: str(getattr(self.ctx, p, "")) for p in spec_of(stage).params}

    def input_fps(self, stage: str, cell: Cell | None) -> dict:
        spec = spec_of(stage)
        out: dict = {}
        for inp in spec.inputs:
            if spec.per_cell:
                out[inp] = self.manifest.get(inp, cell_key(inp, cell)) or ABSENT
            elif spec_of(inp).per_cell:
                out[inp] = self.manifest.fingerprints(inp) or ABSENT
            else:
                out[inp] = self.manifest.get(inp, GLOBAL_KEY) or ABSENT
        return out

    def externals(self, stage: str, cell: Cell | None) -> dict:
        return {name: self._EXTERNALS[name](self, cell) for name in spec_of(stage).externals}

    # -- the rule, applied -------------------------------------------------

    def current(self, stage: str, cell: Cell | None = None) -> str:
        return stage_fingerprint(
            stage, code_version(stage), self.params(stage),
            self.input_fps(stage, cell), self.externals(stage, cell),
        )

    def recorded(self, stage: str, cell: Cell | None = None) -> str | None:
        return self.manifest.get(stage, cell_key(stage, cell))

    def unchanged(self, stage: str, cell: Cell | None = None, fp: str | None = None) -> bool:
        """True when the recorded fingerprint equals the current one and
        `--force` is off. Pass `fp` when the caller computed it already."""
        if self.ctx.force:
            return False
        recorded = self.recorded(stage, cell)
        if recorded is None:
            return False
        return recorded == (fp if fp is not None else self.current(stage, cell))

    def stamp(self, stage: str, cell: Cell | None = None, fp: str | None = None) -> str:
        """Record the current fingerprint (or `fp`) with its provenance."""
        fp = fp if fp is not None else self.current(stage, cell)
        self.manifest.record(
            stage, cell_key(stage, cell), fp,
            code_version=code_version(stage),
            params=self.params(stage),
            inputs=self.input_fps(stage, cell),
            externals=self.externals(stage, cell),
        )
        return fp

    def forget(self, stage: str, cell: Cell | None = None) -> None:
        self.manifest.forget(stage, cell_key(stage, cell))

    # -- external inputs ----------------------------------------------------

    def _env_dir(self) -> Path:
        return self.ctx.env_dir

    def cell_input_dir(self, cell: Cell) -> Path | None:
        """The cell's directory under the documents root (MNO dir matched
        case-insensitively; `enumerate_input_cells` upper-cases the MNO)."""
        root = self.ctx.documents_dir
        if not root.is_dir():
            return None
        for d in root.iterdir():
            if d.is_dir() and d.name.upper() == cell.mno.upper():
                p = d / cell.release
                if p.is_dir():
                    return p
        return None

    def _documents(self, cell: Cell | None) -> str:
        d = self.cell_input_dir(cell) if cell else None
        if d is None:
            return ABSENT
        return hash_files((f for f in d.rglob("*") if f.is_file()), rel_to=d)

    def _profile(self, cell: Cell | None) -> str:
        """Hash of the cell's bound profile after placeholder substitution,
        which is exactly what the profile stage materializes."""
        if cell is None:
            return ABSENT
        try:
            from core.src.env.profile_bindings import load_profile_bindings
            from core.src.profiler.profile_substitute import load_substituted_profile
            env_dir = self._env_dir()
            bindings = load_profile_bindings(env_dir, override=self.ctx.profile_override())
            path = Path(bindings.resolve(cell.mno, cell.release))
            return digest(load_substituted_profile(path, env_dir=env_dir).to_dict())
        except Exception:  # noqa: BLE001 — the stage reports the cause (PIP-E003 / PIP-E002)
            return UNRESOLVED

    def _annotations(self, cell: Cell | None) -> str:
        """The user annotations (D-061) of the cell's documents."""
        if cell is None:
            return ABSENT
        ann_dir = self._env_dir() / "annotations"
        if not ann_dir.is_dir():
            return ABSENT
        stems = [f.stem[:-len("_ir")] for f in self.ctx.stage_output("extract", cell).glob("*_ir.json")]
        files = [ann_dir / f"{s}_annotations.json" for s in stems]
        files = [f for f in files if f.is_file()]
        return hash_files(files, rel_to=ann_dir) if files else ABSENT

    def _overview_prompt(self, cell: Cell | None) -> str:
        """The MNO's corpus-overview prompt file the taxonomy extractor
        would use (highest version wins)."""
        if cell is None:
            return ABSENT
        from core.src.taxonomy.extractor import resolve_corpus_overview
        path = resolve_corpus_overview(os.getenv("NORA_TAXONOMY_OVERVIEW_DIR", "").strip() or None, cell.mno)
        if path is None:
            return ABSENT
        return hash_files([path], rel_to=path.parent)

    _EXTERNALS = {
        "documents": _documents,
        "profile": _profile,
        "annotations": _annotations,
        "overview_prompt": _overview_prompt,
    }
