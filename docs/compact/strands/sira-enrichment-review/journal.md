## 2026-07-20 — Strand opened; full architecture pass

### Done this session
- Strand created + bound; phase = architecture (target module: web).
- Complete design pass captured in `enrichment-review-design.md` (4 settled
  sections), evolved through live dialogue:
  1. **Persistence** — per-MNO word-record delta overlay on a dedicated
     CORRECTIONS_DIR volume (outside builds + immutable serve labels).
     Each edit = a word record carrying label (campaign tag), reason
     (category + free-form note), attribution, origin release. Deliberate
     divergence from D-011 full-copy corrections: enrichments regenerate,
     and the delta IS the evaluation signal. Labels toggle via labels.json
     (remove-wins on collisions); reason categories extensible via seed file.
  2. **Cross-release propagation** — corrections keyed per MNO apply to all
     releases, guarded by token-set Jaccard over VANILLA BM25 index terms
     (same tokenizer retrieval uses; pre-enrichment by construction — no
     circularity). Records store origin.release only; the multi-cell
     service compares live term sets. Below threshold (0.85, tunable) or
     origin unloaded → HELD for review ([Re-affirm]/[Discard] in UI).
  3. **Access + self-service apply** — sira-query serves cell data over
     HTTP (GET /cells/<cell>/enrichments, /plans; loaded_at in /cells) and
     gains POST /cells/<cell>/reload (re-runs _load_one_cell, atomic swap,
     per-cell lock) so experts run edit → apply → re-query WITHOUT an
     operator. Web owns overlay read/write (flock, last-writer-wins);
     service mounts corrections ro and applies overlay at cell load — key
     enabling fact verified: enrichment phrases apply IN MEMORY at load,
     so no index rebuild is ever needed.
  4. **Evaluation loop** — deterministic FIX-style export: label × category
     matrix, top removed/added words, suppressions, held summary, verbatim
     notes; plus a prompt-fix SCORECARD mode (per remove-record: does the
     new LLM run still produce the word?) — the reason stale remove-records
     are kept.
  5. **UI contract** — /enrichment-review page (Bootstrap+HTMX, parse-review
     precedent): MNO→Release→Plan cascade, sticky stamping bar
     (label/reason/note/name), chip grammar (plain ×→ghost ↺, green adds,
     suppress-collapse, held banners), stateless pending (overlay mtime vs
     loaded_at), Apply hits both stacks. Team-mode gate must admit the page.
- (Pre-strand, same day: compact→compact-skill rename, 5830cc7; transcript
  size guard rails in ~/.claude — outside this repo.)

### In progress
- Nothing — design complete, no code yet.

### Next
- /switch-phase development; implement in slices: (1) sira-query endpoints
  (enrichments/plans/loaded_at/reload) — UI is unbuildable without them;
  (2) overlay store + edit API in web (flock, word records); (3) the page +
  chip grammar; (4) apply/reload wiring + pending state; (5) exports +
  scorecard. Compose: CORRECTIONS_DIR volume (rw web, ro sira-query).
- MODULE.md updates ride the dev slices (web surface + team-gate change;
  sandbox service additions are outside the module system — note in web's
  runtime-edge line).

### Flags
- bm25x per-doc term extraction (or callable tokenizer) is an early
  implementation risk — if unavailable, fallback re-tokenization
  reintroduces the consistency risk the vanilla-fingerprint choice avoids.
- Team-mode gate currently locks team members to /test only — admitting
  /enrichment-review is a deliberate gate change (D-159 context), design
  section 4.

## 2026-07-24 — Development phase complete: slices 1–5, label branches, deployment hardening

(Catch-up entry covering the full development phase, 2026-07-20 → 2026-07-24,
27 commits `1407b36`..`3bdc4b4`.)

### Done this session
- **All five design slices implemented** (1407b36..4a93d5c): sira-query
  read endpoints + per-cell hot reload; web overlay store + edit API
  (flock, word records, cross-side fold parity test); the review page
  (chip grammar, stamp bar, HTMX partials); CORRECTIONS_DIR compose
  wiring (rw web, ro sira-query); exports — label × category report +
  prompt-fix scorecard.
- **Beyond-design feature: labels are branches** (a02223b): per-label
  serving variants on the service, `accepted-labels.json` merge log,
  admin merge-to-main / un-merge in the Labels drawer (team-mode
  `is_admin` gated); records never rewritten on merge — both directions
  instant and reversible.
- **Iterative fixes from early field use** (e22a37e..f5864a7): all
  requirements listed (not just enriched ones), free-form phrases +
  per-row reason/note, chip grammar polish, composite plan-stamp
  matching, 400+-row perf (CSS grid + content-visibility + lazy
  chunks), pending = overlay CONTENT digest (not mtime), per-plan
  pending dots.
- **Real-deployment hardening on the TEAM_MODE stack** (6cc0fae..b586f3b,
  all user-found): merge flags only touched plans; first-edit-after-label
  no longer wiped (projection-aware reload skip); newer add countermands
  a merged remove (strictly-newer-at wins, ties keep remove-wins);
  banner reflects the PRIMARY sira-query; reqs:records drawer badge;
  BM25 index-words column + model-named enrichments header +
  NORA_SIRA_ENRICH_MODEL_NAME override (run config.json records the
  CONFIGURED model, not the served one); Apply-all button (per-MNO
  staleness sweep across releases); single OOB pending banner; digest
  formula drops the merge log (un-merge flagged all MNOs' cells).
- **Docs**: distributable Enrichment Review Guide
  (`docs/enrichment-review-guide.md`, both roles + under-the-hood);
  web MODULE.md caught up (merge log surface, digest pending, Apply-all,
  two new invariants: labels-are-branches / Apply-never-publishes,
  digest formula lock).
- End-to-end verified by the user on the deployed stack: label
  branching, merge/un-merge/retract, countermand re-add, multi-release
  Apply-all, team-mode roles.

### In progress
- Nothing — code complete; strand ready to land.

### Next
- `/land-strand sira-enrichment-review` (drafts D-DRAFT-6..N promoted).
- Evaluation loop (pilot campaign → report → prompt fix → re-enrich →
  scorecard → retire label) moves to a NEW strand — deliberate scope cut.

### Flags
- D-DRAFT-4's "pending = overlay mtime > loaded_at" is superseded by the
  content-digest model (ade9f24, b586f3b) — correct at promotion time.
- Digest formula is cross-side locked (web store ↔ sira-query); any
  change must ship in BOTH images together or the banner wedges.
- Enrich-model header: run config.json records the CONFIGURED
  sglang.model, not the served model — NORA_SIRA_ENRICH_MODEL_NAME
  (operator-supplied) is the only reliable source (7594ac0).
- D-DRAFT-3 promotion notes: threshold "0.85, tunable" = code parameter
  only, no runtime knob wired; the design-time bm25x term-extraction
  risk flag resolved benignly (vanilla_tokens re-tokenizes corpus text
  with the index's OWN tokenizer — consistency preserved, corpus text
  pre-enrichment by construction).
