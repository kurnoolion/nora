# decision

**Purpose**
Decision models — typed `bool` / `score` / `choice` inference with no token generation. A decision model has a classification head rather than a token decoder, so it answers in a single forward pass and its latency does not scale with the number of options. This module owns the abstraction and the query-classification application of it. Strand jev-experimentation; design `docs/compact/strands/jev-experimentation/decision-model-design.md`.

Separate from [llm](../llm/MODULE.md) deliberately: a different abstraction (typed heads, not completions), and one-package-per-concern is the convention here.

**Public surface**
- `DecisionProvider` (base.py) — `@runtime_checkable Protocol`; the only decision-model interface the rest of the project sees. Attribute `model`, method `decide(state, questions) -> list[DecisionAnswer]`. Shaped on `/v1/systemone`: a batch of typed questions evaluated in parallel against one shared `state`. Implementations may be HTTP clients, in-process adapters, or mocks — a candidate without a `/v1/systemone` endpoint satisfies this as a thin adapter rather than an HTTP hop.
- `DecisionQuestion(question_id, kind, question, options=())` (base.py) — frozen. `kind` is `bool` | `score` | `choice`; `options` is required for `choice` and rejected for the others, validated at construction.
- `DecisionAnswer(question_id, value, confidence=0.0)` (base.py) — frozen. Matched back to its question by `question_id`, never by position, so a provider may reorder or parallelise. `confidence` is the calibrated probability where the provider reports one; recorded, not yet acted on — an abstain threshold is a later decision.
- `DecisionKind` (base.py) — `Literal["bool", "score", "choice"]`.
- `DecisionError` (base.py) — a provider could not answer. Callers degrade rather than fail.
- `SystemOneDecisionProvider(base_url, model="", api_key="", timeout=30)` (systemone_provider.py) — HTTP client for an in-corp `/v1/systemone` endpoint; stdlib `urllib` only, no SDK, the posture that kept `OpenAICompatibleProvider` vendor-neutral. `model`, `call_count`, `last_latency_ms`. **The wire schema is UNVERIFIED against a live server** — no endpoint was reachable when it was written — so the parse fails loudly and names the keys the server actually sent, making the first real run a schema correction rather than a silent mis-mapping. An empty `model` is omitted from the payload, meaning "the server's own default".
- `assert_decision_head_present(model_dir) -> dict`, `inspect_weights(model_dir)`, `confidence_is_calibrated(answers)`, `REQUIRED_HEAD_FILES`, `ADAPTER_MARKERS` (head_check.py) — the design §7 gate. A head-bearing distribution must carry `head.safetensors`, `calibration.json` and a decision adapter; a GGUF-only directory raises with the hollow-build diagnosis. Returns a names-only report (no file contents), safe to log and to stamp on a run. `confidence_is_calibrated` is the complementary signal a *client* can see — an all-zero confidence vector is the shape a stripped build produces — and informs a warning, never a hard failure.
- `MockDecisionProvider` (mock_provider.py) — deterministic keyword stub, offline, no network; `model` (`"mock"`), `call_count` (requests, not questions). What the `decision` knob value resolves to when no real provider is configured, so the seam is exercisable with nothing installed. `choice` returns the first option named in the state, else the LAST option; `bool` is true when a non-stopword capitalised token of the question appears in the state; `score` is a constant 0.5.
- `HybridQueryAnalyzer(provider, fallback=None)` (analyzer.py) — `analyze(query) -> QueryIntent`. Decision model for `query_type` and `doc_type_scope`; the rule-based analyzer for everything else. `call_count` and `fallback_count` — queries served, and queries served entirely from the rules because the provider raised.
- `QUERY_TYPE_QUESTION`, `DOC_SCOPE_QUESTION`, `CLASSIFICATION_QUESTIONS` (analyzer.py) — the two choice questions sent in one request, options generated from `QueryType` and `DocTypeScope`.

Selected by the three-valued `NORA_QUERY_ANALYZER` knob — see [query](../query/MODULE.md)'s `make_query_analyzer_from_knob` and `env.config.resolve_query_analyzer` / `resolve_decision_provider`.

