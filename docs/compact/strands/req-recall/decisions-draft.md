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
