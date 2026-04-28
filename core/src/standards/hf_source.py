"""HuggingFace `GSMA/3GPP` dataset as a 3GPP spec source.

Public, no-auth dataset mirroring the 3GPP FTP archive plus markdown
conversions. We consume only the DOCX side (`original/Rel-{N}/{NN}_series/`)
so that the existing `SpecParser` works unchanged.

Layout (DOCX side):
    original/Rel-{release}/{series}_series/{compact}-{version_code}.docx

For TS 24.301 Rel-19 v0.0 → `original/Rel-19/24_series/24301-j00.docx`.

Version discovery: the HF tree API
(`https://huggingface.co/api/datasets/GSMA/3GPP/tree/main/<path>`) returns
JSON file listings. We pick the latest available version code matching the
requested release prefix (j-prefix for Rel-19, k-prefix for Rel-20, …).

No `huggingface_hub` dependency — stdlib `urllib` only, matching the
`OllamaProvider` pattern so the module installs cleanly on offline /
locked-down hosts.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from core.src.standards.spec_resolver import (
    release_to_prefix,
    spec_to_compact,
    spec_to_series,
)

logger = logging.getLogger(__name__)

_DEFAULT_DATASET_BASE = "https://huggingface.co/datasets/GSMA/3GPP"
_DEFAULT_API_BASE = "https://huggingface.co/api/datasets/GSMA/3GPP/tree/main"
_DEFAULT_TIMEOUT = 60


class HuggingFaceSource:
    """Download 3GPP spec DOCX files from the GSMA/3GPP HuggingFace dataset."""

    def __init__(
        self,
        dataset_base: str = _DEFAULT_DATASET_BASE,
        api_base: str = _DEFAULT_API_BASE,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._dataset_base = dataset_base.rstrip("/")
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout
        # Cache directory listings so a 100-spec run hits the tree API once
        # per series-release, not 100 times.
        self._listing_cache: dict[str, list[str]] = {}

    def download(
        self, spec_number: str, release_num: int, dest_dir: Path
    ) -> Path | None:
        """Download a DOCX for `(spec_number, release_num)` into `dest_dir`.

        Returns the local path to the .docx, or None on failure. If a matching
        DOCX already exists in `dest_dir`, it is returned without a network
        round-trip.
        """
        compact = spec_to_compact(spec_number)
        series = spec_to_series(spec_number)
        prefix = release_to_prefix(release_num)
        if not prefix:
            logger.warning(
                f"HF: no version prefix for release {release_num} "
                f"(supported: 0-21)"
            )
            return None

        dir_path = f"original/Rel-{release_num}/{series}_series"
        files = self._list_directory(dir_path)
        if not files:
            logger.warning(
                f"HF: no files at {dir_path} for TS {spec_number} Rel-{release_num}"
            )
            return None

        latest = self._pick_latest_docx(files, compact, prefix)
        if not latest:
            logger.warning(
                f"HF: no DOCX matching TS {spec_number} (compact={compact}) "
                f"prefix={prefix!r} at {dir_path}"
            )
            return None

        dest_path = dest_dir / latest
        if dest_path.exists():
            logger.info(
                f"HF: TS {spec_number} Rel-{release_num} already at {dest_path.name}"
            )
            return dest_path

        url = f"{self._dataset_base}/resolve/main/{dir_path}/{latest}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not self._download_file(url, dest_path):
            return None

        logger.info(
            f"HF: TS {spec_number} Rel-{release_num} → {dest_path.name}"
        )
        return dest_path

    def _list_directory(self, path: str) -> list[str]:
        """Return filenames at the HF tree path. Empty list on error."""
        if path in self._listing_cache:
            return self._listing_cache[path]

        url = f"{self._api_base}/{path}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "NORA/1.0"}
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            logger.debug(f"HF tree API failed for {url}: {e}")
            return []
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"HF tree API parse failed for {url}: {e}")
            return []

        if not isinstance(data, list):
            return []

        files = [
            entry["path"].rsplit("/", 1)[-1]
            for entry in data
            if isinstance(entry, dict)
            and entry.get("type") == "file"
            and "path" in entry
        ]
        self._listing_cache[path] = files
        return files

    @staticmethod
    def _pick_latest_docx(
        filenames: list[str], compact: str, prefix: str
    ) -> str | None:
        """Pick the latest version DOCX matching `{compact}-{prefix}*.docx`.

        Within a release, lexicographic sort on the 3-char version code yields
        the latest minor.patch (3GPP encoding is monotonic over the digit/letter
        series 0-9, a-z used in `code_to_version`).
        """
        prefix_lower = prefix.lower()
        compact_dash = f"{compact}-".lower()
        matches = [
            f for f in filenames
            if f.lower().startswith(compact_dash)
            and f.lower().endswith(".docx")
            and len(f) > len(compact_dash)
            and f[len(compact_dash)].lower() == prefix_lower
        ]
        if not matches:
            return None
        # Sort by the version code (the chars between "compact-" and ".docx").
        # Lexicographic order on lower-cased codes is equivalent to release
        # ordering within a single release prefix.
        return max(matches, key=lambda fn: fn[len(compact_dash):-5].lower())

    def _download_file(self, url: str, dest: Path) -> bool:
        """Stream-download a URL to `dest`. Cleans up partial files on failure."""
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "NORA/1.0"}
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                if getattr(resp, "status", 200) >= 400:
                    return False
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
        except urllib.error.URLError as e:
            logger.debug(f"HF download failed for {url}: {e}")
            if dest.exists():
                dest.unlink()
            return False
        except Exception as e:
            logger.debug(f"HF download error for {url}: {e}")
            if dest.exists():
                dest.unlink()
            return False

        size_kb = dest.stat().st_size / 1024
        logger.debug(f"HF: downloaded {dest.name} ({size_kb:.0f} KB)")
        return True
