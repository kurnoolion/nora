"""Profile placeholder substitution [D-062].

Profiles for proprietary corpora are stored with redaction placeholders
in their regex strings — `<MNO0>_REQ_<PLAN>_\\d+` instead of
`VZ_REQ_[A-Z0-9_]+_\\d+`. The mapping that resolves placeholders to
real values lives outside the public repo (gitignored
``customizations/mappings/<bootstrap_id>.json`` snapshot, falling back
to the live ``<env_dir>/state/cline-mapping.json`` Cline maintains).
This module joins the two at runtime.

Two classes of placeholders are recognised:

1. **Specific placeholders** — ``<MNO0>``, ``<PLAN3>``, ``<REL0>``,
   ``<MNO0_ALIAS>`` — names that exist in the mapping. The placeholder
   substitutes to ``re.escape(<mapped value>)`` so the literal value
   is matched verbatim in the regex.

2. **Generic placeholders** — ``<MNO>``, ``<PLAN>``, ``<REL>``,
   ``<DIGITS>`` — names *not* in the mapping (no index suffix). They
   substitute to a regex character class that matches the typical
   shape of the token. Used for wildcards: a regex that needs to match
   *every* plan in the corpus (not just the annotated ones) uses
   ``<PLAN>`` and gets ``[A-Z0-9_]+`` after substitution.

Profiles for public corpora (e.g. ``vzw_oa_profile.json``) carry real
values in their regex strings and have no mapping snapshot. The
substitution layer is a no-op for them — the load function returns the
profile unchanged when no mapping is found.
"""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from pathlib import Path

from core.src.profiler.profile_schema import DocumentProfile

logger = logging.getLogger(__name__)


# Generic placeholders → regex character class for the token's typical shape.
# Match order is longest-first so e.g. <DIGITS> isn't shadowed by a shorter prefix.
GENERIC_PLACEHOLDERS: dict[str, str] = {
    "<DIGITS>": r"\d+",
    "<MNO>": r"[A-Z]{2,4}",
    # ``<PLAN>`` covers plan-code tokens like LTEOTADM, lteOTADM, VoWiFi,
    # and multi-word forms like "LTE OTADM" / "Voice WiFi". Real corpora
    # ship all four:
    #   - all-caps (LTEOTADM)
    #   - CamelCase / mixed-case (VoWiFi, lteOTADM)
    #   - space-separated multi-word
    #   - hyphenated (e.g. "e-SIM OTA") — field-validated class-(b)
    #     recall loss (strand req-recall): plan families with hyphens
    #     failed to anchor ANY of their ids, invisibly to the checker,
    #     because the checker shares this pattern.
    # SAFETY IS ANCHOR-DEPENDENT (field-validated). The prefix + numeric
    # suffix bound this class only under END-bounded extraction
    # (anchor=last_run / solo-run / end-anchored rescue). Under a
    # ``leading_text`` anchor the id regex is start-anchored with NO end
    # bound, so this space-bearing greedy class runs across body prose
    # until any downstream ``-<digits>``/``_<digits>`` — swallowing
    # requirement text into the id, which whitespace canonicalization
    # then welds into one corrupted token. Leading-anchor profiles must
    # NOT use this placeholder: use a bounded, space-free class with
    # scoped ``\s?`` beside separators (see the field-validated profile
    # provenance notes). The hazard is class x anchor, not the class
    # alone.
    "<PLAN>": r"[A-Za-z0-9_ -]+",
    "<REL>": r"[A-Za-z0-9-]+",
}


