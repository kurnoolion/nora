"""Web UI configuration.

Loads settings from config/web.json with sensible defaults for
development. The path_mappings field translates Windows UNC paths
(used by team members) to Linux mount points on the server.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# core/src/web/config.py -> core/src/web/ -> core/src/ -> core/ -> <repo_root>
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "web.json"


@dataclass
class PathMapping:
    """Maps a Windows network path to a Linux mount point."""

    windows: str
    linux: str
    label: str


@dataclass
class WebConfig:
    """Web application configuration."""

    host: str = "0.0.0.0"
    port: int = 8000
    root_path: str = ""
    path_mappings: list[PathMapping] = field(default_factory=list)
    ollama_url: str = "http://localhost:11434"
    default_model: str = "gemma3:12b"
    db_path: str = "web/nora.db"

    @classmethod
    def from_dict(cls, data: dict) -> WebConfig:
        mappings = [
            PathMapping(**m) for m in data.get("path_mappings", [])
        ]
        return cls(
            host=data.get("host", cls.host),
            port=data.get("port", cls.port),
            root_path=data.get("root_path", cls.root_path),
            path_mappings=mappings,
            ollama_url=data.get("ollama_url", cls.ollama_url),
            default_model=data.get("default_model", cls.default_model),
            db_path=data.get("db_path", cls.db_path),
        )


def load_config(path: Path | None = None) -> WebConfig:
    """Load config from JSON file, falling back to defaults."""
    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.exists():
        logger.info("Loading web config from %s", config_path)
        with open(config_path) as f:
            return WebConfig.from_dict(json.load(f))
    logger.warning("Config file %s not found, using defaults", config_path)
    return WebConfig()
