## 2026-11-10 — Steps 2 and 3 built: store, recipes, enrich stamp, Redo, GC

### Done this session
- Step 2 (9a57ecb): host stage store and corpus root; proven on a stack (probe 2 found all four corpus stages, 9s).
- Taxonomy-gap follow-up (77a71e0): TAX-W003, prompts gate checks batch env files (CYC-E042), rollback refused when its label is no longer active; cell re-derived and served, recall at baseline.
- Step 3 in four slices: recipes (0e1e254), enrich as a driver-side stage (2e5f79d), stage origins and Redo (7829963), store GC (63dc8dd). D-DRAFT-9..13.
- All slices merged and running on a stack; GC dry run there kept all entries, nothing to remove.

### In progress
- End-to-end check on the next self-serve ingest: recipe line, enrich_stamped, manifest origins, page badges.

### Next
- `/drift-check dev-full` over pipeline, ingest_runner, stores, web before landing.
- Land the strand after that ingest (/land-strand; promote D-DRAFT-1..13).
- Open: the second stack's runner (runner-driven or hand-built).

### Flags
- Labels carry STAGES.json only from 0e1e254 on; GC warns on a labels root until the first label promoted after it.

## 2026-11-13 — Steps 4 and 6 built; stack A rebuilding on the stage graph

### Done this session
- Step 4, five slices plus a fix (3730d1e..0d2fe67, ff36f23): builds hold manifests, every reader asks `locate`, promote assembles through it, extract reuses IRs by source digest, empty build dirs never hide the store. Confirmed on stack B. D-DRAFT-14, 15.
- Step 5 groundwork: recipe sira-generic (d890fe9, D-DRAFT-17), runner guards for a second stack (D-DRAFT-16), extractor switches in the extract fingerprint (c83e8ae, D-DRAFT-18), enrich concurrency from the env (63dd370), refusal fallback on the one-request-per-call path (7f50295).
- Step 6, five slices (e291514..964a227): stale-set refusal and rebase at Serve (D-DRAFT-19), per-build cycles and host-wide enrich slots (D-DRAFT-20), parallel runner workers one per MNO (D-DRAFT-21), Ingest page follows them.
- Select-synth answers lose reasoning written as plain prose (b36ef37, D-DRAFT-22).
- PRs #25, #28, #29 merged.

### In progress
- Stack A phase 4 (enrich, all cells, concurrency 8; hold at 8).
- Stack B store+web rebuild to b36ef37 (sent; awaiting report and `working_text_marks` counts).

### Next
- mno-b/REL-1 count reconciliation before phase 5.
- Phase 5: promote, convert, publish to stack A's registry, golden gate; no flip without a go; `--skip-standards` on any parse.
- Phase 6: stack A's runner (peer_registry_roots, staging store, token, rebuild, service).
- Step 7 (new unit kinds); then `/drift-check dev-full` and land (D-DRAFT-1..22).
- PR #31 after the rebase/retarget; delete the sira-intent branches after the final merge.

### Flags
- mno-b/REL-1: "Enriching 50 docs" against 254 more kept rows; likely interrupted-then-resumed (heal-torn on 9 rows), unconfirmed.
- Step 4 host evidence still owed: a label hard-linked from the store (phase 5 shows it).
- Parallel ingests are untested on a real stack; stack B runs one worker.
