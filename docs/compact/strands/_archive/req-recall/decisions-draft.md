<!-- Promoted at landing on 2026-08-16: -->
<!-- D-DRAFT-1 -> D-186 -->
<!-- D-DRAFT-2 -> D-187 -->
<!-- D-DRAFT-3 -> D-188 -->
<!-- D-DRAFT-4 -> D-189 -->
<!-- D-DRAFT-5 -> D-190 -->
<!-- D-DRAFT-6 -> D-191 -->
<!-- D-DRAFT-7 -> D-192 -->
<!-- D-DRAFT-8 -> D-193 -->
<!-- D-DRAFT-9 -> D-194 -->
<!-- D-DRAFT-10 -> D-195 -->
<!-- D-DRAFT-11 -> D-196 -->
<!-- D-DRAFT-12 -> D-197 -->

<!-- Draft ADRs for req-recall. Promoted to canonical DECISIONS.md
with sequential D-XXX at /land-strand. One entry per decision. -->

## D-DRAFT-1 — Recall checker: extract-vs-parse req-id diff as a bucketed, parse-blind sandbox tool

**Context:** Team reports of requirements visible in source PDFs but
absent downstream; no mechanized way to measure recognition recall or
localize where ids fall out of the pipeline.

**Decision:** `sandbox/verify_req_recall.py` diffs every extract-IR's
req-id candidates against the parse trees per cell, driven entirely by
the materialized cell profile (id pattern, type pattern, TOC entry
pattern). Output is BUCKETED CANDIDATES, not assertions: MISSING (with
locations), demoted, table-only, struck-only, toc-only, cross-doc,
section-id, UNPARSED. Liveness: a block is struck when either the block
flag or font_info.strikethrough is set; TOC entry lines are never body;
ids defined in a sibling doc of the same cell bucket as cross-doc.

**Why:** Bucketing over asserting because a raw regex over extract text
cannot distinguish definitions from citations — classification needs a
human/document pass, and the tool's job is to make that pass start from
a short list. Parse-blind by design: the checker does NOT replicate
parser logic (strike cascade, zoning), so cascade drops deliberately
surface as MISSING — replicating parse logic would make the checker
agree with the parser by construction and blind to its bugs. The
liveness rules were each field-driven: the naive version inflated
MISSING ~40x on a strike-heavy corpus (struck headings counted via
their live TOC echoes) and misread sibling-doc citations as gaps.

**Consequences:** Checker totals are candidates until classified;
cascade-dropped ids always need a classification pass. The tool depends
on materialized profiles (out/profile), so it runs only on built envs.

## D-DRAFT-2 — Fused-heading promotion: end-anchored-only rescue; extractor join untouched

**Context:** Headings whose title and id are separated only at layout
level join into fused text (`SOME TITLEABC-FOO-123`); with a run
topology that defeats the last-run solo-match, such ids never promoted.

**Decision:** In `anchor="last_run"` corpora, a multi-run heading whose
text carries an id at the very END (trailing whitespace tolerated)
promotes it and strips it from the title; logged as
`parser.format_error kind=fused_trailing_heading`. Mid-heading id
mentions still never promote. The extractor's run join is untouched.

**Why:** End-anchored-only preserves the no-promotion semantic for
inline citations while healing every fused topology. Extractor-side
separator insertion was rejected: DOCX splits runs mid-word, so blind
separators corrupt titles; and a parser-side fix heals existing
extracts (parse-only rebuild, no re-extract). Field note: on the live
corpus the "fused" headings turned out to be struck revisions the
parser correctly drops — the rescue found nothing to rescue there, but
stands as correct behavior for genuinely fused shapes.

**Consequences:** A heading legitimately ENDING with a citation would
be promoted; no such shape observed, and requirement_type filtering
still applies downstream.

## D-DRAFT-3 — Announced requirements: standalone labeled-id paragraphs anchor their own node (forward attach + backward move)

