"""Compact FIX report generators for profile and taxonomy corrections.

Strip all proprietary document content. Only include:
  - field names, regex patterns, thresholds, counts
  - feature IDs, feature names, keyword tokens
  - diff deltas (added / removed / renamed)

Never include:
  - sample document text, sample req IDs, sample heading text
  - feature descriptions (may echo MNO-specific language)
  - zone descriptions or heading sample arrays
"""

from __future__ import annotations

from src.corrections.schema import FixReport
from src.profiler.profile_schema import DocumentProfile
from src.taxonomy.schema import FeatureTaxonomy, TaxonomyFeature


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _fmt_change(old: str, new: str) -> str:
    old_s = old if old else '""'
    new_s = new if new else '""'
    return f"{old_s} -> {new_s}"


def _list_delta(old: list[str], new: list[str]) -> tuple[int, int, list[str], list[str]]:
    old_set, new_set = set(old or []), set(new or [])
    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)
    return len(added), len(removed), added, removed


def profile_fix_report(
    output: DocumentProfile | None,
    correction: DocumentProfile,
    env_name: str,
) -> FixReport:
    """Diff correction vs output profile, emit compact FIX block."""
    lines: list[str] = [f"FIX {env_name} profile"]
    summary: dict[str, int] = {"changes": 0}

    if output is None:
        lines.append("(no output profile to diff against — full correction)")
        lines.append(f"numbering_pattern: {correction.heading_detection.numbering_pattern!r}")
        lines.append(f"req_pattern: {correction.requirement_id.pattern!r}")
        lines.append(f"zones: {len(correction.document_zones)}")
        summary["changes"] = 4
        return FixReport(env=env_name, artifact="profile", lines=lines, summary=summary)

    # Heading numbering pattern
    if output.heading_detection.numbering_pattern != correction.heading_detection.numbering_pattern:
        lines.append(
            "numbering_pattern: "
            + _fmt_change(
                output.heading_detection.numbering_pattern,
                correction.heading_detection.numbering_pattern,
            )
        )
        summary["changes"] += 1

    # Requirement ID pattern
    if output.requirement_id.pattern != correction.requirement_id.pattern:
        lines.append(
            "req_pattern: "
            + _fmt_change(output.requirement_id.pattern, correction.requirement_id.pattern)
        )
        summary["changes"] += 1

    # Requirement components (e.g. separator, plan_id_position)
    old_comp = output.requirement_id.components or {}
    new_comp = correction.requirement_id.components or {}
    if old_comp != new_comp:
        diffs = []
        for k in sorted(set(old_comp) | set(new_comp)):
            if old_comp.get(k) != new_comp.get(k):
                diffs.append(f"{k}={old_comp.get(k, '-')}→{new_comp.get(k, '-')}")
        if diffs:
            lines.append("req_components: " + ", ".join(diffs))
            summary["changes"] += 1

    # Zones — diff by zone_type (section_pattern may also change)
    old_zones = {z.zone_type: z.section_pattern for z in output.document_zones}
    new_zones = {z.zone_type: z.section_pattern for z in correction.document_zones}
    added_zones = sorted(set(new_zones) - set(old_zones))
    removed_zones = sorted(set(old_zones) - set(new_zones))
    modified_zones = sorted(
        zt for zt in set(old_zones) & set(new_zones)
        if old_zones[zt] != new_zones[zt]
    )
    if added_zones or removed_zones or modified_zones:
        lines.append(
            f"zones: +{len(added_zones)}/-{len(removed_zones)}/~{len(modified_zones)}"
        )
        for zt in added_zones:
            lines.append(f"  +{zt}({new_zones[zt]})")
        for zt in removed_zones:
            lines.append(f"  -{zt}")
        for zt in modified_zones:
            lines.append(f"  ~{zt}: {_fmt_change(old_zones[zt], new_zones[zt])}")
        summary["changes"] += len(added_zones) + len(removed_zones) + len(modified_zones)

    # Header/footer patterns
    ha, hr, h_add, h_rem = _list_delta(
        output.header_footer.header_patterns, correction.header_footer.header_patterns
    )
    fa, fr, f_add, f_rem = _list_delta(
        output.header_footer.footer_patterns, correction.header_footer.footer_patterns
    )
    if ha or hr:
        lines.append(f"header_patterns: +{ha}/-{hr}")
        for p in h_add:
            lines.append(f"  +{p!r}")
        for p in h_rem:
            lines.append(f"  -{p!r}")
        summary["changes"] += ha + hr
    if fa or fr:
        lines.append(f"footer_patterns: +{fa}/-{fr}")
        for p in f_add:
            lines.append(f"  +{p!r}")
        for p in f_rem:
            lines.append(f"  -{p!r}")
        summary["changes"] += fa + fr

    if output.header_footer.page_number_pattern != correction.header_footer.page_number_pattern:
        lines.append(
            "page_number_pattern: "
            + _fmt_change(
                output.header_footer.page_number_pattern,
                correction.header_footer.page_number_pattern,
            )
        )
        summary["changes"] += 1

    # Cross-ref patterns
    cs_a, cs_r, cs_add, cs_rem = _list_delta(
        output.cross_reference_patterns.standards_citations,
        correction.cross_reference_patterns.standards_citations,
    )
    if cs_a or cs_r:
        lines.append(f"xref.standards_citations: +{cs_a}/-{cs_r}")
        for p in cs_add:
            lines.append(f"  +{p!r}")
        for p in cs_rem:
            lines.append(f"  -{p!r}")
        summary["changes"] += cs_a + cs_r

    if (
        output.cross_reference_patterns.internal_section_refs
        != correction.cross_reference_patterns.internal_section_refs
    ):
        lines.append(
            "xref.internal_section_refs: "
            + _fmt_change(
                output.cross_reference_patterns.internal_section_refs,
                correction.cross_reference_patterns.internal_section_refs,
            )
        )
        summary["changes"] += 1
    if (
        output.cross_reference_patterns.requirement_id_refs
        != correction.cross_reference_patterns.requirement_id_refs
    ):
        lines.append(
            "xref.requirement_id_refs: "
            + _fmt_change(
                output.cross_reference_patterns.requirement_id_refs,
                correction.cross_reference_patterns.requirement_id_refs,
            )
        )
        summary["changes"] += 1

    # Body text thresholds
    if output.body_text.font_size_min != correction.body_text.font_size_min:
        lines.append(
            f"body_text.font_size_min: {output.body_text.font_size_min} -> {correction.body_text.font_size_min}"
        )
        summary["changes"] += 1
    if output.body_text.font_size_max != correction.body_text.font_size_max:
        lines.append(
            f"body_text.font_size_max: {output.body_text.font_size_max} -> {correction.body_text.font_size_max}"
        )
        summary["changes"] += 1

    fa_a, fa_r, fa_add, fa_rem = _list_delta(
        output.body_text.font_families, correction.body_text.font_families
    )
    if fa_a or fa_r:
        lines.append(f"body_text.font_families: +{fa_a}/-{fa_r}")
        summary["changes"] += fa_a + fa_r

    if summary["changes"] == 0:
        lines.append("(no differences)")

    return FixReport(env=env_name, artifact="profile", lines=lines, summary=summary)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

