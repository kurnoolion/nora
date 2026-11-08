"""The stage graph as data (strand stage-graph, D-DRAFT-1).

Every pipeline stage is a node with declared **inputs** (upstream stages),
**externals** (inputs that are not stage outputs: the documents, the bound
profile, the annotations, the overview prompt), the **parameters** that
enter its fingerprint, and whether it calls a model (an LLM or an
embedder). From those the stage's **tier** follows by one rule:

    a stage is corpus-side when it calls no model and every input of its
    is corpus-side; otherwise it is variant-side.

The corpus tier (``extract → profile → parse → resolve``, and ``standards``,
which is deterministic over them) is the chain every serving stack on a
host shares. Variant stages are the ones a recipe chooses and
parameterises. The rule, not a list, decides, so a stage that gains a
model call moves tiers by definition.

``STAGE_ORDER`` is the graph's topological order and equals
``env.config.STAGE_NAMES`` (a test pins that). The per-cell / global
partition is ``pipeline.cells``'; this module reads it.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.src.env.config import STAGE_NAMES
from core.src.pipeline.cells import PER_CELL_STAGES

#: The manifest key of a global (cross-cell) stage's one output.
GLOBAL_KEY = "*"

CORPUS = "corpus"
VARIANT = "variant"


@dataclass(frozen=True)
class StageSpec:
    """One stage's node in the graph."""

    name: str
    #: Upstream stages whose outputs this stage reads.
    inputs: tuple[str, ...] = ()
    #: Named external inputs (hashed by `pipeline.fingerprints`).
    externals: tuple[str, ...] = ()
    #: `PipelineContext` attributes that enter the fingerprint as parameters.
    params: tuple[str, ...] = ()
    #: True when the stage calls a language model or an embedder.
    uses_model: bool = False

    @property
    def per_cell(self) -> bool:
        return self.name in PER_CELL_STAGES


SPECS: tuple[StageSpec, ...] = (
    StageSpec("extract", externals=("documents",)),
    StageSpec("profile", externals=("profile",)),
    StageSpec("parse", inputs=("extract", "profile"), externals=("annotations",)),
    StageSpec("resolve", inputs=("parse",)),
    StageSpec("standards", inputs=("resolve",), params=("standards_source",)),
    StageSpec(
        "taxonomy", inputs=("parse",), externals=("overview_prompt",),
        params=("model_provider", "model_name", "llm_reasoning"), uses_model=True,
    ),
    StageSpec("graph", inputs=("parse", "resolve", "taxonomy", "standards")),
    StageSpec(
        "vectorstore", inputs=("parse", "taxonomy"),
        params=("embedding_provider", "embedding_model"), uses_model=True,
    ),
    StageSpec(
        "eval", inputs=("graph", "vectorstore"),
        params=("model_provider", "model_name"), uses_model=True,
    ),
)

STAGE_GRAPH: dict[str, StageSpec] = {s.name: s for s in SPECS}
STAGE_ORDER: tuple[str, ...] = tuple(s.name for s in SPECS)


def spec_of(stage: str) -> StageSpec:
    """The spec of `stage`; `KeyError` for an unknown name."""
    return STAGE_GRAPH[stage]


def tier_of(stage: str) -> str:
    """``"corpus"`` or ``"variant"``, by the tier rule."""
    spec = STAGE_GRAPH[stage]
    if spec.uses_model:
        return VARIANT
    return VARIANT if any(tier_of(i) == VARIANT for i in spec.inputs) else CORPUS


CORPUS_STAGES: frozenset[str] = frozenset(s for s in STAGE_ORDER if tier_of(s) == CORPUS)
VARIANT_STAGES: frozenset[str] = frozenset(s for s in STAGE_ORDER if tier_of(s) == VARIANT)
#: The shared chain, in order.
CORPUS_PREFIX: tuple[str, ...] = tuple(s for s in STAGE_ORDER if s in CORPUS_STAGES)


def validate_graph() -> list[str]:
    """Structural checks; an empty list means the graph is well formed.

    - the order equals `STAGE_NAMES` (one source of truth for stage names);
    - every input is a known stage that precedes its consumer;
    - a per-cell stage reads per-cell inputs only (its cell fingerprint
      must not depend on a global output).
    """
    problems: list[str] = []
    if list(STAGE_ORDER) != list(STAGE_NAMES):
        problems.append(f"STAGE_ORDER {list(STAGE_ORDER)} != STAGE_NAMES {list(STAGE_NAMES)}")
    seen: set[str] = set()
    for spec in SPECS:
        for inp in spec.inputs:
            if inp not in STAGE_GRAPH:
                problems.append(f"{spec.name}: unknown input {inp!r}")
            elif inp not in seen:
                problems.append(f"{spec.name}: input {inp!r} does not precede it")
            elif spec.per_cell and not STAGE_GRAPH[inp].per_cell:
                problems.append(f"{spec.name}: per-cell stage reads global input {inp!r}")
        seen.add(spec.name)
    return problems