**Context:** In id_label corpora, requirements appear as a standalone
paragraph `(Marker) ID: <id>` with the statement in adjacent blocks.
Previously the announcement was absorbed into the preceding node (or at
best scavenged as the section's id), so every announcement after a
section's first vanished from the corpus (~500 per cell).

**Decision:** A body paragraph that is NOTHING but the labeled id
(optional parenthesized marker, which becomes the priority) spawns its
own Requirement — structurally like a leading-id node (no
section_number, parented to the enclosing heading); following
body/table/image blocks attach to it until the next heading or
announcement. A post-pass handles the trailing form (body first, id
line last): an announced node that ends the walk EMPTY moves its id
backward onto the nearest preceding content node without an id of its
own, and the empty shell is dropped. A labeled id embedded in longer
prose remains a reference.

**Why:** Spawning per announcement is the only shape that preserves
each requirement's own body; section-scavenging collapses N
requirements into one node. The backward move is a post-pass (not
spawn-time lookahead) so the validated forward behavior is untouched
and only provably-empty nodes move. Field-validated: MISSING 876→380 /
829→341 with zero ids lost and corpus rows growing by exactly the new
nodes.

**Consequences:** Sections that formerly scavenged an announced id now
parent a child requirement instead (tree reshape, id set preserved).
Empty container sections are the normal shape for structure whose body
lives on announced nodes; tree-backed UI surfaces see more id-less
container nodes.

## D-DRAFT-4 — Bare unlabeled ids stay references; bare_small_font_stamp exists but is active nowhere

**Context:** ~19 bare small-font solo-id paragraphs per cell in an
id_label corpus looked like id-after-body requirement stamps; a prior
contract (with a regression test) says an unlabeled id in an id_label
corpus is always a reference.

**Decision:** The contract stands. The opt-in profile knob
`requirement_id.bare_small_font_stamp` (default false; guards: exact
solo id, small font, enclosing section has body text and no id) was
implemented rather than a contract flip — and field validation then
INVERTED the premise: the bare ids are the corpus's citation style (18
of 19 defined in sibling docs; 1 cites a deliberately-nonexistent
deprecated requirement). The knob correctly refused every activation
and remains in code, active in no profile.

**Why:** An opt-in knob converts a risky global contract change into a
per-corpus decision with guards — and the guards are what caught the
wrong premise. Keeping the dormant knob costs nothing and the guard
behavior is now field-proven for any future corpus that genuinely
stamps ids after bodies.

**Consequences:** Activating the knob is a committed-profile edit, so
it is reviewable per corpus. The cell-wide cross-doc resolution in the
recall checker (D-DRAFT-1) exists specifically so this misread class
cannot recur.

## D-DRAFT-5 — Regenerating writers use temp + atomic rename (hardlink-snapshot isolation)

**Context:** Serve labels are promoted as hardlink snapshots and
declared immutable, but four writer classes rewrote existing files in
place on the same inode — parse-tree saves on forced re-parse, extract
IR saves, the adapter's corpus/queries/qrels/metadata emits, and the
incremental prune's run-cache rewrites — so build re-runs retroactively
mutated promoted labels.

