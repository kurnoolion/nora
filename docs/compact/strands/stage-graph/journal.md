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
