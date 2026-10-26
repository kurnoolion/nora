### D-DRAFT-1 — Canonical form v3 strips Unicode format characters; the IR strips them at extraction

**Decision:** `canonical_text` removes category-Cf characters before NFKC; `FINGERPRINT_VERSION` = v3; `ContentBlock`/`TextRun` strip them at construction.
**Why:** 194 of 326 "changed" requirements between two real releases differed only by byte-order marks. Rejected: History-only normalization (breaks the shared join key); extraction-only (older trees stay noisy).
**Consequences:** Every fingerprint changed once; `overlay_restamp` recomputes stored record fingerprints from the origin-release text; unit identity carries the version (D-DRAFT-4).

### D-DRAFT-2 — `moved` needs a different parent; a changed outline number is `renumbered`

**Decision:** Location kinds: parent changed → `moved`; same parent, number changed → `renumbered` (chapter | sibling | other); a parent that vanished and reappeared with identical content → `renumbered` (parent).
**Why:** All 444 "moves" in one release pair kept their parent; a chapter insertion shifted their numbers. Rejected: masking children of renamed sections only (caught 0 of 444; parents are requirements, not sections).
**Consequences:** `req_history` still reports a parent-key change as `moved`; the diff resolves it with both releases' states.

### D-DRAFT-3 — A plan node added or removed marks the plan absent; edits exclude its rows

**Decision:** `diff_releases` reports `absent_plans`, `edits` (tallies without their rows) beside `counts`, and ranks `most_changed_sections` from edit rows only.
**Why:** 530 of 866 removals were nine plans missing from the newer release's documents, not edits. Rejected: dropping those rows (the page must say "plan not in this release").
**Consequences:** Consumers quote `edits` for change claims and `absent_plans` for coverage.

### D-DRAFT-4 — Store-version identity includes the fingerprint version (amends D-283)

**Decision:** `store_version.fingerprint_version`, an additive column `ensure()` adds to an older registry; reuse keys on fingerprint, schema and fingerprint version.
**Why:** Neither source fingerprint nor schema moves on a canonical-form change, so a republish would have kept every v2 file. Rejected: folding the version into `source_fingerprint` (breaks the flat-vs-DB identity parity check).
**Consequences:** Rows from before read as unversioned, so every unit republishes once after a bump; units with no fingerprint column reuse as before.

### D-DRAFT-5 — A change row's containing section: tree section parent, else nearest section node, else a two-level prefix

**Decision:** `containing_section()` in that order; sections aggregate and rank by it.
**Why:** On real corpora requirements hang under requirements or the plan root and nest four levels deep; per-requirement keys ranked requirements, raw prefixes split one region across levels. Rejected: a fixed two-level prefix alone (ignores real section nodes where they exist).
**Consequences:** Ranking is a region view; `section` on the row keeps the requirement's own number.
