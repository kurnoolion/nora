"""The build manifest: which fingerprint each stage produced per cell
(strand stage-graph, design §5).

``<env_dir>/STAGES.json``::

    {
      "version": 1,
      "stages": {
        "parse": {
          "MNO-A/Feb2026": {"fp": "…", "at": "…", "code_version": "…",
                            "params": {…}, "inputs": {…}, "externals": {…}}
        },
        "graph": {"*": {…}}
      }
    }

A per-cell stage is keyed by ``<MNO>/<release>``; a global stage by ``*``.
An entry is written when a stage completes a cell cleanly and removed
when a run of that cell degrades, so a recorded fingerprint always
stands for a complete output. The file is rewritten atomically after
every change, so a hard kill keeps the progress so far.

The name is ``STAGES.json``, not ``MANIFEST.json``: the promoted snapshot
under the labels root already carries a ``MANIFEST.json`` with a
different shape.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core.src.pipeline.error_codes import PipelineError

MANIFEST_NAME = "STAGES.json"
MANIFEST_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BuildManifest:
    """Read-modify-write view of one build's ``STAGES.json``."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data = self._load()

    # -- io ------------------------------------------------------------------

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": MANIFEST_VERSION, "stages": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise PipelineError("PIP-E004", {"path": str(self.path), "reason": str(e)}) from e
        if not isinstance(data, dict) or not isinstance(data.get("stages"), dict):
            raise PipelineError("PIP-E004", {"path": str(self.path), "reason": "not a manifest"})
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    # -- reads ---------------------------------------------------------------

    def stages(self) -> list[str]:
        return sorted(self._data["stages"])

    def entry(self, stage: str, key: str) -> dict | None:
        return self._data["stages"].get(stage, {}).get(key)

    def get(self, stage: str, key: str) -> str | None:
        """The recorded fingerprint, or None."""
        e = self.entry(stage, key)
        return e["fp"] if e else None

    def fingerprints(self, stage: str) -> dict[str, str]:
        """``{key: fp}`` for every recorded output of `stage`."""
        return {k: v["fp"] for k, v in sorted(self._data["stages"].get(stage, {}).items())}

    # -- writes --------------------------------------------------------------

    def record(self, stage: str, key: str, fp: str, **provenance) -> None:
        """Record `fp` for (`stage`, `key`) with its provenance and save."""
        entry = {"fp": fp, "at": _now_iso()}
        entry.update(provenance)
        self._data["stages"].setdefault(stage, {})[key] = entry
        self.save()

    def forget(self, stage: str, key: str) -> None:
        """Drop the entry for (`stage`, `key`) if present and save."""
        stage_entries = self._data["stages"].get(stage)
        if stage_entries and key in stage_entries:
            del stage_entries[key]
            if not stage_entries:
                del self._data["stages"][stage]
            self.save()
