"""Per-cell profile binding (D-DRAFT-7).

Maps a ``(mno, release)`` cell to the ``DocumentProfile`` used to parse it,
from a per-env manifest ``<env_dir>/profiles.json``::

    { "bindings": [ { "mno": "VZW-OA", "release": "*",
                      "profile": "customizations/profiles/bs_d7a2c81f.json" } ],
      "default": null }

Resolution precedence per cell: an explicit single-profile **override**
(``--profile``, applied to every cell — single-MNO back-compat) > exact
``(mno, release)`` > ``(mno, "*")`` > ``default`` > **fail-loud**
(``PIP-E003``). Profile paths are taken as-given (repo-relative, like the
``--profile`` CLI flag). MNO match is case-insensitive (cells are
upper-cased at ingest); release matches exactly, with ``"*"`` the per-MNO
wildcard.

Lives in ``env`` (not ``pipeline``) to avoid an import cycle — ``pipeline``
imports ``env``. Resolution operates on plain ``(mno, release)`` strings so
no ``Cell`` import is needed here; the profile stage passes
``cell.mno, cell.release``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PROFILE_BINDINGS_FILENAME = "profiles.json"


@dataclass(frozen=True)
class ProfileBinding:
    mno: str
    release: str  # "*" = any release of this MNO
    profile: str


class ProfileBindings:
    """Resolves `(mno, release)` → profile path with the D-DRAFT-7 precedence."""

    def __init__(
        self,
        bindings: Iterable[ProfileBinding],
        default: str | None = None,
        override: str | None = None,
    ):
        self._bindings = list(bindings)
        self._default = default
        self._override = override

    @property
    def override(self) -> str | None:
        return self._override

    def _match(self, mno: str, release: str) -> str | None:
        mu = (mno or "").upper()
        # exact (mno, release)
        for b in self._bindings:
            if b.mno.upper() == mu and b.release == release:
                return b.profile
        # (mno, "*")
        for b in self._bindings:
            if b.mno.upper() == mu and b.release == "*":
                return b.profile
        return None

    def resolve(self, mno: str, release: str) -> Path:
        """The profile path for a cell. Raises ``ValueError`` (PIP-E003) on miss."""
        if self._override:
            return Path(self._override)
        hit = self._match(mno, release)
        if hit is not None:
            return Path(hit)
        if self._default:
            return Path(self._default)
        raise ValueError(
            f"PIP-E003: no profile bound for {mno}/{release} — add a binding to "
            f"<env_dir>/{PROFILE_BINDINGS_FILENAME} (or pass --profile / set a "
            f"\"default\")."
        )

    def covers(self, mno: str, release: str) -> bool:
        if self._override or self._default:
            return True
        return self._match(mno, release) is not None

    def uncovered(self, cells: Iterable) -> list[tuple[str, str]]:
        """`(mno, release)` pairs with no binding. Accepts tuples or objects
        carrying ``.mno`` / ``.release`` (e.g. ``Cell``)."""
        out: list[tuple[str, str]] = []
        for c in cells:
            mno, release = (c.mno, c.release) if hasattr(c, "mno") else (c[0], c[1])
            if not self.covers(mno, release):
                out.append((mno, release))
        return out


def load_profile_bindings(env_dir, override: str | Path | None = None) -> ProfileBindings:
    """Load ``<env_dir>/profiles.json``.

    ``override`` (the ``--profile`` path) wins over the manifest for every
    cell. A missing manifest with no override yields empty bindings —
    ``resolve`` / ``covers`` then fail-loud per uncovered cell, so the
    profile stage can report exactly which cells need a binding.
    """
    if override:
        return ProfileBindings([], default=None, override=str(override))
    path = Path(env_dir) / PROFILE_BINDINGS_FILENAME
    if not path.exists():
        return ProfileBindings([], default=None, override=None)
    data = json.loads(path.read_text(encoding="utf-8"))
    bindings = [
        ProfileBinding(
            mno=b["mno"],
            release=b.get("release", "*"),
            profile=b["profile"],
        )
        for b in data.get("bindings", [])
    ]
    return ProfileBindings(bindings, default=data.get("default"), override=None)