def _feature_index(tax: FeatureTaxonomy) -> dict[str, TaxonomyFeature]:
    return {f.feature_id: f for f in tax.features if f.feature_id}


def taxonomy_fix_report(
    output: FeatureTaxonomy | None,
    correction: FeatureTaxonomy,
    env_name: str,
) -> FixReport:
    """Diff correction vs output taxonomy, emit compact FIX block.

    Match features by feature_id. Reports:
      - added / removed features (with keyword list for added)
      - renamed features (feature_id preserved, name changed)
      - keyword additions / removals per feature
      - description edits (flag only, text not included)
    """
    lines: list[str] = [f"FIX {env_name} taxonomy"]
    summary = {"added": 0, "removed": 0, "renamed": 0, "kw_edits": 0, "desc_edits": 0}

    new_idx = _feature_index(correction)

    if output is None:
        lines.append(f"(no output taxonomy — full correction, feat={len(new_idx)})")
        summary["added"] = len(new_idx)
        return FixReport(env=env_name, artifact="taxonomy", lines=lines, summary=summary)

    old_idx = _feature_index(output)

    added_ids = sorted(set(new_idx) - set(old_idx))
    removed_ids = sorted(set(old_idx) - set(new_idx))
    common_ids = set(old_idx) & set(new_idx)

    renamed: list[tuple[str, str, str]] = []  # (feat_id, old_name, new_name)
    kw_diffs: list[tuple[str, list[str], list[str]]] = []  # (feat_id, +kws, -kws)
    desc_edited: list[str] = []

    for fid in sorted(common_ids):
        old_f, new_f = old_idx[fid], new_idx[fid]
        if old_f.name != new_f.name:
            renamed.append((fid, old_f.name, new_f.name))
        kw_add, kw_rem = set(new_f.keywords or []) - set(old_f.keywords or []), set(
            old_f.keywords or []
        ) - set(new_f.keywords or [])
        if kw_add or kw_rem:
            kw_diffs.append((fid, sorted(kw_add), sorted(kw_rem)))
        if (old_f.description or "") != (new_f.description or ""):
            desc_edited.append(fid)

    summary["added"] = len(added_ids)
    summary["removed"] = len(removed_ids)
    summary["renamed"] = len(renamed)
    summary["kw_edits"] = len(kw_diffs)
    summary["desc_edits"] = len(desc_edited)

    lines.append(
        f"feat_total={len(new_idx)} added={summary['added']} removed={summary['removed']} "
        f"renamed={summary['renamed']} kw_edits={summary['kw_edits']} desc_edits={summary['desc_edits']}"
    )

    if added_ids:
        for fid in added_ids:
            f = new_idx[fid]
            kws = ",".join((f.keywords or [])[:8])
            more = "…" if len(f.keywords or []) > 8 else ""
            lines.append(f"add: {fid}(kws: {kws}{more})")

    if removed_ids:
        lines.append("remove: " + ", ".join(removed_ids))

    if renamed:
        for fid, oldn, newn in renamed:
            lines.append(f"rename: {oldn}->{newn} [{fid}]")

    if kw_diffs:
        for fid, adds, rems in kw_diffs:
            parts = []
            if adds:
                parts.append("+" + ",".join(adds[:6]) + ("…" if len(adds) > 6 else ""))
            if rems:
                parts.append("-" + ",".join(rems[:6]) + ("…" if len(rems) > 6 else ""))
            lines.append(f"kw: {fid} {' '.join(parts)}")

    if desc_edited:
        lines.append("desc: " + ", ".join(desc_edited) + "  (text omitted)")

    if sum(summary.values()) == 0:
        lines.append("(no differences)")

    return FixReport(env=env_name, artifact="taxonomy", lines=lines, summary=summary)