def substitute_placeholders(
    profile: DocumentProfile, mapping: dict[str, str]
) -> DocumentProfile:
    """Return a copy of *profile* with placeholders substituted.

    Specific placeholders use the mapping (``re.escape`` applied to the
    value). Generic placeholders use ``GENERIC_PLACEHOLDERS``. Any
    placeholder that's neither specific nor generic stays in place — a
    warning is logged so the user can fix the mapping or rename.

    The input profile is not mutated; the returned profile is a deep
    copy with substituted regex-string fields.
    """
    out = deepcopy(profile)

    def sub(s: str) -> str:
        if not s:
            return s
        # Specific first — longest key first to avoid substring collisions
        # (e.g. <MNO0_ALIAS> must substitute before <MNO0>).
        for key in sorted(mapping.keys(), key=lambda k: -len(k)):
            value = mapping[key]
            if not isinstance(value, str):
                continue
            s = s.replace(f"<{key}>", re.escape(value))
        # Then generic
        for placeholder, char_class in GENERIC_PLACEHOLDERS.items():
            s = s.replace(placeholder, char_class)
        # Detect any remaining placeholders for visibility
        for m in re.finditer(r"<([A-Z][A-Z0-9_]*)>", s):
            logger.warning(
                "profile substitution: placeholder %s left unsubstituted "
                "(no mapping entry, no generic class)",
                m.group(0),
            )
        return s

    # Walk every regex-string field on the profile and substitute. Each
    # call site is explicit (not auto-generated) so we don't accidentally
    # substitute inside non-regex fields like profile_name or notes.
    out.heading_detection.numbering_pattern = sub(out.heading_detection.numbering_pattern)
    out.heading_detection.definitions_section_pattern = sub(
        out.heading_detection.definitions_section_pattern
    )
    out.heading_detection.priority_marker_pattern = sub(
        out.heading_detection.priority_marker_pattern
    )

    out.requirement_id.pattern = sub(out.requirement_id.pattern)
    out.requirement_id.requirement_type_pattern = sub(
        out.requirement_id.requirement_type_pattern
    )
    out.requirement_id.id_label_pattern = sub(out.requirement_id.id_label_pattern)

    pm = out.plan_metadata
    for fld in (pm.plan_name, pm.plan_id, pm.version, pm.release_date):
        fld.pattern = sub(fld.pattern)

    for zone in out.document_zones:
        zone.section_pattern = sub(zone.section_pattern)

    out.header_footer.header_patterns = [sub(p) for p in out.header_footer.header_patterns]
    out.header_footer.footer_patterns = [sub(p) for p in out.header_footer.footer_patterns]
    out.header_footer.page_number_pattern = sub(out.header_footer.page_number_pattern)

    crp = out.cross_reference_patterns
    crp.standards_citations = [sub(p) for p in crp.standards_citations]
    crp.internal_section_refs = sub(crp.internal_section_refs)
    crp.requirement_id_refs = sub(crp.requirement_id_refs)

    ad = out.applicability_detection
    ad.requirement_patterns = [sub(p) for p in ad.requirement_patterns]
    ad.global_section_pattern = sub(ad.global_section_pattern)

    out.definitions_entry_pattern = sub(out.definitions_entry_pattern)
    out.definitions_table_term_column = sub(out.definitions_table_term_column)
    out.definitions_table_definition_column = sub(out.definitions_table_definition_column)
    out.toc_detection_pattern = sub(out.toc_detection_pattern)
    out.toc_detection.style_pattern = sub(out.toc_detection.style_pattern)
    out.toc_detection.entry_pattern = sub(out.toc_detection.entry_pattern)
    out.revision_history_label_pattern = sub(out.revision_history_label_pattern)
    out.reference_list_section_pattern = sub(out.reference_list_section_pattern)
    out.reference_list_entry_pattern = sub(out.reference_list_entry_pattern)
    out.exclude_section_pattern = sub(out.exclude_section_pattern)
    out.content_end_marker = sub(out.content_end_marker)

    return out


# ---------------------------------------------------------------------------
# Mapping discovery + load chain
# ---------------------------------------------------------------------------

def _project_root_from_profile(profile_path: Path) -> Path | None:
    """Resolve the project root that owns ``customizations/`` for *profile_path*.

    Two-step lookup:

    1. **Walk up from the profile** until a directory containing
       ``customizations/`` is found. Works when the profile lives inside
       the project tree (e.g. ``customizations/profiles/bs_*.json``
       directly, or a copy somewhere under the repo).
    2. **Fall back to this module's own location.** ``profile_substitute.py``
       lives at ``<repo>/core/src/profiler/profile_substitute.py``, so
       ``Path(__file__).parents[3]`` is the repo root. This catches the
       common case where the pipeline has copied the profile to
       ``<env_dir>/out/profile/profile.json`` and ``env_dir`` is
       *outside* the repo tree — the walker would otherwise dead-end at
       ``env_dir``'s parent without finding ``customizations/``.

    Returns the project-root Path or None.
    """
    p = profile_path.resolve().parent
    while p != p.parent:
        if (p / "customizations").is_dir():
            return p
        p = p.parent

    # Walker dead-ended. Try this file's own location.
    self_root = Path(__file__).resolve().parents[3]
    if (self_root / "customizations").is_dir():
        return self_root

    return None


