## 2026-08-14 — Strand opened; recall checker; five validation rounds; recognition fixes

### Done this session
- Strand opened after team reports of requirements visible in source PDFs
  but absent downstream (spot-checked and confirmed).
- Built sandbox/verify_req_recall.py — extract-vs-parse req-id diff per
  cell, profile-driven, bucketed candidates (MISSING/demoted/table-only/
  struck-only/section-id/UNPARSED) rather than assertions (84787ac).
- Baseline against the real corpora: mno-b clean (1.5%), mno-c ~14%,
  mno-a reported ~40% (later shown to be mostly checker artifact).
- Recognition fix 1 (d7d2839): end-anchored fused-heading rescue in
  last_run mode + title cleanup; multi-run no-promotion preserved for
  mid-text citations. Field-validated as not-needed on the live corpus
  (the "fused" headings were struck revisions — correctly dropped) but
  correct for genuinely fused shapes.
- Recognition fix 2 (d7d2839): announced-requirement path in id_label
  corpora — standalone labeled-id paragraph spawns its own requirement;
  following body/table/image blocks attach to it. Validated: mno-c
  MISSING 876→380 / 829→341, zero ids lost, corpus rows +132/+126.
- bare_small_font_stamp knob (fa9f24f): opt-in id-after-body stamp.
  Field validation inverted the premise — bare small-font ids are the
  corpus's citation style; knob correctly refused all activations and
  stays dormant everywhere. Unlabeled-id-is-a-reference contract upheld.
- Checker liveness fixes (4a8f1cb): font-level strikethrough honored;
  TOC entry lines bucket as toc_only. mno-a MISSING collapsed 6175→1348;
  true recognition gap ~138/cell (~2%), consistent with zero user
  reports against that corpus.
- Backward move + cross_doc (e06346b): trailing announcements (body
  first, id line last) move their id onto the preceding id-less carrier;
  checker resolves ids cell-wide so sibling-doc citations never read as
  gaps.
- Zone ruling: reference-list, standards-list, revision-history, and
  introduction/frontmatter sections drop their ids WITH the section by
  design — bounds the remaining classification to ~50 ids/cell.
- Also this session: eval-studio-ux-fix merged + landed (2d07ba4);
  serve-label integrity — all regenerating writers now write temp +
  atomic rename (afb2aca), field-validated byte-identical labels.
- Suite 2078 → 2120 passed (42 new tests).

### In progress
- Final mno-c re-parse validation (backward move) + fixed-checker
  totals with cross_doc split; doc-title-vs-plan-id check; shell
  pre-vs-post decisive check; 47/34-id edge bucket.

### Next
- On validation green: end the enrichment hold with the single combined
  enrichment batch (ingestion fixes batched into one expensive cycle —
  the strand's founding constraint).
- Re-check the original team reports in Eval Studio post-batch — the
  acceptance criterion for the strand.
- mno-b user-report context still open (ids exist end to end; likely a
  serving/retrieval question, tracked independently of parsing).

### Flags
- table-fidelity strand journal is 2 sessions behind (rounds 2-3:
  headers-only render fix, checker semantics, close-out validation) and
  its draft decisions are uncaptured — needs a table-fidelity-bound
  close or capture at /land-strand.
- Eval Studio picker/req-browser read parse trees — announced-node and
  backward-move reshaping will surface there after the next promote.

## 2026-08-16 — Rounds 6–10: recognition fixes converge, batch ships, acceptance passes

### Done this session
- Absorbed-statement extraction post-pass (fixpoint; text-only eligibility;
  sibling tails claimable) — closing-announcement statements recovered from
  id-bearing absorbers; chained docs unwind completely.
- Inline-trailing announcement defense + displacement guards — third
  announcement form keeps its own id; field −1 regression fixed.
- Heading-continuation defense gated to numbering path — the de-facto
  level-1 anchor gate behind the 33-id chapter residue; docx_styles
  chapter headings now anchor.
- Separator-absorption id canonicalization (parser + checker) — space-variant
  labels anchor with clean canonical ids; generic <PLAN> class gains hyphen
  (live class-(b) complaint: entire plan families invisible end-to-end).
- Checker: cell-wide cross_doc resolution and whitespace canonicalization
  landed; TOTAL MISSING 13980 (strand start) → 1524, honest buckets.
- Combined enrichment batch ran ONCE per the hold: corpus 37543 → 40097,
  ~10.8k docs enriched, zero non-benign verify failures, serving healthy.
- Eval-UI acceptance PASSED: original complaint resolved end-to-end (232
  rows from 0, live probe top-10); fresh 3-id report resolved by
  pre-existing fixes. Field validation closed.
- Code comments swept for internal process references; composition gate
  extended.

### In progress
- (none — strand work complete; awaiting /land-strand)

### Next
- /land-strand req-recall: promote D-DRAFT-1..12, archive the strand.
- Scope decision on the follow-up queue: inline-form own-node spawn,
  prefix-space label tolerance, sibling-claim text sweep.

### Flags
- Accepted residue: one doc version carries −1 anchored id + 3 empty
  announced nodes (fourth announcement form) — known state, queued.
- table-fidelity strand journal still 2 sessions behind (pre-existing flag).
