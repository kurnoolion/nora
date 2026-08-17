## D-DRAFT-1 — Bounds-first req-id over-capture guard with a profile-tunable word bound

**Context:** A permissive plan-token class under a one-sided anchor can
run past the real id into surrounding prose (any prose ending in
``-<digits>`` completes the match); whitespace canonicalization welds
the capture into one silently corrupted token. Field-validated: 354
corrupted served rows on one corpus, 1 per cell on another. The first
guard deployment attempted recovery on every whitespace-bearing
capture with a fixed 3-word bound — and field validation rejected it:
~220 legitimate multi-word-plan ids lost per cell, and "shortest slice
that fullmatches" recovery could silently truncate a legitimate id
whose plan token carries an interior ``-<digits>`` segment.

**Decision:** Every raw pattern match passes a bounds-first guard
before canonicalization: an in-bound capture (≤
``requirement_id.guard_max_words`` whitespace-separated words — a
profile knob, module default 6 — and ≤64 chars) passes UNTOUCHED,
never recovered, never rejected; only over-bound captures get
anchored-side weld recovery (recovered slice itself in-bound), else a
loud no-id skip. Recovery is side-aware: end-anchored captures
(trailing rescue, TOC tail-peel) recover from the suffix side so a
mid-text citation is never resurrected.

**Why:** In-bound pass-through eliminates the truncation class
structurally rather than by tuning; a visible no-id skip beats an
invisible wrong id; and corpus inventory width is corpus data — it
belongs in the profile (retunable without a code round-trip), not in
code, which is exactly where the data-blind first deployment went
wrong.

**Consequences:** Profiles for corpora with narrow inventories should
pin the knob below the default (one profile pins 5 from measured
inventory); the checker must read the same knob (D-DRAFT-3); two
ParseStats counters (``req_id_over_captures_recovered`` /
``req_id_captures_rejected``) surface guard activity per parse; the
single-id weld shape (id + prose within the bound) is accepted
residue, governed by the bound and profile class discipline.

## D-DRAFT-2 — Containment trigger: two complete ids in one capture recover bounds-independently

**Context:** Field experiment (a knob-tightening test) proved the
``id + prose + id`` weld capture can be NARROWER than the corpus's
widest legitimate id — the weld sat at ≤4 raw words while legitimate
inventory reaches 5 — so no value of the word bound separates them.
Segment arithmetic was also ruled unsafe as a bound basis (single-word
ids can carry literal underscores).

**Decision:** A capture whose proper whitespace-bounded PREFIX slice
AND proper SUFFIX slice each fullmatch the id pattern holds at least
two complete ids and triggers anchored-side recovery regardless of
bounds — checked before the in-bound pass-through.

**Why:** Two complete ids in one capture is a structural fact about
the capture, not a width heuristic — definitionally not one id.
Field-measured on a full cell inventory: the weld capture counts 2,
all 10753 legitimate ids count exactly 1, zero false positives (only
one edge of a legitimate id carries the literal prefix, so its edges
never both fullmatch). Equivalent to a non-greedy ≥2-match count for
prefix-bearing patterns, and it reuses the existing slice machinery.

**Consequences:** The bounds (D-DRAFT-1) and containment are
complementary: bounds catch id + long prose, containment catches
id + prose + id; the single-id in-bound weld shape remains accepted
residue. The 0-false-positive result is measured on one corpus's
inventory; the trigger is generic but its false-positive claim should
be re-checked when a corpus's pattern lacks a literal prefix.

## D-DRAFT-3 — Parser and checker share one guard function and one knob

**Context:** 354 corrupted served ids survived every standing check
because the recall checker scanned extract-side text with the SAME
unguarded pattern semantics as the parser — parser and checker
over-captured in agreement, so the corruption was invisible to the
tool meant to find it. Guarding only the parser would instead surface
every weld as a false MISSING on the extract side.

**Decision:** The guard is a module-level function
(``guard_req_id_capture``) in the parser module; the extract-side
recall checker imports it and reads the word bound from the SAME
profile knob (``requirement_id.guard_max_words``) it passes to the
parser.

**Why:** Divergence between the two sides is the failure mode — shared
blindness hides corruption, one-sided guarding manufactures false
gaps. A single shared function with a single shared knob makes
divergence structurally impossible instead of procedurally avoided.

**Consequences:** The sandbox checker imports from core (an accepted
sandbox→core dependency, consistent with existing sandbox tooling);
any future change to guard semantics automatically applies to both
sides; checker id-diff baselines shift only when guard semantics
change, and such changes must expect a re-baseline round.

## D-DRAFT-4 — dedup repair command: newest-wins duplicate-row repair for resume traces

**Context:** Repeated or interrupted enrich invocations append
duplicate kept + enrichment records for the same doc (field-observed
via single-req smoke reruns, with DIFFERENT phrase sets per record).
``verify-run --strict`` FAILs on duplicate kept rows, but no repair
command addressed the state: heal-torn sees no torn lines or orphans
(the duplicate rows are well-formed and paired), prune skips unchanged
docs, retry-failed only evicts failed rows. The field repair was
manual, under one-off authorization.

**Decision:** ``sira_incremental dedup`` — keep only the NEWEST row
per resume key (doc_id for doc-enrich, (query_id, doc_id) for rerank)
in the stage's kept trace and enrichments file, dropping earlier
duplicates; temp + atomic-rename rewrite (never in place — promoted
snapshots share files by hardlink); ``--dry-run`` reports without
rewriting; unparseable and None-keyed lines are kept defensively.

**Why:** A strict verifier that can FAIL on a state the tooling cannot
repair forces ad-hoc manual file surgery on resume-critical files —
the command encodes the field-proven procedure (keep newest, both
files, atomic) so the failure mode is reachable-but-repairable.
Newest-wins because rows append in run order: the last record is the
latest enrichment.

**Consequences:** The repair surface for run dirs is now
prune / retry-failed / heal-torn / dedup, each owning a disjoint
state class; dedup is repair-only (does not prevent duplicates —
prevention would need invocation-level locking, out of scope);
operational note — the command ships in the sira-batch image, so
sandbox-side changes require rebaking BOTH images.