def find_mapping_file(
    profile_path: Path, env_dir: Path | None = None
) -> Path | None:
    """Locate the mapping JSON for *profile_path*, in priority order:

    1. ``customizations/mappings/<profile_stem>.json`` — per-bootstrap snapshot.
    2. ``customizations/mappings/<_provenance.bootstrap_id>.json`` —
       same snapshot, looked up by the bootstrap_id embedded in the
       profile content. Needed when the active profile has been copied
       to a generic name (e.g. the pipeline copies
       ``corrections/profile.json`` → ``out/profile/profile.json``;
       the stem becomes ``"profile"`` and step 1 misses).
    3. ``<env_dir>/state/cline-mapping.json`` — Cline's live mapping.
    4. None — substitution will be a no-op.

    Returns the resolved Path or None.
    """
    project_root = _project_root_from_profile(profile_path)
    if project_root is not None:
        snapshot = project_root / "customizations" / "mappings" / f"{profile_path.stem}.json"
        if snapshot.exists():
            return snapshot

        # Read _provenance.bootstrap_id from the profile content and
        # retry the snapshot lookup with that name. Tolerates profiles
        # that have been copied / renamed away from their source name
        # (the standard pipeline does this).
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict):
            prov = data.get("_provenance") or {}
            boot_id = prov.get("bootstrap_id") if isinstance(prov, dict) else None
            if isinstance(boot_id, str) and boot_id:
                by_id = project_root / "customizations" / "mappings" / f"{boot_id}.json"
                if by_id.exists():
                    return by_id

    if env_dir is not None:
        cline = env_dir / "state" / "cline-mapping.json"
        if cline.exists():
            return cline

    return None


def _load_mapping_dict(mapping_path: Path) -> dict[str, str]:
    """Read mapping JSON. Both shapes are accepted:

    1. Cline live: ``{"version": 1, "mappings": {<placeholder>: <real>, ...}}``
    2. Per-bootstrap snapshot: ``{"version": 1, "bootstrap_id": "...", "mappings": {...}}``

    Both have a ``mappings`` dict at the top level. Returns the dict
    (empty on parse failure — substitution becomes a no-op).
    """
    try:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("mapping load failed for %s: %s", mapping_path, exc)
        return {}
    raw = data.get("mappings") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    # Cline's live mapping uses placeholder-as-value semantics:
    # `{"<real>": "<placeholder>"}` (real → placeholder, for forward redaction).
    # The per-bootstrap snapshot can be either direction; we want the
    # "<key without brackets> → <real>" form. Normalize.
    return _normalize_mapping(raw)


def _normalize_mapping(raw: dict[str, str]) -> dict[str, str]:
    """Convert any of the supported on-disk shapes into ``{NAME: real}``.

    Cline's live mapping is keyed real → placeholder
    (``{"VZ": "<MNO0>", "<PLAN0>": "<PLAN0>"}``) for forward redaction.
    The per-bootstrap snapshot is keyed placeholder → real
    (``{"MNO0": "VZ", "PLAN0": "<PLAN0>"}``) — the form this module
    expects. Detect and flip when needed; the discriminator is whether
    keys are bracketed (live) or bare (snapshot).
    """
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k.startswith("<") and k.endswith(">"):
            # k is a placeholder; v should be a real value, but Cline
            # writes it the other way. Flip with care: detect by the
            # value's shape.
            inner = k[1:-1]
            out[inner] = v
        elif v.startswith("<") and v.endswith(">"):
            # k is real, v is placeholder — Cline live form.
            inner = v[1:-1]
            out[inner] = k
        else:
            # Plain {name: value} — accept as-is.
            out[k] = v
    return out


def load_substituted_profile(
    profile_path: Path, env_dir: Path | None = None
) -> DocumentProfile:
    """Load a profile and apply placeholder substitution.

    Drop-in replacement for ``DocumentProfile.load_json`` at the parser
    boundary. *env_dir* is optional; when provided it enables fallback
    to ``<env_dir>/state/cline-mapping.json``.

    ``substitute_placeholders`` is called unconditionally so that
    **generic placeholders** (``<PLAN>``, ``<DIGITS>``, ``<MNO>``,
    ``<REL>``) always expand to their regex character class — even
    when no mapping snapshot is found. Without this, a profile that
    has its specific placeholders manually filled in (e.g. user
    replaced ``<MNO0>`` → ``VZ``) but still contains generic
    ``<PLAN>`` would leave ``<PLAN>`` as a literal substring in the
    compiled regex, breaking every req_id match downstream. Specific
    placeholders with no mapping entry are surfaced as WARN logs by
    ``substitute_placeholders`` (not silently dropped).
    """
    profile = DocumentProfile.load_json(profile_path)
    mapping_path = find_mapping_file(profile_path, env_dir)
    if mapping_path is None:
        logger.debug(
            "profile %s: no mapping snapshot — generic placeholders will "
            "still substitute; any specific placeholders left will WARN",
            profile_path.name,
        )
        return substitute_placeholders(profile, {})
    mapping = _load_mapping_dict(mapping_path)
    logger.info(
        "applying %d mapping entries from %s to profile %s",
        len(mapping), mapping_path, profile_path.name,
    )
    return substitute_placeholders(profile, mapping)
