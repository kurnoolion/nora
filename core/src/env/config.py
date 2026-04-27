"""Environment configuration for multi-user pipeline workflows.

An environment defines a workspace for a team member to run specific
pipeline stages against specific documents, with defined scope and objectives.

Usage:
    from src.env.config import EnvironmentConfig, PIPELINE_STAGES

    env = EnvironmentConfig(
        name="profiler-review",
        description="Verify profiler accuracy on new VZW docs",
        created_by="mohan",
        member="alice",
        document_root="/data/vzw-new-batch",
        stage_start="extract",
        stage_end="parse",
        mnos=["VZW"],
        releases=["Feb2026"],
        objectives=["Verify heading detection", "Check table extraction"],
    )
    env.save_json(Path("environments/profiler-review.json"))
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Pipeline stage registry — single source of truth for names and ordering
# ---------------------------------------------------------------------------

PIPELINE_STAGES: list[tuple[str, str]] = [
    ("extract", "Document content extraction"),
    ("profile", "Document profiling"),
    ("parse", "Structural parsing"),
    ("resolve", "Cross-reference resolution"),
    ("taxonomy", "Feature taxonomy extraction"),
    ("standards", "Standards ingestion"),
    ("graph", "Knowledge graph construction"),
    ("vectorstore", "Vector store construction"),
    ("eval", "Evaluation"),
]

STAGE_NAMES: list[str] = [s[0] for s in PIPELINE_STAGES]
STAGE_NUM: dict[str, int] = {name: i + 1 for i, (name, _) in enumerate(PIPELINE_STAGES)}
NUM_STAGE: dict[int, str] = {i + 1: name for i, (name, _) in enumerate(PIPELINE_STAGES)}
STAGE_DESC: dict[str, str] = {name: desc for name, desc in PIPELINE_STAGES}


def resolve_stage(value: str) -> str:
    """Convert a stage number or name to a canonical stage name."""
    if value.isdigit():
        num = int(value)
        if num not in NUM_STAGE:
            raise ValueError(
                f"Stage number {num} out of range (1-{len(PIPELINE_STAGES)})"
            )
        return NUM_STAGE[num]
    if value in STAGE_NUM:
        return value
    raise ValueError(
        f"Unknown stage '{value}'. Valid: {', '.join(STAGE_NAMES)} or 1-{len(PIPELINE_STAGES)}"
    )


# ---------------------------------------------------------------------------
# Document root directory layout
# ---------------------------------------------------------------------------

DOC_ROOT_DIRS = {
    "documents": "Source documents (PDFs, DOCx, XLS, etc.)",
    "corrections": "User-corrected artifacts (profile.json, taxonomy.json)",
    "eval": "User-supplied Q&A eval pairs (Excel)",
    "output": "Pipeline outputs (auto-created per stage)",
    "reports": "Pipeline reports (auto-created)",
}


# ---------------------------------------------------------------------------
# Environment config
# ---------------------------------------------------------------------------

@dataclass
class EnvironmentConfig:
    """Configuration for a pipeline environment."""

    name: str
    description: str
    created_by: str
    member: str
    document_root: str

    # Stages to run
    stage_start: str = "extract"
    stage_end: str = "eval"

    # Scope
    mnos: list[str] = field(default_factory=lambda: ["VZW"])
    releases: list[str] = field(default_factory=lambda: ["Feb2026"])
    doc_types: list[str] = field(default_factory=lambda: ["requirements"])

    # Objectives (human-readable)
    objectives: list[str] = field(default_factory=list)

    # Model config
    model_provider: str = "ollama"
    model_name: str = "auto"
    model_timeout: int = 600

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))

    # --- Serialization ---

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load_json(cls, path: Path) -> EnvironmentConfig:
        with open(path) as f:
            data = json.load(f)
        return cls(**data)

    # --- Validation ---

    def validate(self) -> list[str]:
        """Return list of validation errors (empty = valid)."""
        errors: list[str] = []
        if self.stage_start not in STAGE_NAMES:
            errors.append(f"Unknown start stage: {self.stage_start}")
        if self.stage_end not in STAGE_NAMES:
            errors.append(f"Unknown end stage: {self.stage_end}")
        if (
            self.stage_start in STAGE_NUM
            and self.stage_end in STAGE_NUM
            and STAGE_NUM[self.stage_start] > STAGE_NUM[self.stage_end]
        ):
            errors.append(
                f"Start stage '{self.stage_start}' ({STAGE_NUM[self.stage_start]}) "
                f"is after end stage '{self.stage_end}' ({STAGE_NUM[self.stage_end]})"
            )
        if not self.name:
            errors.append("Environment name is required")
        if not self.document_root:
            errors.append("document_root is required")
        if not self.mnos:
            errors.append("At least one MNO must be specified")
        if not self.releases:
            errors.append("At least one release must be specified")
        return errors

    # --- Derived paths ---

    @property
    def active_stages(self) -> list[str]:
        """Stage names that will run, in order."""
        start = STAGE_NUM.get(self.stage_start, 1) - 1
        end = STAGE_NUM.get(self.stage_end, len(PIPELINE_STAGES))
        return STAGE_NAMES[start:end]

    @property
    def doc_root(self) -> Path:
        return Path(self.document_root)

    def path(self, key: str) -> Path:
        """Get a standard subdirectory under document_root."""
        return self.doc_root / key

    def output_path(self, stage: str) -> Path:
        """Get output directory for a specific stage."""
        return self.doc_root / "output" / stage

    def correction_path(self, artifact: str) -> Path | None:
        """Get path to a correction file if it exists."""
        p = self.doc_root / "corrections" / artifact
        return p if p.exists() else None

    def init_directories(self) -> list[str]:
        """Create the standard directory structure. Returns created dirs."""
        created: list[str] = []
        for dirname in DOC_ROOT_DIRS:
            p = self.doc_root / dirname
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created.append(str(p))
        # Stage-specific output dirs
        for stage in self.active_stages:
            p = self.output_path(stage)
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created.append(str(p))
        return created
