# team-eval-pilot — draft decisions

Draft decisions for this strand. Promoted to canonical `DECISIONS.md` with real
`D-XXX` IDs at `/land-strand` time.

---

## D-DRAFT-1 — Extend `feedback_db.py` rather than build a parallel `team_pilot/` module

**Context:** The merged /test page needed structured per-(question x lane)
event logging with score + categories + comment. A `core/src/web/feedback_db.py`
already existed (the legacy thumbs-up/down log) with an aiosqlite async pattern
matching the rest of the web layer, an `/api/test/feedback` endpoint already
wired, and a schema overlap of ~60% with the new fields needed. A first attempt
in this session built a parallel `core/src/team_pilot/` module (sync stdlib
`sqlite3`, separate file, separate tests) before the existing infrastructure
was discovered.

**Decision:** Delete the just-built `team_pilot/` scaffold; extend the
existing `feedback_db.py` (additive columns + new `record_user_feedback`
method) and update `playground.py` in place.

**Why:** Single source of truth for /test feedback; no parallel near-duplicate
schema for analysts to disambiguate later. Async-consistent with the rest of
`core/src/web/` — the sync stdlib `sqlite3` path the parallel module used
would have introduced an inconsistent pattern. The existing
`/api/test/feedback` endpoint pipeline is reused rather than parallelled.
Alternatives considered: (a) keep team_pilot/ parallel with its own DB file —
rejected as two-DB drift over time; (b) migrate `feedback_db.py` *into*
`core/src/team_pilot/` — rejected as a heavier change that touches the
working legacy feedback flow.

**Consequences:** The merged-tab fields live alongside the legacy
vote/free_form_feedback fields in one table. Module boundary for "test-page
feedback" stays in `core/src/web/`, not extracted. Future analyses query a
single table. If the pilot's needs ever diverge sharply from the legacy
feedback flow (e.g. different retention policy), the consolidation might be
revisited.

---

## D-DRAFT-2 — Additive schema migration (8 new nullable columns + PRAGMA-driven ADD COLUMN) over schema rewrite

**Context:** The legacy `test_feedback` schema (id, timestamp, section,
question, answer, citations_json, vote, free_form_feedback,
query_elapsed_ms, llm_model, metadata_json) was missing fields the merged
tab needs (lane, user_name, retrieved_ids, reranked_ids, cited_ids,
user_score, user_categories, lane_config). A redesign could have cleaned up
overlaps (e.g. citations_json vs cited_ids; metadata_json vs lane_config) at
the cost of migrating existing rows; the additive path keeps all legacy
data interpretable.

**Decision:** Add the 8 new columns to the existing `test_feedback` table,
all nullable. CREATE TABLE includes them for fresh DBs. For DBs created
before the merged tab existed, `FeedbackStore.initialize()` reads
`PRAGMA table_info` and runs ADD COLUMN for any missing column (idempotent
on re-run). The `lane` index is created after `_ensure_columns` because
SQLite can't index a column that doesn't exist yet — learned during testing.

**Why:** Back-compat: legacy rows stay queryable unchanged; the existing
flows (requirement_bot / sira_retrieval section URLs) keep working. No
data migration step required — DB upgrades transparently on the next
service start. The overlaps (citations_json + cited_ids; metadata_json +
lane_config) are accepted as the price of additive migration; analysis SQL
treats the new columns as authoritative for the merged tab and falls back
to the legacy columns for legacy rows.

**Consequences:** Two near-duplicate views of citations live in one table
(rich dicts in citations_json; flat req_id list in cited_ids). Schema is
slightly more sprawling. The "fresh DB has all columns; legacy DB
upgrades" path is exercised by tests, so the upgrade behavior is
reproducible. Future schema changes follow the same pattern: column
additions go to `_MERGED_TAB_COLUMNS` (or a successor list) +
`_ensure_columns` picks them up.

---

## D-DRAFT-3 — Two methods on FeedbackStore (`record_user_feedback` new, `record_feedback` unchanged) over overloading the legacy method

**Context:** The legacy `record_feedback(row_id, vote, free_form_feedback)`
always updates both columns; passing `vote=None` deliberately clears a
prior vote. The merged tab needs to set user_score + user_categories +
comment + user_name without touching `vote` (the merged tab doesn't expose
up/down). A unified method would have to distinguish "field not passed" from
"field passed as None" — typically via a sentinel object — to preserve the
legacy clear-vote semantics while letting the merged path leave vote alone.

