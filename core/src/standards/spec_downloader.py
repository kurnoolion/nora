"""3GPP spec downloader with local caching.

Downloads spec ZIP files from the 3GPP FTP archive, extracts the
DOC/DOCX content, and caches locally under a structured directory.

Cache structure:
    data/standards/TS_{spec}/Rel-{N}/
        {compact}-{version_code}.zip       — original archive
        {compact}-{version_code}.doc[x]    — extracted spec document

Also supports manual placement: if a DOC/DOCX file already exists
in the cache directory, the downloader skips the download.

DOC→DOCX conversion: older 3GPP specs are in .doc format. If
LibreOffice is available, .doc files are automatically converted
to .docx for parsing with python-docx.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import zipfile
from pathlib import Path

from src.standards.spec_resolver import ResolvedSpec, SpecResolver

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("data/standards")


class SpecDownloader:
    """Download and cache 3GPP specification documents."""

    def __init__(
        self,
        cache_dir: Path = _DEFAULT_CACHE_DIR,
        resolver: SpecResolver | None = None,
    ):
        self._cache_dir = cache_dir
        self._resolver = resolver or SpecResolver()

    def download(
        self, spec_number: str, release_num: int
    ) -> Path | None:
        """Download a spec for the given release.

        Returns the path to the extracted DOC/DOCX file, or None on failure.
        Uses cache if available, downloads from 3GPP FTP otherwise.
        """
        # Check cache first
        cached = self._find_cached(spec_number, release_num)
        if cached:
            logger.info(
                f"TS {spec_number} Rel-{release_num}: using cached {cached.name}"
            )
            return cached

        # Resolve the best version
        resolved = self._resolver.resolve(spec_number, release_num)
        if not resolved:
            logger.warning(
                f"TS {spec_number} Rel-{release_num}: no version found"
            )
            return None

        # Download
        spec_dir = self._spec_dir(spec_number, release_num)
        spec_dir.mkdir(parents=True, exist_ok=True)

        zip_path = spec_dir / f"{resolved.compact}-{resolved.version_code}.zip"
        if not self._download_file(resolved.url, zip_path):
            # Try a few more candidates
            candidates = self._resolver.resolve_candidates(
                spec_number, release_num
            )
            downloaded = False
            for cand in candidates[1:5]:  # Skip first (already tried)
                zip_path = spec_dir / f"{cand.compact}-{cand.version_code}.zip"
                if self._download_file(cand.url, zip_path):
                    resolved = cand
                    downloaded = True
                    break
            if not downloaded:
                logger.warning(
                    f"TS {spec_number} Rel-{release_num}: all downloads failed"
                )
                return None

        # Extract DOC/DOCX from ZIP
        doc_path = self._extract_doc(zip_path, spec_dir)
        if doc_path:
            logger.info(
                f"TS {spec_number} Rel-{release_num}: "
                f"v{resolved.version} → {doc_path.name}"
            )
        return doc_path

    def _spec_dir(self, spec_number: str, release_num: int) -> Path:
        """Cache directory for a spec+release."""
        return self._cache_dir / f"TS_{spec_number}" / f"Rel-{release_num}"

    def _find_cached(
        self, spec_number: str, release_num: int
    ) -> Path | None:
        """Check if a DOC/DOCX already exists in the cache."""
        spec_dir = self._spec_dir(spec_number, release_num)
        if not spec_dir.exists():
            return None

        # Look for DOCX files first (preferred), then DOC
        for ext in (".docx", ".doc"):
            files = list(spec_dir.glob(f"*{ext}"))
            if files:
                path = sorted(files)[-1]  # Latest by name
                # Convert .doc → .docx if needed
                if path.suffix.lower() == ".doc":
                    converted = self._convert_doc_to_docx(path)
                    if converted:
                        return converted
                return path
        return None

    def _download_file(self, url: str, dest: Path) -> bool:
        """Download a file from URL to local path."""
        if dest.exists():
            return True

        try:
            import requests
            logger.debug(f"Downloading {url}")
            resp = requests.get(
                url, timeout=60, stream=True,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code != 200:
                logger.debug(
                    f"HTTP {resp.status_code} for {url}"
                )
                return False

            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            size_kb = dest.stat().st_size / 1024
            logger.debug(f"Downloaded {dest.name} ({size_kb:.0f} KB)")
            return True

        except Exception as e:
            logger.debug(f"Download failed for {url}: {e}")
            if dest.exists():
                dest.unlink()
            return False

    def _extract_doc(self, zip_path: Path, dest_dir: Path) -> Path | None:
        """Extract the DOC/DOCX file from a 3GPP spec ZIP archive."""
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Find DOC/DOCX files in the archive
                doc_files = [
                    n for n in zf.namelist()
                    if n.lower().endswith((".doc", ".docx"))
                    and not n.startswith("__MACOSX")
                    and not n.startswith(".")
                ]

                if not doc_files:
                    logger.warning(
                        f"No DOC/DOCX found in {zip_path.name}"
                    )
                    return None

                # Prefer DOCX over DOC
                docx_files = [
                    f for f in doc_files if f.lower().endswith(".docx")
                ]
                target = docx_files[0] if docx_files else doc_files[0]

                # Extract to dest_dir (flatten path)
                target_name = Path(target).name
                dest_path = dest_dir / target_name

                if not dest_path.exists():
                    data = zf.read(target)
                    with open(dest_path, "wb") as f:
                        f.write(data)

                # Convert .doc → .docx if needed
                if dest_path.suffix.lower() == ".doc":
                    converted = self._convert_doc_to_docx(dest_path)
                    if converted:
                        return converted

                return dest_path

        except zipfile.BadZipFile:
            logger.warning(f"Bad ZIP file: {zip_path.name}")
            return None
        except Exception as e:
            logger.warning(f"Failed to extract {zip_path.name}: {e}")
            return None

    @staticmethod
    def _convert_doc_to_docx(doc_path: Path) -> Path | None:
        """Convert a .doc file to .docx using LibreOffice headless.

        Returns the path to the .docx file, or None if conversion fails.
        The original .doc file is kept.
        """
        docx_path = doc_path.with_suffix(".docx")
        if docx_path.exists():
            return docx_path

        lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
        if not lo_bin:
            logger.warning(
                "LibreOffice not found — cannot convert .doc to .docx. "
                "Install LibreOffice or manually convert the file."
            )
            return None

        try:
            result = subprocess.run(
                [
                    lo_bin, "--headless",
                    "--convert-to", "docx",
                    str(doc_path),
                    "--outdir", str(doc_path.parent),
                ],
                capture_output=True, text=True, timeout=120,
            )
            if docx_path.exists():
                logger.debug(f"Converted {doc_path.name} → {docx_path.name}")
                return docx_path
            else:
                logger.warning(
                    f"LibreOffice conversion produced no output: "
                    f"{result.stderr[:200]}"
                )
                return None
        except subprocess.TimeoutExpired:
            logger.warning(f"LibreOffice conversion timed out for {doc_path.name}")
            return None
        except Exception as e:
            logger.warning(f"DOC→DOCX conversion failed: {e}")
            return None
