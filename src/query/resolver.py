"""MNO and release resolver (TDD 7.2).

Resolves which MNO(s) and release(s) the query targets,
applying defaults where unspecified.

Uses the knowledge graph to discover available MNOs and releases.
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from src.graph.schema import NodeType
from src.query.schema import (
    QueryIntent,
    QueryType,
    ScopedQuery,
    MNOScope,
)

logger = logging.getLogger(__name__)


class MNOReleaseResolver:
    """Resolves MNO and release scope from query intent + graph metadata."""

    def __init__(self, graph: nx.DiGraph) -> None:
        self._graph = graph
        self._available = self._discover_available()

    def resolve(self, intent: QueryIntent) -> ScopedQuery:
        """Resolve MNO/release scope.

        Resolution rules (from TDD 7.2):
        - MNO + release specified → use exactly
        - MNO only → default to latest release
        - No MNO → search across all (latest releases)
        - Two MNOs → comparison query
        - Two releases → version diff
        """
        scoped_mnos = []

        if intent.mnos:
            # User specified MNO(s)
            for mno in intent.mnos:
                mno_upper = mno.upper()
                if mno_upper not in self._available:
                    logger.warning(f"MNO '{mno_upper}' not in graph — skipping")
                    continue

                releases = self._available[mno_upper]
                if intent.releases:
                    # Match specified release
                    matched = self._match_release(intent.releases[0], releases)
                    scoped_mnos.append(MNOScope(mno=mno_upper, release=matched))
                else:
                    # Default to latest
                    scoped_mnos.append(
                        MNOScope(mno=mno_upper, release=releases[0])
                    )
        else:
            # No MNO specified — use all available, latest releases
            for mno, releases in self._available.items():
                scoped_mnos.append(MNOScope(mno=mno, release=releases[0]))

        if not scoped_mnos:
            # Fallback: use all available
            logger.warning("No MNO resolved — using all available")
            for mno, releases in self._available.items():
                scoped_mnos.append(MNOScope(mno=mno, release=releases[0]))

        scoped = ScopedQuery(intent=intent, scoped_mnos=scoped_mnos)

        logger.info(
            f"Resolved scope: {[(s.mno, s.release) for s in scoped_mnos]}"
        )
        return scoped

    def _discover_available(self) -> dict[str, list[str]]:
        """Discover available MNOs and their releases from the graph.

        Returns dict of mno -> [releases] sorted newest first.
        """
        available: dict[str, list[str]] = {}

        for nid, data in self._graph.nodes(data=True):
            if data.get("node_type") == NodeType.RELEASE.value:
                mno = data.get("mno", "")
                release = data.get("release", "")
                if mno and release:
                    available.setdefault(mno, []).append(release)

        # Sort releases (reverse so latest is first — simple string sort works
        # for our format "YYYY_mmm")
        for mno in available:
            available[mno].sort(reverse=True)

        logger.info(f"Available MNOs/releases: {available}")
        return available

    def _match_release(
        self, user_release: str, available_releases: list[str]
    ) -> str:
        """Match a user-specified release string to an available release."""
        user_lower = user_release.lower().strip()

        if user_lower in ("latest", "current"):
            return available_releases[0]

        # Try exact match first
        for rel in available_releases:
            if rel.lower() == user_lower:
                return rel

        # Try substring match (e.g., "feb" matches "2026_feb")
        for rel in available_releases:
            if user_lower in rel.lower():
                return rel

        # Try year + month extraction
        for rel in available_releases:
            rel_parts = rel.lower().replace("_", " ").split()
            if any(part in user_lower for part in rel_parts):
                return rel

        # Default to latest
        logger.warning(
            f"Could not match release '{user_release}' — defaulting to latest"
        )
        return available_releases[0]

    @property
    def available_mnos(self) -> dict[str, list[str]]:
        return dict(self._available)
