"""Path translation between Windows UNC paths and Linux mount points."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from src.web.config import PathMapping


def _normalize_win(path: str) -> str:
    """Normalize a Windows path: forward slashes to backslashes, strip trailing."""
    return path.replace("/", "\\").rstrip("\\")


class PathMapper:
    """Translates paths between Windows UNC and Linux mount conventions."""

    def __init__(self, mappings: list[PathMapping]) -> None:
        self._mappings = mappings

    def to_linux(self, windows_path: str) -> Path | None:
        """Convert a Windows UNC path to a Linux path.

        Returns None if no mapping matches.
        """
        normalized = _normalize_win(windows_path)
        norm_lower = normalized.casefold()

        for m in self._mappings:
            prefix = _normalize_win(m.windows)
            prefix_lower = prefix.casefold()
            if norm_lower == prefix_lower:
                return Path(m.linux)
            if norm_lower.startswith(prefix_lower + "\\"):
                relative = normalized[len(prefix):]
                tail = relative.replace("\\", "/")
                return Path(m.linux + tail)
        return None

    def to_windows(self, linux_path: str | Path) -> str | None:
        """Convert a Linux path to a Windows UNC path for display.

        Returns None if no mapping matches.
        """
        posix = PurePosixPath(linux_path)
        posix_str = str(posix)

        for m in self._mappings:
            prefix = m.linux.rstrip("/")
            if posix_str == prefix or posix_str.startswith(prefix + "/"):
                relative = posix_str[len(prefix):]
                tail = relative.replace("/", "\\")
                return m.windows.rstrip("\\") + tail
        return None

    def list_roots(self) -> list[dict]:
        """Return available roots with both path representations and labels."""
        return [
            {"windows": m.windows, "linux": m.linux, "label": m.label}
            for m in self._mappings
        ]

    def resolve(self, path: str) -> Path:
        """Smart resolve: detect Windows paths and convert; otherwise treat as Linux.

        Always returns a Linux Path. Raises ValueError if the path is outside
        all configured roots.
        """
        if _looks_like_windows(path):
            result = self.to_linux(path)
            if result is None:
                raise ValueError(f"Windows path does not match any configured mapping: {path}")
            resolved = result.resolve()
        else:
            resolved = Path(path).resolve()

        if not self.is_within_roots(resolved):
            raise ValueError(f"Path is outside all configured roots: {path}")
        return resolved

    def is_within_roots(self, linux_path: Path) -> bool:
        """Security check: ensure the resolved path is within a configured root."""
        resolved = linux_path.resolve()
        for m in self._mappings:
            root = Path(m.linux).resolve()
            if resolved == root or _is_subpath(resolved, root):
                return True
        return False


def _is_subpath(path: Path, parent: Path) -> bool:
    """Return True if *path* is strictly under *parent*."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _looks_like_windows(path: str) -> bool:
    """Heuristic: starts with \\\\ or a drive letter like C:\\."""
    return path.startswith("\\\\") or (len(path) >= 3 and path[1] == ":" and path[2] in ("\\", "/"))
