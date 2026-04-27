"""Data models for correction FIX reports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FixReport:
    """Compact correction report — pasteable into chat.

    No proprietary document content. Only field names, regex patterns,
    feature IDs/names, keyword tokens, and counts.
    """

    env: str
    artifact: str  # "profile" | "taxonomy"
    lines: list[str] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def to_text(self) -> str:
        return "\n".join(self.lines)

    @property
    def is_empty(self) -> bool:
        return not any(self.summary.values())
