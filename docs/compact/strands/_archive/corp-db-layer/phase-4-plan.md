# Phase 4 plan — store split, then publish

Strand `corp-db-layer` · v1.1 · 2026-10-21, status 2026-10-24 · design:
`store-split-design.md`, `publish-design.md`.
Scope: 4a + 4b (both **done**); 4c (corp deployment) is a later strand.

**Outcome 2026-10-23:** every nora stack serves the four-store design
live from its own registry, with the Overlay authority on SQLite;
retrieval provably unchanged on each (expansion-off Stage-1, 0 of 39
differ, 0 knob values changed). The strand's goal — nora's corp data
layer ready and in use on every stack — is met; what remains is host
retirement of the flat path after a soak, and landing the strand.

## 4a — store split (local) — done 2026-10-22

| # | Change |
|---|---|
| S1 | `text_fp` (was `req_fingerprint`) in one shared module, canonical form v2, versioned values; computed at build; tests pin the normalization. |
| S2 | Cells v2: converter writes the vanilla index and `sira_phrases` (raw, per run), no effective set; `schema_version` 2. nora-store composes at load (§4); no cache (measured, D-DRAFT-23). Identity gate: v2 composed index == today's baked index on the same inputs. |
| S3 | `OverlayStore`: SQLite backend, one-time import of the per-MNO JSON corrections (provenance kept), fingerprint recorded per edit. nora-web writes through it; nora-store reads through it. Apply works in cell-db mode. |
| S4 | `TreeStore`: protocol; file backend over `out/parse` (today's `req_tree` logic moves behind it); SQLite backend and builder. |
| S5 | `HistoryStore` (design §6a): builder from `TreeStore` across an MNO's releases computing `node_fp` / `subtree_fp`; version intervals + content-addressed text in SQLite; the six query kinds. |
| S6 | nora-store mounts `/stores/{cells,trees,history,overlay}/v1` (Overlay with writes); HTTP clients for all four chosen by `NORA_<STORE>_URL`; nora-web uses the clients (file backends = dev fallback); peer-store Overlay read; schema-tolerance tests; parity tests local vs HTTP. Checkpoint: trial's nora-web without corrections/parse mounts, review edits and Apply through the store. |

Checkpoint after S2–S3: rebuild, trial stack on Cells v2 + Overlay
store; golden Stage-1 equal to the current trial; review Apply works.

## 4b — publish (against a stand-in corp volume) — design: `publish-design.md`

Status 2026-10-23: B1–B8 done (`core/src/stores/registry.py`,
`publish.py`; nora-store registry mode; `core/tests/test_stores_publish.py`,
`core/src/store_service/test_registry_mode.py`; B8 on trial, publish-design §9.10).
Roll-out the same day: trial, then both A/B stacks, each publish → gate
→ flip at 0 of 39 differ; the Overlay authority and trial onto the
SQLite Overlay (publish-design §9.11). Stale label pointers retired from
the wiring envs. Still on the host, after a soak: the env backups, the
three pre-4a image sets, the label directories (flat cell inputs stay
until the next build cycle).

| # | Change |
|---|---|
| B1 | `corpus_publish`: publishes Cells and Trees per cell, rebuilds History per MNO, deterministic fingerprints, skips unchanged units. |
| B2 | `registry.db`: `store_versions`, `active`, publish log. |
| B3 | Stage with checksum + temp-name rename; transfer pluggable (local copy now). |
| B4 | Flip refuses without a recorded passing golden run (`--force --reason` overrides). |
| B5 | Flip and rollback, one transaction per release. |
| B6 | Services load the active versions from `registry.db` (`NORA_STORE_REGISTRY`). |
| B7 | GC: keep active + N previous. |
| B8 | Local end-to-end on trial: publish → stage → gate → flip → rollback. |

## Out of scope (carried to later strands)

- **4c corp:** the remote `Transfer`, the first publish into the corp
  volume, the corp backend for the registry and Overlay (SQLite vs
  Postgres), corp metrics/config DBs.
- **Runtime DBs and golden set** (jobs, metrics, config, feedback,
  samples and runs) behind nora-store; today nora-web's own files.
- **Retiring flat build files:** the pipeline keeps writing the env-dir
  layout; the flat SIRA cells remain the converter's input.