**Decision:** Add a new `record_user_feedback(row_id, *, user_score,
user_categories, comment, user_name)` method for the merged tab. Leave
`record_feedback` exactly as it was. The merged method's SQL UPDATE
explicitly excludes the `vote` column (only touches user_score,
user_categories, free_form_feedback for the comment, and user_name via
COALESCE so a re-submit without a name doesn't blank it).

**Why:** Two methods read cleaner than a sentinel-based dispatch. The
legacy method's tests + its existing call site in `playground_feedback`
keep working with zero changes. Re-submits on the merged tab cleanly
overwrite the merged fields without ever clobbering a legacy vote that
might be on the row (defensive invariant tested explicitly).

**Consequences:** Two methods with overlapping concerns ("user feedback on
test-page row") will need parallel updates if either flow gains a new
common field. The `free_form_feedback` column is shared between flows
(legacy free-form comment + merged-tab comment) — accepted because the
semantic is the same.

---

## D-DRAFT-4 — `lane` as a new column distinct from `section`

**Context:** The existing `section` column identifies the user-facing
tab/route (`'requirement_bot'`, `'sira_retrieval'`). The merged tab needs
to record *which retrieval pipeline answered* for a given (question,
section='merged') pair. Overloading `section` with values like
`'merged_nora'`/`'merged_sira'` would have collapsed the two concepts into
one column.

**Decision:** Keep `section` as the tab/route identifier (now also accepts
`'merged'`) and add `lane` ('nora'|'sira', NULL for legacy rows) as a new
column. The merged tab inserts `(section='merged', lane='nora')` and/or
`(section='merged', lane='sira')` rows. The row-creation invariant is
**one row per checked lane** — both checked → two rows; one checked → one
row; none checked → form-level rejection.

**Why:** `section` is the *where the question was asked from*; `lane` is
the *which retrieval pipeline answered*. Mixing them would lose the
ability to run cross-lane comparisons cleanly (the headline analysis "where
did NORA outperform SIRA on the same question?" becomes a JOIN on `question`
filtered by `lane`, not a string-prefix decomposition).

**Consequences:** Two fields to set/check rather than one. Legacy rows
have `lane=NULL`, which is meaningful (the legacy tabs predate the merged
view, and didn't distinguish a "pipeline" from the tab itself). Adding a
third pipeline later (e.g. a `'nora-rerank-v2'` lane) needs a CHECK
constraint update in both schema and the Python validator — currently
hardcoded to `('nora', 'sira')`.

---

## D-DRAFT-5 — Template architecture: conditional include in `_answer.html` + per-lane pre-render to string + outer container

**Context:** `_answer.html` is 503 lines and rendered from nine code paths
across `playground.py`. The body is largely lane-agnostic (a small SIRA
preamble is conditioned on `sira_results is defined`); the bottom feedback
widget was lane-specific (vote up/down only). Building the merged tab
required two things: per-column rendering of the existing answer body (to
preserve display fidelity) AND a different feedback widget per column
(score + categories vs vote).

**Decision:** Two-part template structure:
- The feedback widget at the bottom of `_answer.html` was replaced with a
  conditional include (`{% if feedback_mode == 'merged' %}
  {% include "test/_feedback_merged.html" %}{% else %}
  {% include "test/_feedback_legacy.html" %}{% endif %}`). The legacy
  widget moved verbatim into `_feedback_legacy.html`. New
  `_feedback_merged.html` carries the score + categories + comment form.
- The merged container (`_merged_answer.html`) is a thin two-column
  Bootstrap wrapper that receives **pre-rendered HTML strings** per lane
  (computed in Python via a new `_render_template_to_string` helper that
  re-uses the shared Jinja2Templates env + root_path injection). Each
  lane's `_answer.html` is rendered with `feedback_mode='merged'` plus
  the lane-specific context, then composed into the container.

**Why:** The conditional include is a one-line change to `_answer.html` that
preserves display fidelity (none of the 500-line body is touched). Per-lane
pre-render-to-string was picked over Jinja `{% with %}` variable
enumeration because `_answer.html` consumes ~20 context variables — listing
them all in a `with` block per lane would be brittle and easy to drift. The
render-to-string path lets the endpoint own the per-lane context shape
without templates needing to know.

**Consequences:** Two feedback widget files instead of one. The merged tab
accepts some visual nesting (border-in-border, duplicate Q heading per
column) since `_answer.html` keeps its own wrapper — accepted for the pilot
as the strictest preservation of "current display content"; can be polished
later if team flags clutter. The render-to-string helper is small but
introduces a new pattern: per-lane fragments built in Python, composed in
Jinja. Used here, may be reused if other multi-pipeline comparisons land.

---

## D-DRAFT-6 — Merged-tab UX posture: responsive layout + fault-isolated lanes + replace tab nav

**Context:** Three small UX/reliability shape-decisions on the merged tab
that were each load-bearing for the pilot's usefulness:

- *Layout:* both lanes checked vs one checked is a meaningful UX state.
- *Reliability:* SIRA's pipeline can fail (service down, LLM timeout) while
  NORA still works (or vice versa). The Ask should still return useful
  output for the lane that succeeded.
- *Navigation:* the existing /test page had a section-tab nav
  (`requirement_bot`, `sira_retrieval`) that the merged tab would render
  obsolete from the team's perspective.

**Decision:**
- **Responsive layout** — `_merged_answer.html` sizes each column at
  `col-md-6` when both lanes are rendered, `col-md-12` when only one.
  Single-lane queries get full width rather than half-empty space.
- **Fault-isolated lane runners** — `asyncio.gather(return_exceptions=False)`
  (each runner catches its own exceptions and returns `{"error": "..."}`).
  In the merged branch, lanes with an error render a Bootstrap alert in
  their column; lanes that succeeded render normally + still insert their
  `test_feedback` row. One lane's failure cannot block the other.
- **Replace tab nav** — `index.html` no longer renders the section-tab UI.
  The merged form is the sole entry point from the UI. The legacy section
  values are still accepted by `/api/test/ask` (back-compat for direct
  POSTs / bookmarked URLs), but the page no longer generates them.

**Why:** Per the user's instruction ("I just want one tab"), the team's
mental model is one form with two lanes, not "pick a tab then ask." A
responsive layout makes the single-lane case feel intentional, not
half-broken. Fault isolation is the right posture for an evaluation tool
specifically — comparing whatever did come back is more useful than
nothing, and the lane error itself is data worth seeing.

**Consequences:** Legacy section URLs are unreachable from the UI; they
linger as endpoints only. Eventual cleanup pending if no bookmarks depend
on them (carried in journal Next). The fault-isolation pattern means a
silent partial failure is possible — a team member could read a half-empty
two-column result without noticing the error alert. Accepted: the alert is
visually loud (Bootstrap `alert-danger`), and analyses can spot rows where
one lane succeeded without the other.

---

## D-DRAFT-7 — Two distinct OpenAI-style rerank classes (chat + dedicated) over one parameterized class

**Context:** vLLM can serve cross-encoder reranking two different ways. With
`--task=reranker`, it exposes `/v1/rerank` (Cohere-shape: `documents` →
wrapped `{"results": [...]}`). With the default `--task=generate`, only
`/v1/chat/completions` is available, and reranking has to be encoded as a
chat-completion prompt that asks the model to return per-pair scores. Most
production deploys run chat-mode vLLM (for synthesis) and would have to spin
up a second vLLM instance to get the dedicated endpoint. NORA needed to
support both deployment shapes.

**Decision:** Two distinct provider classes in `core/src/query/reranker.py`:
`OpenAIRerankChat` (issues chat-completion requests with a structured
scoring prompt; supports `batch_size` to pack N pairs per LLM call) and
`OpenAIRerankDedicated` (calls `/v1/rerank` directly in one HTTP call).
Both selected via the existing `reranker_provider` enum dispatch in
`core/src/web/routes/query.py`. Wire formats, error modes, and test
surfaces are entirely separate.

**Why:** The two backends have fundamentally different latency profiles
(chat-completion is per-batch LLM inference; `/v1/rerank` is one bulk
HTTP call), different prompt/payload contracts, and different failure
modes (chat parses LLM text output and handles per-pair-id score
extraction; dedicated parses structured JSON). A single parameterized
class would conflate these behind a mode flag and force every test path
to know which mode is active. Alternative considered: one provider with
`mode="chat"|"dedicated"` flag → rejected because the implementations
share almost nothing operationally; the abstraction would be ornamental.

**Consequences:** Two enum entries for what users may perceive as
"OpenAI-style reranking." Operators must know which their vLLM exposes
(chat works against anything OpenAI-compatible; dedicated requires
`--task=reranker`). The naming convention (provider-style hyphenated)
was extended to `tei` (the fifth entry); a sixth single-call backend in
the same family would mean another distinct class unless a shared base
emerges naturally by then.

---

## D-DRAFT-8 — Unified `NORA_RERANK_BATCH_SIZE` knob across all rerank providers

**Context:** `OpenAIRerankChat` needs a batch-size knob to control how many
(query, doc) pairs are packed into one chat-completion call (LLM context
window + cost tradeoffs). The SIRA codebase had previously used
`NORA_SIRA_RERANK_BATCH_SIZE` — a SIRA-specific name from before unified
rerank infrastructure existed. With three new rerank providers landing in
close succession (chat, dedicated, then TEI), the choice was: each provider
gets its own env var, or one cross-provider knob.

**Decision:** Single `NORA_RERANK_BATCH_SIZE` env var, resolved through the
standard 4-tier precedence (env > config-page DB > `config/llm.json` >
built-in default 1). Wired into `OpenAIRerankChat`. Deprecated alias
`NORA_SIRA_RERANK_BATCH_SIZE` accepted with a deprecation warning at
startup so existing deploys' configs keep working without a hard cutover.

**Why:** The semantic of "how many chunks to score per HTTP call" is
identical across providers. Users care about latency/throughput
tradeoffs, not which provider is configured. One knob means switching
provider doesn't silently change behavior. The deprecation alias
preserves operational continuity. Alternative considered: per-provider
env vars (`NORA_OPENAI_RERANK_BATCH_SIZE`, `NORA_TEI_BATCH_SIZE`) →
rejected as needless surface area; provider-switching would force
re-tuning.

**Consequences:** `TEIReranker`'s `max_batch_size=32` constructor default
(matching TEI's `--max-client-batch-size` server default; see D-DRAFT-11)
is a separate concept from the user knob — user knob is "request batch
size NORA wants"; TEI's cap is "server's per-request limit." Currently
`NORA_RERANK_BATCH_SIZE` is NOT plumbed into `TEIReranker.max_batch_size`
(TEI's internal split runs independently), and the user-knob default of
1 wouldn't make sense for TEI. **TODO:** future cleanup — decide whether
`NORA_RERANK_BATCH_SIZE=N` should override TEI's `max_batch_size` (and
document the divergence in resolution semantics) or remain TEI-internal.

---

## D-DRAFT-9 — SSE-streamed per-lane progress via `fetch` + `ReadableStream` (POST-based SSE) over EventSource or polling

**Context:** The merged /test tab runs NORA and SIRA lanes in parallel;
each can take 30s–5min (synthesis is slow on the spark). Users sitting at
"Asking..." with no feedback for minutes couldn't distinguish broken from
slow. We needed real-time per-lane progress (which stage running, which
finished, where time is going).

**Decision:** Replace the form's `hx-post` with a JS submit handler that
calls `fetch` and reads the streaming response via `ReadableStream`. The
endpoint emits Server-Sent Events (`event:` / `data:` lines) as lanes
progress through retrieval / rerank / synthesis. A new `#progress-display`
card renders per-lane rows with a spinner, status badge, and rolling stage
message.

**Why:** Three options were on the table. (a) **Polling**: client polls a
status endpoint every 1-2s. Rejected — adds 1-2s latency on every state
change and doubles HTTP load. (b) **EventSource** (browser-native SSE
client): GET-only by spec. Our query is a POST with substantial body
(question + lane config + flags); EventSource would force splitting into
two requests (POST to register, GET to subscribe) or jamming the body
into query params. Rejected — splits state across two requests, makes
the "submit form" pattern leaky. (c) **`fetch` + `ReadableStream`**
(SSE-over-POST): one request carries body up and event stream down.
Works in all modern browsers; trivial to parse `event:` / `data:` lines
from the response stream.

**Consequences:** The web layer now has one place doing manual SSE
parsing on the client side. A second streaming use case should trigger
factoring this out into a reusable helper. Server-side streaming over
POST is non-idiomatic in some HTTP intermediaries — some proxies buffer
POST responses fully before forwarding. Fine on direct localhost
connections (current pilot deploy); **flag for any future load-balancer
or CDN scenario between user and NORA.** The `#progress-display` card is
currently merged-tab-only.

---

## D-DRAFT-10 — `TEIReranker` as a distinct fifth provider class, not a TEI-shape variant of `OpenAIRerankDedicated`

**Context:** TEI's `/rerank` and vLLM's `/v1/rerank` are both "single HTTP
call, query + documents, returns sorted indices+scores" at a 30,000-foot
view. They could have been collapsed into one parameterized class with a
`wire="cohere-tei"|"vllm-openai"` flag. But the differences are pervasive:
URL (`/rerank` vs `/v1/rerank`), request field (`texts` vs `documents`),
response shape (flat array vs wrapped `{"results": [...]}`), score field
(`score` vs `relevance_score`), and `model` field presence (TEI is
single-model per instance and doesn't accept the field).

**Decision:** Add `TEIReranker` as the fifth provider class in
`core/src/query/reranker.py` (after `huggingface`, `ollama`,
`openai-rerank-chat`, `openai-rerank-dedicated`). Independent payload
construction, response parsing, and error handling. Shares the
`Reranker` Protocol and graceful-passthrough contract but no
implementation inheritance.

**Why:** Adding TEI-shape support to `OpenAIRerankDedicated` would have
meant five distinct conditional branches threaded through one method
(payload-key naming, response-shape detection, score-field selection,
model-field omission, URL suffix), each exercised by only one backend. A
separate class isolates each backend's wire idiosyncrasies. Boilerplate
duplication (truncate, Bearer-token header, urllib error handling)
accepted because the boilerplate is short, well-tested, and divergent
enough across providers that sharing it would itself need conditionals.
Alternative considered: factor a `_BaseSingleCallReranker` mixin →
rejected as premature; revisit if a third single-call provider lands and
the abstraction becomes evident from concrete code.

**Consequences:** Some patterns (truncate, Bearer header, urllib error
handling) live in both `OpenAIRerankDedicated` and `TEIReranker`. If
they drift in non-trivial ways, both should be reviewed together. Only
the `Reranker` Protocol is enforced; any future shared helpers would be
opt-in mixins, not enforced inheritance.

---

## D-DRAFT-11 — Client-side auto-batching in `TEIReranker` (default `max_batch_size=32`) over server-side `--max-client-batch-size` raise

**Context:** TEI's `--max-client-batch-size` defaults to 32; NORA's RAG
pipeline routinely hands the reranker 50+ chunks. First end-to-end test
on dgx-spark-srv returned `422 "batch size 50 > maximum allowed batch
size 32"`. Two ways to fix: raise the server cap on the spark, or split
client-side. The server raise was offered as a one-line change in the
dgx-spark-srv compose; client-side split was ~60 lines of code.

**Decision:** `TEIReranker.rerank()` splits inputs into ≤`max_batch_size`
batches (constructor default 32, matching TEI's server default), fires
one HTTP call per batch, and merges scores globally via
sort-by-score-descending. A failed batch contributes no scores; its
chunks fall through to the unranked tail in input order, preserving the
size invariant (output length == input length even with partial failure).

**Why:** Client-side batching makes NORA portable across any TEI
deployment regardless of the server's `--max-client-batch-size` setting.
Server-side raise would couple NORA's correctness to a specific gateway
config — anyone else deploying TEI would hit the same 422 on the first
oversized request and have to coordinate with the spark admin.
Score-sort-merge is provably correct: cross-encoders score (query, doc)
pairs independently with no batch-relative normalization, so
concatenating scores across batches and sorting yields the same global
ranking a hypothetical single oversized call would have produced.
Alternative considered: raise the spark's cap to 100 → noted as a
separate latency optimization (fewer round trips) but not load-bearing;
can be done later as a perf tune. Recorded in journal Next.

**Consequences:** Two HTTP round trips for 50 chunks instead of one
(currently sequential — parallelizing them is a future ~2× perf win,
flagged in journal). `max_batch_size` is an internal class default, not
plumbed through `NORA_RERANK_BATCH_SIZE` (see D-DRAFT-8 TODO). If TEI
later bumps its default or admins raise it server-side, the constant in
the constructor stays at 32 (safe lower bound). Partial-batch failures
surface in the new INFO logs as `batch K/N — FAILED` — operators can
spot it without grepping for WARNING.

---

## D-DRAFT-12 — `retry-failed` subcommand on `sira_incremental.py` (stage-scoped re-process) over flag-on-main or external tool

**Context:** SIRA incremental ingestion runs in two stages (doc-enrich,
rerank), each writing per-(doc_id) or per-(query_id, doc_id) outcomes to
JSONL state files. When a subset of entries failed (LLM timeout,
transient HTTP error, malformed input), operators had three poor
options: (a) re-run the whole ingest — slow at corpus scale; (b)
hand-edit JSONL state to remove failed entries — error-prone; (c) build
a new tool — fragmented operations surface across binaries.

**Decision:** Add `retry-failed` as a subcommand of `sira_incremental.py`
(the same CLI as the main ingest). Flags: `--stage doc-enrich|rerank|both`,
`--include-all-filtered`. Implementation prunes JSONL state for the
targeted stage's failed entries (`_prune_jsonl_by_keys` + `_STAGE_FILES`
+ `_STAGE_KEY_FN` tables), then re-runs the normal incremental loop,
which now sees the failed entries as "absent" and re-processes them.

**Why (reasoning inferred from code; user to confirm):** Subcommand
structure colocates retry with the original ingest tool — operators
don't context-switch between binaries to fix a partial failure.
Stage-scoping lets operators target the specific failure without
re-running the cheap stage when only the expensive stage broke (or vice
versa). The `--include-all-filtered` flag covers the case where entries
were filtered out for legitimate reasons but the operator wants to retry
them anyway (e.g., upstream fix landed). Alternative considered: a
`--retry-failed` flag on the existing main subcommand → rejected because
retry semantics (prune-then-process) differ enough from fresh-ingest
semantics (process-only-new) that conflating them would muddy the main
loop. **TODO if rationale differs.**

**Consequences:** Two CLI surfaces now coexist on `sira_incremental.py`
— operators must know which subcommand to use for which task. The
retry path mutates state files in place (prunes failed-entry rows before
re-running); operators wanting to inspect failed entries before retry
must do so *before* invoking the subcommand. The `_STAGE_FILES` +
`_STAGE_KEY_FN` tables are the new authority for "which file holds which
stage's state and what's its primary key"; future stages must register
there.

---

## D-DRAFT-13 — `TEIEmbedder` uses TEI's native `/embed` shape over the OpenAI-compatible `/v1/embeddings`

**Context:** TEI ≥1.5 exposes two embedding routes — its native
`POST /embed` with `{"inputs": [...]}` returning array-of-arrays, and an
OpenAI-shape `POST /v1/embeddings` with `{"input", "model"}` returning
`{"data": [{"embedding": [...]}]}`. The operator's probe
(`probe_tei`) confirmed both worked on their deployment:
`openai-embedding: dim=1024, 51ms`; `tei-embedding: dim=1024, 6ms`.
Roughly 8× faster on the native shape.

**Decision:** `TEIEmbedder` posts to `{base_url}/embed` in TEI's
native shape; OpenAI-compat path is documented in `probe_tei.py` as
available but not used. If a future deployment needs OpenAI-compat
(TEI version too old, or pointing at a non-TEI server), a separate
`OpenAIEmbedding` provider can land then — same precedent set by the
rerank providers (separate `openai-rerank-*` classes alongside `tei`).

**Why:** ~8× per-call latency win matters at embedding volumes
(whole-corpus ingestion at thousands of chunks; per-query embedding
at runtime). Smaller request/response payload (no `model` field, no
wrapper). Symmetric with `TEIReranker` from the prior session, which
used the same reasoning to land as a distinct class. Alternative
considered: OpenAI-compat as the "portable" shape that also works
against vLLM-embedding endpoints — rejected because (a) NORA doesn't
currently target vLLM embedding, (b) when it does, an
`OpenAIEmbedding` class can be added in parallel rather than
retrofitted on top of `TEIEmbedder`'s native-shape internals.

**Consequences:** `TEIEmbedder` only works against TEI servers; not
portable to other OpenAI-compat embedding endpoints. If TEI changes
its native wire shape in a future major version, the class needs
updating (the OpenAI-compat path would not). The provider family now
has two native-shape members (`TEIEmbedder`, `TEIReranker`) and three
OpenAI-style members (`OpenAIRerankChat`, `OpenAIRerankDedicated`,
`ollama` reranker) — the asymmetry is real and documented in the
class docstrings.

---

## D-DRAFT-14 — SIRA per-stage routing maintained as patch files under `sandbox/sira_patches/`, not a NORA-owned SIRA fork

**Context:** SIRA is cloned to `sandbox/sira/` and gitignored —
committing into the clone is not an option without forking upstream.
The per-stage routing change touches four upstream scripts
(`scripts/{add_doc_index_adapter,enrich_query_and_retrieve,llm_reranking,run_pipeline}.py`).
Two persistent paths: maintain a NORA-owned SIRA fork (gives full
version control, costs ongoing upstream-sync work), or carry patches
in NORA and apply on demand.

**Decision:** Patches live as unified diffs under
`sandbox/sira_patches/*.patch` and apply via
`sandbox/install_configs.sh` against the local SIRA clone.
Idempotency via sentinel-grep on a unique string from the patch
(currently `NORA_SIRA_ENRICH_LLM_URL`). The clone itself stays
gitignored and upstream-trackable via plain `git -C sandbox/sira
pull`.

**Why:** No upstream-divergence maintenance burden — pulling fresh
SIRA + re-running `install_configs.sh` is the upgrade path. The
clone stays a clean snapshot of the upstream HEAD the operator
chose, making it easy to bisect against upstream when an upstream
regression bites. Alternative considered: a NORA-owned fork →
rejected because the SIRA strand is treated as integration code,
not as upstream we want to evolve. We need flexibility to add
NORA-specific behavior without committing to long-term upstream-sync
work.

**Consequences:** Patches must be manually re-conflict-resolved when
upstream SIRA changes the patched call sites; the `git apply
--check` bail-out in `install_configs.sh` is the operator's only
line of defense. Sentinel-grep idempotency is fragile when patches
evolve (the documented operational footgun: an old patch's sentinel
hides the extended patch's new content from being applied — operator
must `git checkout -- scripts/` first). The clone's gitignored
status means changes there don't show in `git status` from NORA's
root — operators can't see at a glance whether a stale patched
state is sitting in the clone.

---

## D-DRAFT-15 — Single-knob `NORA_SIRA_*_LLM_TIMEOUT` collapses aiohttp `total` + `sock_read`

**Context:** SIRA's pre-patch `ClientTimeout(total=300.0,
sock_read=60.0)` has two independent timeout values. The 60s
`sock_read` was the actual cut-off surprise for slow LLMs (a request
that takes >60s before the first byte arrives gets dropped, even
though `total=300` would otherwise let it run). Adding env-var
control could expose one knob, two knobs, or four knobs (per-stage ×
per-axis).

**Decision:** One env var per stage
(`NORA_SIRA_ENRICH_LLM_TIMEOUT`, `NORA_SIRA_RERANK_LLM_TIMEOUT`);
its value is applied to **both** `total` and `sock_read`. Defaults
to 300s to match SIRA's pre-patch `total`. Each stage logs the
resolved value at startup as `LLM timeout: total=Xs sock_read=Xs`.

**Why:** Most users want the two values equal — the slow-LLM use
case is "give the request a long time," not "let the connection sit
idle for X but kill it overall after Y." Two separate knobs would
be more API surface for little operational benefit. Default of 300
(not 60) was chosen because it's the more permissive number of
SIRA's defaults and users tuning concurrency / timeouts should not
be silently downgrading to 60s. Alternative considered: separate
`*_TIMEOUT_TOTAL` and `*_TIMEOUT_READ` env vars → rejected as
needless surface area; the asymmetric default (`total=300`,
`sock_read=60`) in SIRA's upstream was likely an oversight, not a
deliberate design.

**Consequences:** Cannot independently tune `total` vs `sock_read`
without a follow-up patch. If a future use case needs that (e.g.
fast-establishing connection that streams slowly), the env var pair
would split into two — keeping back-compat would require the
single-knob name to mean "both," and new `_TOTAL` / `_READ` knobs
to override. Patching `llm.py`'s `post_chat` to also become
env-var-aware could provide finer-grained per-call control, but is
out of scope for this patch.

---

## D-DRAFT-16 — `NORA_SIRA_RERANK_LLM_*` env-var family shared between batch pipeline and runtime service

**Context:** Earlier this strand-series, `sandbox/sira_query/service.py`
(NORA's runtime SIRA query service) defined `NORA_SIRA_RERANK_LLM_URL
/ _MODEL / _API_KEY` to route the runtime rerank stage to a faster
LLM than the proprietary one used for enrichment. The new
batch-pipeline routing patch needed env vars for the same purpose —
rerank to a different endpoint than enrichment. Two options: pick
fresh names (e.g. `NORA_SIRA_BATCH_RERANK_LLM_*`) to keep batch and
runtime decoupled, or reuse the existing names.

**Decision:** Reuse the same `NORA_SIRA_RERANK_LLM_URL / _MODEL`
names. Both the batch-pipeline scripts (via the patch) and the
runtime service (`sandbox/sira_query/service.py:64-66`) read from
the same env vars. Setting the env var routes both code paths
consistently.

**Why:** Operators don't think in "batch vs runtime" boundaries —
they think in "where do I want SIRA's rerank LLM to live?" One env
var pair answers that question for both contexts.
Single-source-of-truth: if you set
`NORA_SIRA_RERANK_LLM_URL=http://localhost:11434/v1` in your shell,
both the batch eval pass and the runtime query service hit Ollama.
No drift possible between the two. Alternative considered: separate
prefixes → rejected because no scenario was found where you'd
actually want them to differ; if one does emerge, splitting later
is cheap (env var = config field, easy refactor).

**Consequences:** Cannot route batch and runtime rerank to different
LLMs via env vars alone — they share. If a future need emerges
(e.g. batch evaluates against the same LLM as production but
runtime A/B-tests a candidate replacement), the env vars need a
per-context override pattern. The decision presumes operators want
consistency over flexibility, which has held so far in this
strand's deployments. The batch pipeline does NOT yet have a
`NORA_SIRA_RERANK_LLM_API_KEY` reader (the runtime service does);
pure oversight — header injection isn't currently needed for this
strand but should land if it ever does.

---

## D-DRAFT-17 — Patch SIRA's hardcoded `127.0.0.1` at the per-call site, not by adding a `sglang.host` config field

**Context:** SIRA hardcodes `127.0.0.1` in four places —
`scripts/run_pipeline.py:261, 387` (reachability check / spawn) and
`scripts/{add_doc_index_adapter,enrich_query_and_retrieve,llm_reranking}.py`
(URL construction). Removing the localhost constraint could be done
two ways: (a) add a config field `sglang.host` defaulting to
`127.0.0.1`, all call sites read `cfg.sglang.host`; (b) introduce
env-var-driven URL overrides per stage, leaving the localhost
default in place when env vars are unset.

**Decision:** Option (b) — env-var-driven per-stage URL overrides.
The `127.0.0.1` literal stays in the code as the fallback; env vars
route around it when set. No new Hydra config field added.

**Why:** Per-stage routing is the real operator need — enrichment
to one LLM, rerank to a different (typically faster) one. A single
`sglang.host` knob couldn't express that. Adding both (a
`sglang.host` knob AND per-stage env vars) is more total surface
area than needed. The env-var approach also makes the
`run_pipeline.py` precheck/spawn skip clean: if either env var is
set, SIRA's own sglang spawn is unwanted; the guard reads the env
vars directly without touching Hydra config. Alternative
considered: add `sglang.host` AND `sglang.{enrich,rerank}_host`
per-stage overrides — rejected as Hydra-config gymnastics for what
env vars handle in 8 lines per call site.

**Consequences:** The four patched call sites must stay in sync as
patches evolve — if SIRA upstream renames a script or refactors the
URL construction, the patch needs updating in four places. The
`127.0.0.1` literal becomes dead code in env-routed setups (still
present, never reached). Operators who want to point SIRA at a
non-localhost endpoint without per-stage routing now have no path
other than setting both env vars to the same URL — which works
fine but is mildly awkward.
