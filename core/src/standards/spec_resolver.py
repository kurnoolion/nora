"""3GPP spec version resolver and URL builder.

Maps (spec_number, release) pairs to download URLs on the 3GPP FTP
archive. Handles the 3GPP version encoding scheme:

  Release 8  → version 8.x.y → file code "8xy"
  Release 9  → version 9.x.y → file code "9xy"
  Release 10 → version 10.x.y → file code "axy"  (a=10)
  Release 11 → version 11.x.y → file code "bxy"  (b=11)
  ...
  Release 19 → version 19.x.y → file code "jxy"  (j=19)

Generic: works for any 3GPP spec number and release. No hardcoded
spec lists — all information derived from the spec number and release.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 3GPP archive base URL
_ARCHIVE_BASE = "https://www.3gpp.org/ftp/Specs/archive"

# Release → version prefix mapping
# Releases 0-9 use their digit directly; 10+ use letters a-z
_RELEASE_TO_PREFIX = {i: str(i) for i in range(10)}
_RELEASE_TO_PREFIX.update({
    10: "a", 11: "b", 12: "c", 13: "d", 14: "e",
    15: "f", 16: "g", 17: "h", 18: "i", 19: "j",
    20: "k", 21: "l",
})

# Reverse mapping
_PREFIX_TO_RELEASE = {v: k for k, v in _RELEASE_TO_PREFIX.items()}


@dataclass
class ResolvedSpec:
    """A resolved spec with its download URL and metadata."""
    spec_number: str        # e.g., "24.301"
    release_num: int        # e.g., 11
    version: str            # e.g., "11.7.0"  (empty until resolved)
    version_code: str       # e.g., "b70"
    url: str                # full download URL
    series: str             # e.g., "24"
    compact: str            # e.g., "24301"


def spec_to_series(spec_number: str) -> str:
    """Extract the series from a spec number: '24.301' → '24'."""
    return spec_number.split(".")[0]


def spec_to_compact(spec_number: str) -> str:
    """Remove dots from spec number: '24.301' → '24301'."""
    return spec_number.replace(".", "")


def release_to_prefix(release_num: int) -> str:
    """Map release number to version code prefix."""
    return _RELEASE_TO_PREFIX.get(release_num, "")


def prefix_to_release(prefix: str) -> int:
    """Map version code prefix back to release number."""
    return _PREFIX_TO_RELEASE.get(prefix, 0)


def version_to_code(version: str) -> str:
    """Convert version string to 3GPP file code.

    '11.7.0' → 'b70'  (11→b, 7→7, 0→0)
    '8.10.0' → '8a0'  (8→8, 10→a, 0→0)
    """
    parts = version.split(".")
    if len(parts) < 3:
        return ""

    major = int(parts[0])
    minor = int(parts[1])
    patch = int(parts[2])

    prefix = release_to_prefix(major)
    if not prefix:
        return ""

    # Minor and patch: 0-9 → digit, 10+ → letter
    def _encode(n: int) -> str:
        if n < 10:
            return str(n)
        return chr(ord("a") + n - 10)

    return f"{prefix}{_encode(minor)}{_encode(patch)}"


def code_to_version(code: str) -> str:
    """Convert 3GPP file code to version string.

    'b70' → '11.7.0'
    '8a0' → '8.10.0'
    """
    if len(code) != 3:
        return ""

    prefix = code[0]
    major = _PREFIX_TO_RELEASE.get(prefix, -1)
    if major < 0:
        return ""

    def _decode(c: str) -> int:
        if c.isdigit():
            return int(c)
        return ord(c) - ord("a") + 10

    minor = _decode(code[1])
    patch = _decode(code[2])
    return f"{major}.{minor}.{patch}"


def build_url(spec_number: str, version_code: str) -> str:
    """Build 3GPP FTP download URL for a spec version.

    Returns URL like:
    https://www.3gpp.org/ftp/Specs/archive/24_series/24.301/24301-b70.zip
    """
    series = spec_to_series(spec_number)
    compact = spec_to_compact(spec_number)
    return (
        f"{_ARCHIVE_BASE}/{series}_series/{spec_number}/"
        f"{compact}-{version_code}.zip"
    )


def build_candidate_urls(
    spec_number: str, release_num: int, max_minor: int = 20
) -> list[ResolvedSpec]:
    """Build candidate download URLs for a spec+release.

    Tries version codes from highest minor version downward,
    so the first successful download gets the latest version.

    Returns a list of ResolvedSpec with URLs to try.
    """
    prefix = release_to_prefix(release_num)
    if not prefix:
        logger.warning(f"No version prefix for release {release_num}")
        return []

    series = spec_to_series(spec_number)
    compact = spec_to_compact(spec_number)
    candidates = []

    for minor in range(max_minor, -1, -1):
        code = f"{prefix}{_encode_digit(minor)}0"
        version = f"{release_num}.{minor}.0"
        url = build_url(spec_number, code)
        candidates.append(ResolvedSpec(
            spec_number=spec_number,
            release_num=release_num,
            version=version,
            version_code=code,
            url=url,
            series=series,
            compact=compact,
        ))

    return candidates


def _encode_digit(n: int) -> str:
    """Encode a single digit/number for version code."""
    if n < 10:
        return str(n)
    return chr(ord("a") + n - 10)


class SpecResolver:
    """Resolve spec+release to download URLs.

    Optionally probes the 3GPP FTP archive directory to find
    available versions, falling back to candidate enumeration.
    """

    def __init__(self, cache_listings: bool = True):
        self._listing_cache: dict[str, set[str]] = {}
        self._cache_listings = cache_listings

    def resolve(
        self, spec_number: str, release_num: int
    ) -> ResolvedSpec | None:
        """Resolve a spec+release to a download URL.

        Tries to find the latest available version by probing
        the FTP directory listing. Falls back to candidate URLs.
        """
        # Try directory listing first
        available = self._get_available_versions(spec_number)
        if available:
            return self._find_best_version(
                spec_number, release_num, available
            )

        # Fall back to candidates (caller will try downloading each)
        candidates = build_candidate_urls(spec_number, release_num)
        return candidates[0] if candidates else None

    def resolve_candidates(
        self, spec_number: str, release_num: int
    ) -> list[ResolvedSpec]:
        """Get all candidate URLs for a spec+release.

        If directory listing is available, only returns known versions.
        Otherwise returns candidate URLs to try.
        """
        available = self._get_available_versions(spec_number)
        if available:
            return self._filter_by_release(
                spec_number, release_num, available
            )
        return build_candidate_urls(spec_number, release_num)

    def _get_available_versions(self, spec_number: str) -> set[str]:
        """Get available version codes from the 3GPP FTP directory."""
        if spec_number in self._listing_cache:
            return self._listing_cache[spec_number]

        try:
            import requests
            series = spec_to_series(spec_number)
            compact = spec_to_compact(spec_number)
            url = f"{_ARCHIVE_BASE}/{series}_series/{spec_number}/"
            resp = requests.get(
                url, timeout=15,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if resp.status_code != 200:
                return set()

            # Parse zip file names from directory listing
            pattern = re.compile(
                rf"{re.escape(compact)}-([a-z0-9]{{3}})\.zip"
            )
            codes = set(pattern.findall(resp.text))

            if self._cache_listings:
                self._listing_cache[spec_number] = codes

            logger.debug(
                f"Found {len(codes)} versions for TS {spec_number}"
            )
            return codes

        except Exception as e:
            logger.debug(f"Failed to list versions for {spec_number}: {e}")
            return set()

    def _find_best_version(
        self,
        spec_number: str,
        release_num: int,
        available: set[str],
    ) -> ResolvedSpec | None:
        """Find the latest version for a release from available codes."""
        candidates = self._filter_by_release(
            spec_number, release_num, available
        )
        return candidates[0] if candidates else None

    def _filter_by_release(
        self,
        spec_number: str,
        release_num: int,
        available: set[str],
    ) -> list[ResolvedSpec]:
        """Filter and sort available codes for a specific release."""
        prefix = release_to_prefix(release_num)
        if not prefix:
            return []

        series = spec_to_series(spec_number)
        compact = spec_to_compact(spec_number)

        matching = sorted(
            [c for c in available if c.startswith(prefix)],
            reverse=True,  # Latest version first
        )

        results = []
        for code in matching:
            version = code_to_version(code)
            url = build_url(spec_number, code)
            results.append(ResolvedSpec(
                spec_number=spec_number,
                release_num=release_num,
                version=version,
                version_code=code,
                url=url,
                series=series,
                compact=compact,
            ))
        return results
