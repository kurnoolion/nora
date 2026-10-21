# Phase 4 plan — store split, then publish

Strand `corp-db-layer` · v1 · 2026-10-21 · design: `store-split-design.md`.
Scope: 4a + 4b now; 4c (corp deployment) waits on storage-design §8 points 2 and 3.

## 4a — store split (local)

| # | Change |
|---|---|
| S1 | `req_fingerprint` in one shared module; computed at build; tests pin the normalization. |
| S2 | Cells v2: converter writes the vanilla index and `sira_phrases` (raw, per run), no effective set; `schema_version` 2. nora-store composes at load (§4) with the composed-index cache. Identity gate: v2 composed index == today's baked index on the same inputs. |
| S3 | `OverlayStore`: SQLite backend, one-time import of the per-MNO JSON corrections (provenance kept), fingerprint recorded per edit. nora-web writes through it; nora-store reads through it. Apply works in cell-db mode. |
| S4 | `TreeStore`: protocol; file backend over `out/parse` (today's `req_tree` logic moves behind it); SQLite backend and builder. |
| S5 | `HistoryStore`: builder from `TreeStore` across releases; SQLite; status and roll-ups. |
| S6 | `/stores/<store>/v1` routes and HTTP clients; tests that local and HTTP implementations return the same results. |

Checkpoint after S2–S3: rebuild, trial stack on Cells v2 + Overlay
store; golden Stage-1 equal to the current trial; review Apply works.

## 4b — publish (against a stand-in corp volume)

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

## Out of scope

Corp transfer, staging and first publish (4c); corp metrics/config DBs;
retiring flat build files.
