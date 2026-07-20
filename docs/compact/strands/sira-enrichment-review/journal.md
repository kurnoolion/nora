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