**Decision:** Every regenerating writer writes a same-directory temp
file and `os.replace()`s it: each rewrite lands on a fresh inode, so
hardlink-shared snapshots keep their bytes, and a crash mid-write can
never tear a resume-critical file. Alternatives rejected: copy-based
promote (disk cost, doesn't fix crash-tearing) and post-snapshot
link-breaking in promote (masks future writer regressions instead of
surfacing them).

**Why:** Fixes the whole class at the writers rather than defending at
the snapshot; the crash-safety is a free second win for exactly the
files a torn write hurts most. Field-validated: 515 label files
byte-identical through a promote → forced rebuild → prune cycle, zero
temp residue, link counts dropping to 1 as designed.

**Consequences:** New writers that regenerate existing artifacts must
follow the same pattern (tests assert the hardlink-isolation property
directly). Labels mutated before the fix were written off — nothing
serving-critical was touched, and old labels are GC candidates.

## D-DRAFT-6 — Zone ruling: reference-list, standards-list, revision-history, and frontmatter ids drop with their sections by design

**Context:** Recall classification on the strike-heavy corpus left ~90
ids per cell in reference-list, standards-list, revision-history, and
introduction sections; deciding whether those are recognition gaps
bounds the remaining triage work.

**Decision:** Ids appearing inside those zones are citations or
metadata, not requirement definitions — the parser's reference-list,
revision-history, and content-start/zoning machinery deliberately drops
them with their sections. They are correct drops, not gaps.

**Why:** Those sections enumerate or cite requirements rather than
define them; recognizing their ids would mint duplicate or phantom
requirements — the exact defect class earlier ingestion work removed.

**Consequences:** Recall triage treats ids in those zones as explained;
only ids in body/requirement zones count toward the true gap.
Doc-title-level ids remain to be classified against plan metadata.

## D-DRAFT-7 — Separator-absorption id canonicalization; parser and checker mirror exactly

**Context:** Source families write ids with stray whitespace: between
bare tokens ("ABC FOO 12") and adjacent to existing separators
("ABC-FOO- 123", a systematic labeled-announcement class, 44+ per
cell). Naive whitespace→underscore canonicalization produced malformed
"ABC-FOO-_123" forms, and the recall checker diffing raw strings
against canonical tree ids reported entire doc families as phantom
MISSING (404/408 on one family).

**Decision:** Whitespace ADJACENT to an existing `-`/`_` separator
absorbs into the separator; only whitespace between two bare tokens
becomes an underscore. Implemented identically in the parser
(`_canonicalize_req_id`) and the checker (`_normalize`) — the two must
never diff a canonical form differently. Pattern-side tolerance
(`-\s?\d+`) is profile-tier and safe only because canonicalization
absorbs the space.

**Why:** Whitespace next to a separator is a source-formatting
artifact, not a missing separator — treating it as a token boundary
invents a second identity for the same requirement. The alternative
(checker-only normalization) leaves the parser storing malformed ids.

**Consequences:** Canonical id forms are stable across space-variant
sources; any future id-comparing tool must reuse the same two-step
canonicalization. Field-validated: +19 ids per affected cell, all
clean forms; phantom family cleared to 4.

## D-DRAFT-8 — Extraction claims sibling tails; displacement guards protect inline-labeled owners

**Context:** Chained announcement-after-body docs put statement N+1
into announced node N, so the extraction post-pass's claimable tail
carries a SIBLING sub-number relative to the absorber
("…6" tail in a "…5" node). The original strictly-extends guard
refused siblings, leaving field-validated empties unresolved; and an
absorber whose tail carried a DIFFERENT inline label could be
mis-claimed or overwritten (the field −1 regression).

**Decision:** The extraction guard refuses only a tail whose number
EQUALS the absorber's own section number (the demoted-duplicate shape
the guard was built for) or is an ANCESTOR of it; siblings and
extensions both claim. Two displacement guards: extraction refuses a
tail carrying a labeled id different from the claiming node's; the
plain backward move refuses a carrier whose text ENDS with a labeled
id.

**Why:** Field block-shapes proved the sibling case is the dominant
chained-doc reality, not an edge; strict-extends was protecting
against a shape (same-number duplicates) that the equality check
covers alone. The label guards keep the relaxation safe: a statement
carrying its own label always outranks positional inference.

**Consequences:** Any different-branch dotted tail is now claimable —
a text-level mis-claim sweep is queued as follow-up since coarse id
audits can't catch one. Field-validated: zero ids lost, chained
empties resolved.

## D-DRAFT-9 — Inline-trailing announcements: close the cursor, keep the scavenge shape (own-node spawn deferred)

**Context:** A third announcement form puts statement and label in ONE
paragraph ("<sub-num> <statement> (Marker) ID: <id>"). The
announced-cursor routing swallowed such paragraphs into the open
announced node, skipping the scavenge that anchored the inline label —
the label degraded to plain text and a neighboring announcement's id
was stamped over the node (field −1 regression).