**Invariants**
- **No external egress.** Every decision provider is self-hosted in-corp. Phase 2 ships no HTTP transport at all — importing this package cannot cause a network call.
- **Option lists are generated from their enums, never hand-written.** A hand-listed option set is exactly how `LLMQueryAnalyzer`'s prompt came to omit `summarize` and `fact` (design §2.1). Adding a `QueryType` member extends the question with no edit here.
- **`QueryIntent` is the contract and does not change.** `HybridQueryAnalyzer` returns the same shape the rule-based analyzer does; a test asserts the field sets match.
- **A provider failure degrades, never fails.** Any exception from a provider falls back to the rule-based `QueryIntent` whole; an answer that is absent or not a member of its enum falls back **per field**, so one bad answer does not discard a good one. A classifier outage degrades classification; it does not fail the query.
- **Answers are matched by `question_id`.** Never by list position.
- **A measurement never gets recorded against an unverified head.** Two gates, because the same silent degradation arrives two ways. `assert_decision_head_present` covers the weights: a GGUF or backbone-only build answers without erroring, so a number recorded against it measures the backbone. `fallback_count` covers the transport: per-query fallback is the right *serving* behaviour but makes a dead provider look like a successful run with the rule-based numbers — `eval.classify_cli` refuses any non-zero count under a `decision` label. Found the hard way during phase 3: a verified head plus an unreachable server reported the rules baseline, 0.720 / 0.136, as the decision arm's result.
- **Free-text fields are not expressible here.** A decision model emits only `bool` / `score` / `choice`, so `entities`, `concepts`, `releases` and `standards_refs` stay with the regex extractors permanently — not as a phase limitation but as a property of the category.

**Key choices**
- `/v1/systemone` as the wire shape, not a bespoke interface: it is the de-facto decision-model format, so a candidate swap is a config change. Same reasoning that kept the LLM roster vendor-neutral behind `/v1/chat/completions` with no SDK dependency.
- `HybridQueryAnalyzer` composes `MockQueryAnalyzer.analyze()` rather than reusing its private `_extract_*` methods. One call fills every open-extraction field and yields a complete fallback value for free; the wasted rule classification costs microseconds. This also means `query` imports `decision` only function-locally (in `make_query_analyzer`), since `decision` imports `query` at module level — that function-local import is what breaks the cycle.
- Both classification questions travel in one `decide()` call. Parallel questions against one shared state is the mechanism the wire format exists for, so two round trips would waste it.
- The three closed-vocabulary multi-selects (`mnos`, `likely_features`, `plan_ids`) are decision-model-ownable as one `bool` question per candidate — 24 booleans plus 2 choices against one state, well inside the concurrency these servers document — but stay on the rules until phase 3 so the first measurement has one variable. A test asserts `CLASSIFICATION_QUESTIONS` still has exactly two entries, so adding them is a deliberate act.

**Depends on**
[query](../query/MODULE.md) (`MockQueryAnalyzer` for the open-extraction fields and the fallback; `QueryIntent` / `QueryType` / `DocTypeScope` as the contract).

**Depended on by**
[query](../query/MODULE.md) (`make_query_analyzer` constructs `HybridQueryAnalyzer`). This edge is **function-local**, not a module-level import — the two modules would otherwise form a cycle, and the local import in the factory is what breaks it.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`analyzer.py`
- `CLASSIFICATION_QUESTIONS` — constant — pub
- `DOC_SCOPE_QUESTION` — constant — pub
- `HybridQueryAnalyzer` — class — pub — Decision model for the choice fields, regex for the spans.
- `HybridQueryAnalyzer.analyze` — method — pub — Analyze a query into structured intent.
- `QUERY_TYPE_QUESTION` — constant — pub
- `_coerce` — function — internal
- `logger` — constant — pub

`base.py`
- `DecisionAnswer` — class — pub — One answer.
- `DecisionError` — class — pub — A decision provider could not answer.
- `DecisionKind` — constant — pub
- `DecisionProvider` — class — pub — The only decision-model interface the rest of the project sees.
- `DecisionProvider.decide` — method — pub — Answer every question against one shared state.
- `DecisionQuestion` — class — pub — One typed question against a shared state.

`head_check.py`
- `ADAPTER_MARKERS` — constant — pub
- `REQUIRED_HEAD_FILES` — constant — pub
- `assert_decision_head_present` — function — pub — Raise unless ``model_dir`` holds a head-bearing distribution.
- `confidence_is_calibrated` — function — pub — Whether the answers look like they came from a calibrated head.
- `inspect_weights` — function — pub — Describe what a weights directory contains, without judging it.
- `logger` — constant — pub

`mock_provider.py`
- `MockDecisionProvider` — class — pub — Keyword-matching stub.
- `MockDecisionProvider.decide` — method — pub
- `_MOCK_SCORE` — constant — internal
- `_QUESTION_STOPWORDS` — constant — internal
- `logger` — constant — pub

`systemone_provider.py`
- `SystemOneDecisionProvider` — class — pub — Decision provider over an in-corp `/v1/systemone` endpoint.
- `SystemOneDecisionProvider.decide` — method — pub — Answer every question against one shared state in one request.
- `SystemOneDecisionProvider._parse` — method — internal
- `logger` — constant — pub
<!-- END:STRUCTURE -->
