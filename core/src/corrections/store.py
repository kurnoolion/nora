"""CorrectionStore — per-environment correction file management.

Wraps the filesystem conventions (D-022):
    <env_dir>/out/profile/*.json            (pipeline output)
    <env_dir>/out/taxonomy/taxonomy.json
    <env_dir>/corrections/profile.json      (engineer-edited override)
    <env_dir>/corrections/taxonomy.json
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.src.env.config import EnvironmentConfig
from core.src.profiler.profile_schema import DocumentProfile
from core.src.taxonomy.schema import FeatureTaxonomy


class CorrectionStore:
    """File-layer helper for per-env profile/taxonomy corrections."""

    def __init__(self, env: EnvironmentConfig):
        self.env = env
        self.root = env.env_dir_path
        self.corrections_dir = env.corrections_path()

    # -- Paths --------------------------------------------------------------

    def profile_output_path(self) -> Path | None:
        out_dir = self.env.out_path("profile")
        if not out_dir.exists():
            return None
        candidates = sorted(out_dir.glob("*.json"))
        return candidates[0] if candidates else None

    def profile_correction_path(self) -> Path:
        return self.corrections_dir / "profile.json"

    def taxonomy_output_path(self) -> Path | None:
        p = self.env.out_path("taxonomy") / "taxonomy.json"
        return p if p.exists() else None

    def taxonomy_correction_path(self) -> Path:
        return self.corrections_dir / "taxonomy.json"

    # -- Status -------------------------------------------------------------

    def profile_status(self) -> dict:
        out = self.profile_output_path()
        cor = self.profile_correction_path()
        return {
            "has_output": out is not None,
            "output_path": str(out) if out else "",
            "has_correction": cor.exists(),
            "correction_path": str(cor),
        }

    def taxonomy_status(self) -> dict:
        out = self.taxonomy_output_path()
        cor = self.taxonomy_correction_path()
        return {
            "has_output": out is not None,
            "output_path": str(out) if out else "",
            "has_correction": cor.exists(),
            "correction_path": str(cor),
        }

    # -- Profile ------------------------------------------------------------

    def load_profile_output(self) -> DocumentProfile | None:
        p = self.profile_output_path()
        return DocumentProfile.load_json(p) if p else None

    def load_profile_correction(self) -> DocumentProfile | None:
        p = self.profile_correction_path()
        return DocumentProfile.load_json(p) if p.exists() else None

    def load_profile_effective(self) -> DocumentProfile | None:
        """Correction if present, else output."""
        return self.load_profile_correction() or self.load_profile_output()

    def save_profile_correction(self, profile: DocumentProfile) -> Path:
        p = self.profile_correction_path()
        profile.save_json(p)
        return p

    def start_profile_correction(self) -> Path:
        """Copy output profile into corrections/profile.json."""
        src = self.profile_output_path()
        if not src:
            raise FileNotFoundError(
                f"No profile output at {self.env.out_path('profile')}"
            )
        dst = self.profile_correction_path()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return dst

    def discard_profile_correction(self) -> bool:
        p = self.profile_correction_path()
        if p.exists():
            p.unlink()
            return True
        return False

    # -- Taxonomy -----------------------------------------------------------

    def load_taxonomy_output(self) -> FeatureTaxonomy | None:
        p = self.taxonomy_output_path()
        return FeatureTaxonomy.load_json(p) if p else None

    def load_taxonomy_correction(self) -> FeatureTaxonomy | None:
        p = self.taxonomy_correction_path()
        return FeatureTaxonomy.load_json(p) if p.exists() else None

    def load_taxonomy_effective(self) -> FeatureTaxonomy | None:
        return self.load_taxonomy_correction() or self.load_taxonomy_output()

    def save_taxonomy_correction(self, tax: FeatureTaxonomy) -> Path:
        p = self.taxonomy_correction_path()
        tax.save_json(p)
        return p

    def start_taxonomy_correction(self) -> Path:
        src = self.taxonomy_output_path()
        if not src:
            raise FileNotFoundError(
                f"No taxonomy output at {self.env.out_path('taxonomy')}"
            )
        dst = self.taxonomy_correction_path()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return dst

    def discard_taxonomy_correction(self) -> bool:
        p = self.taxonomy_correction_path()
        if p.exists():
            p.unlink()
            return True
        return False

    # -- Raw JSON (for form serialization) ----------------------------------

    def read_profile_correction_raw(self) -> dict | None:
        p = self.profile_correction_path()
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)

    def read_taxonomy_correction_raw(self) -> dict | None:
        p = self.taxonomy_correction_path()
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)

    def write_profile_correction_raw(self, data: dict) -> Path:
        p = self.profile_correction_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return p

    def write_taxonomy_correction_raw(self, data: dict) -> Path:
        p = self.taxonomy_correction_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return p