**Decision:** A body paragraph ENDING with its own labeled id closes
the open announced cursor and takes the section body path, where the
existing scavenge anchors the inline id exactly as before the
announced-req machinery landed. End-of-text match only — mid-prose
citations keep flowing to the open announced req. The off-by-one node
shape (statement N+1's text on node N) is deliberately preserved; a
proper own-node spawn for the inline form is queued as post-batch
follow-up, not shipped pre-batch.

**Why:** Restoring the proven scavenge anchor was a zero-new-mechanism
fix on the batch's critical path; an own-node spawn changes node
topology corpus-wide and deserved its own validation cycle. A fourth
form (inline labels on numbering-path statements, one doc version)
needs that mechanism and was explicitly accepted as residue.

**Consequences:** One doc version ships with −1 anchored id + 3 empty
announced nodes as known state. The follow-up strand inherits the
own-node spawn design, which will also retire the off-by-one shape.

## D-DRAFT-10 — Heading-continuation defense is numbering-path-only

**Context:** Field validation showed styled level-1 chapter headings
with solo-id last runs failing to anchor while identically-shaped
level-2 siblings anchored — an empirical depth gate the design said
should not exist. Root cause: the heading-continuation defense (a
guard against PDF split-line artifacts misclassified as phantom
depth-1 chapters) documented itself as numbering-path-only but never
checked the detection method, so a TOC-paired chapter heading directly
following another heading was swallowed into that section's title, id
and all.

**Decision:** The defense is gated to the numbering classification
path in code. Word `Heading N` styles are authoritative: a styled
level-1 heading is never a split-line continuation artifact, so
docx_styles corpora bypass the defense entirely.

**Why:** The artifact the defense targets is a PDF text-extraction
failure mode; DOCX style tags cannot produce it. Depth is not a
definition-vs-citation discriminator anywhere else in the design — the
anchor-mode rules are — and this was the only (accidental) depth gate.

**Consequences:** Chapter-level requirement ids anchor on docx_styles
corpora (33-id field residue resolved on re-parse). Numbering-path
corpora keep the defense unchanged.

## D-DRAFT-11 — Generic <PLAN> placeholder class includes hyphen

**Context:** A live user complaint: an entire plan family absent from
the eval UI and retrieval. The generic `<PLAN>` placeholder
materialized to `[A-Za-z0-9_ ]+` — space allowed, hyphen not — so
every id under a hyphenated plan token failed to anchor (one family
100% invisible), and the recall checker, sharing the same pattern, was
blind to the loss by construction (the predicted pattern-blind class
found live).

**Decision:** The generic class is `[A-Za-z0-9_ -]+`, canonicalized
core-side in the placeholder table so every profile inheriting the
placeholder gets the fix; the interim inlined profile edit reverts to
the generic placeholder.

**Why:** Hyphenated plan tokens are real definitions, not noise; the
surrounding pattern (fixed prefix + `_\d+` suffix) keeps
false-positive risk immaterial. Core-side canonicalization beats
per-profile inlining because profile forks from the generic class
recreate the blindness.

**Consequences:** +931/+925 ids per affected cell; complaint doc went
0 → 231/231 anchored. Standing lesson recorded: any pattern the
checker shares with the parser is a shared blind spot — coverage
sweeps must scan with broader patterns than the profile's own.

## D-DRAFT-12 — Ship criterion applied: batch once with explicitly accepted residue

**Context:** After nine validation rounds the remaining recall gap was
one doc version needing a fourth recognition mechanism (inline labels
on numbering-path statements). Enrichment is hours-long and the hold
existed to run it exactly once; the original user complaint shipped
only with the batch.

**Decision:** Run the single combined enrichment batch with the
residue explicitly accepted and recorded: −1 anchored id + 3 empty
announced nodes in one doc version, plus a one-occurrence
prefix-space label sub-variant. Ship criterion: residue demonstrably
deep (new mechanism, not a fix to existing ones), confined to one doc
version, and net recall massively positive (checker-missing 13980 →
1524; ~1900 requirements recovered per largest cell, zero ids lost).

**Why:** Holding the batch for a mechanism that needs its own design
and validation cycle would have delayed every recovered requirement
and the complaint fix for a marginal, bounded gain. Accepting residue
implicitly, by contrast, hides it — the acceptance is recorded with a
follow-up queue.

**Consequences:** Corpus 37543 → 40097 served; eval-UI acceptance
passed (original complaint end-to-end, fresh 3-id report resolved by
pre-existing fixes). Follow-up queue (inline own-node spawn,
prefix-space tolerance, mis-claim text sweep) is a standing scope
decision after landing.
