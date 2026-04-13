"""Feature taxonomy consolidation (TDD 5.7, Step 2 — single MNO).

Merges per-document feature extractions into a unified taxonomy.
Deduplicates features by feature_id, tracks which plans contribute
to each feature, and builds the mno_coverage mapping.

Cross-MNO consolidation (TDD Step 2 full) uses the LLM to align
features across MNOs. That's deferred until we have multi-MNO data.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from src.taxonomy.schema import (
    DocumentFeatures,
    Feature,
    FeatureTaxonomy,
    TaxonomyFeature,
)

logger = logging.getLogger(__name__)


class TaxonomyConsolidator:
    """Merge per-document features into a unified taxonomy."""

    def consolidate(
        self,
        doc_features: list[DocumentFeatures],
    ) -> FeatureTaxonomy:
        """Consolidate features from multiple documents into a taxonomy."""
        # Group by feature_id across all documents
        feature_map: dict[str, TaxonomyFeature] = {}

        for doc in doc_features:
            self._merge_features(feature_map, doc, doc.primary_features, is_primary=True)
            self._merge_features(feature_map, doc, doc.referenced_features, is_primary=False)

        # Sort features: primary-in-most-docs first, then alphabetically
        features = sorted(
            feature_map.values(),
            key=lambda f: (-len(f.is_primary_in), f.name),
        )

        # Derive MNO from docs
        mnos = {d.mno for d in doc_features if d.mno}
        releases = {d.release for d in doc_features if d.release}

        taxonomy = FeatureTaxonomy(
            mno=", ".join(sorted(mnos)) if mnos else "",
            release=", ".join(sorted(releases)) if releases else "",
            features=features,
            source_documents=[d.plan_id for d in doc_features],
        )

        logger.info(
            f"Consolidated {len(doc_features)} documents into "
            f"{len(features)} features"
        )
        self._log_summary(taxonomy)
        return taxonomy

    @staticmethod
    def _merge_features(
        feature_map: dict[str, TaxonomyFeature],
        doc: DocumentFeatures,
        features: list[Feature],
        is_primary: bool,
    ) -> None:
        """Merge a list of features from one document into the taxonomy map."""
        for feat in features:
            fid = feat.feature_id
            if fid not in feature_map:
                feature_map[fid] = TaxonomyFeature(
                    feature_id=fid,
                    name=feat.name,
                    description=feat.description,
                    keywords=list(feat.keywords),
                )

            tf = feature_map[fid]

            # Track which plans this feature appears in
            if doc.plan_id not in tf.source_plans:
                tf.source_plans.append(doc.plan_id)

            if is_primary and doc.plan_id not in tf.is_primary_in:
                tf.is_primary_in.append(doc.plan_id)
            elif not is_primary and doc.plan_id not in tf.is_referenced_in:
                tf.is_referenced_in.append(doc.plan_id)

            # Build mno_coverage
            mno = doc.mno or "UNKNOWN"
            if mno not in tf.mno_coverage:
                tf.mno_coverage[mno] = []
            if doc.plan_id not in tf.mno_coverage[mno]:
                tf.mno_coverage[mno].append(doc.plan_id)

            # Merge keywords (deduplicate)
            existing = set(tf.keywords)
            for kw in feat.keywords:
                if kw not in existing:
                    tf.keywords.append(kw)
                    existing.add(kw)

    @staticmethod
    def _log_summary(taxonomy: FeatureTaxonomy) -> None:
        logger.info(f"\n--- Feature Taxonomy Summary ---")
        logger.info(f"  MNO: {taxonomy.mno}")
        logger.info(f"  Source documents: {taxonomy.source_documents}")
        logger.info(f"  Total features: {len(taxonomy.features)}")

        for f in taxonomy.features:
            primary_str = ", ".join(f.is_primary_in) if f.is_primary_in else "(none)"
            ref_str = ", ".join(f.is_referenced_in) if f.is_referenced_in else "(none)"
            logger.info(
                f"  {f.feature_id}: primary in [{primary_str}], "
                f"referenced in [{ref_str}]"
            )
