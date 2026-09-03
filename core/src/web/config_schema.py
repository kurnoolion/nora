"""Schema describing the Config page's editable knobs.

This is hand-curated rather than auto-derived from the dataclasses
because not every field on `LLMConfigFile` / `RetrievalConfig` should
be user-editable through a UI (some are documented-only metadata,
others are too low-level / risky to change at runtime).

Each `ConfigField`:
  - module: storage key — matches the resolver-chain section
    ("llm", "retrieval", "pipeline")
  - key: storage key + dataclass attribute name on the cached config
    instance (where applicable)
  - label: human-readable form label
  - kind: input type — "bool" / "string" / "int" / "float" / "enum"
    / "password"
  - choices: enum values (only used when kind="enum")
  - category: "feature" (toggle), "value" (model name / URL), or
    "tunable" (numeric tuning param). Drives layout grouping on the
    Config page.
  - help: one-line description rendered as helptext under the input
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConfigField:
    module: str
    key: str
    label: str
    kind: str
    category: str  # "feature" | "value" | "tunable"
    help: str = ""
    choices: list[str] = field(default_factory=list)
    value_kind: str = ""
    """For kind='dict_by_query_type': the type of each cell —
    'float' / 'int' / 'bool'. Drives form input rendering and
    coercion of the per-type rows. Empty for non-map fields."""


@dataclass
class ConfigSection:
    module: str
    title: str
    description: str
    fields: list[ConfigField] = field(default_factory=list)


# ── LLM + embedding section ────────────────────────────────────


_LLM_FIELDS: list[ConfigField] = [
    # Values
    ConfigField(
        module="llm", key="llm_provider",
        label="LLM Provider", kind="enum", category="value",
        choices=["", "ollama", "openai-compatible", "mock"],
        help=(
            "Backend used for query synthesis. 'openai-compatible' "
            "covers OpenAI / OpenRouter / Together / DeepInfra / "
            "vLLM / proxy frontends that expose /v1/chat/completions."
            " NOTE: does not affect Ask-page synthesis while a named provider roster is configured in llm.json — a selected roster entry owns its own settings."
        ),
    ),
    ConfigField(
        module="llm", key="llm_model",
        label="LLM Model", kind="string", category="value",
        help=("Provider-specific model name (e.g. 'gemma3:12b', "
              "'qwen/qwen3-235b-a22b'). NOTE: does not affect Ask-page synthesis while a named provider roster is configured in llm.json — a selected roster entry owns its own settings."),
    ),
    ConfigField(
        module="llm", key="llm_base_url",
        label="LLM Base URL", kind="string", category="value",
        help=(
            "OpenAI-compatible API root, ending in /v1. "
            "(Ignored when provider is 'ollama'.)"
            " NOTE: does not affect Ask-page synthesis while a named provider roster is configured in llm.json — a selected roster entry owns its own settings."
        ),
    ),
    ConfigField(
        module="llm", key="llm_api_key",
        label="LLM API Key", kind="password", category="value",
        help=(
            "Bearer token for OpenAI-compatible endpoints. "
            "Pass any non-empty string for native-Ollama proxies."
            " NOTE: does not affect Ask-page synthesis while a named provider roster is configured in llm.json — a selected roster entry owns its own settings."
        ),
    ),
    ConfigField(
        module="llm", key="llm_timeout",
        label="LLM Timeout (s)", kind="int", category="tunable",
        help=("Per-request timeout in seconds. 0 = use default (600)."
              " NOTE: does not affect Ask-page synthesis while a named provider"
              " roster is configured in llm.json — a roster entry carries its"
              " own `timeout`."),
    ),
    ConfigField(
        module="llm", key="embedding_provider",
        label="Embedding Provider", kind="enum", category="value",
        choices=["", "sentence-transformers", "huggingface", "ollama"],
        help=(
            "Backend used to embed query text and corpus chunks. "
            "MUST match the model the vectorstore was built with — "
            "see RETRIEVAL.md §10.1."
        ),
    ),
    ConfigField(
        module="llm", key="embedding_model",
        label="Embedding Model", kind="string", category="value",
        help=(
            "Provider-specific name. Examples: 'all-MiniLM-L6-v2', "
            "'qwen3-embedding:4b-q8_0', 'nomic-embed-text'."
        ),
    ),
    # Features
    ConfigField(
        module="llm", key="skip_taxonomy",
        label="Skip Taxonomy Stage", kind="bool", category="feature",
        help=(
            "Skip the LLM-driven taxonomy/feature-extraction stage of "
            "the pipeline. Trades feature-aware retrieval for a "
            "reproducible graph topology."
        ),
    ),
    ConfigField(
        module="llm", key="skip_graph",
        label="Skip Graph Stage", kind="bool", category="feature",
        help=(
            "Skip the knowledge-graph stage entirely. Query path falls "
            "back to RAG-only retrieval (a stub graph is built from "
            "vectorstore metadata)."
        ),
    ),
    ConfigField(
        module="llm", key="reranker_enabled",
        label="Cross-Encoder Reranker", kind="bool", category="feature",
        help=(
            "Attach a sentence-transformers cross-encoder reranker to "
            "the query pipeline. Reranks the RRF-fused top-K against "
            "the query before diversity / context build. Per-query-"
            "type gating still applies (FACT / CROSS_DOC / etc. on; "
            "SUMMARIZE / GENERAL off — see _TYPE_RERANK_ENABLED). "
            "False keeps the MockReranker passthrough (current "
            "default; no reranking)."
        ),
    ),
    ConfigField(
        module="llm", key="reranker_model",
        label="Reranker Model", kind="string", category="value",
        help=(
            "Model id / name. For provider=huggingface: HuggingFace "
            "cross-encoder id (BAAI/bge-reranker-base, BAAI/bge-"
            "reranker-large, cross-encoder/ms-marco-MiniLM-L6-v2) or "
            "local filesystem path. For provider=ollama: name of an "
            "Ollama-pulled reranker (e.g. bbjson/bge-reranker-base:"
            "latest). Empty = built-in default."
        ),
    ),
    ConfigField(
        module="llm", key="reranker_provider",
        label="Reranker Provider", kind="enum", category="value",
        choices=[
            "huggingface", "ollama",
            "openai-rerank-chat", "openai-rerank-dedicated", "tei",
        ],
        help=(
            "Backend to instantiate when the reranker is enabled. "
            "'huggingface' loads via sentence_transformers from HF "
            "cache (requires the model files locally OR HF Hub "
            "access at startup). 'ollama' calls a local Ollama "
            "server — use this on proxy-restricted machines that "
            "have Ollama running but no HF access. "
            "'openai-rerank-chat' calls an OpenAI-compatible "
            "/v1/chat/completions endpoint (e.g. vLLM) with a "
            "per-pair scoring prompt — works with any instruct "
            "model; slower (one call per chunk). "
            "'openai-rerank-dedicated' calls an OpenAI-compatible "
            "/v1/rerank endpoint (vLLM's dedicated reranker route) "
            "for batch ranking — requires the server to expose "
            "/v1/rerank with a cross-encoder model loaded; fast "
            "(one batched call). "
            "'tei' calls a HuggingFace TEI (Text Embeddings "
            "Inference) /rerank endpoint (Cohere-style: body uses "
            "'texts', response is a flat array) — for the standalone "
            "tei-reranker service in the dgx-spark-srv stack. "
            "Empty = default 'huggingface'."
        ),
    ),
    ConfigField(
        module="llm", key="reranker_ollama_url",
        label="Reranker Ollama URL", kind="string", category="value",
        help=(
            "Base URL of the local Ollama server for "
            "provider=ollama. Defaults to http://localhost:11434 "
            "when blank. Ignored for huggingface / openai-rerank-* "
            "providers (see Reranker Base URL below for the "
            "OpenAI-compatible variants)."
        ),
    ),
    ConfigField(
        module="llm", key="reranker_base_url",
        label="Reranker Base URL", kind="string", category="value",
        help=(
            "Base URL of the reranker endpoint when "
            "provider='openai-rerank-chat' or "
            "'openai-rerank-dedicated'. Same shape as the LLM Base "
            "URL — point at vLLM, SGLang, or any OpenAI-"
            "compatible server. The /v1 prefix is appended by the "
            "reranker code, so set this to the server root (e.g. "
            "http://<dgx-spark>:8000). Ignored for huggingface / "
            "ollama providers."
        ),
    ),
    ConfigField(
        module="llm", key="reranker_api_key",
        label="Reranker API Key", kind="password", category="value",
        help=(
            "Bearer token for the reranker endpoint when "
            "provider='openai-rerank-chat' or "
            "'openai-rerank-dedicated'. Sent as "
            "'Authorization: Bearer <key>'. Leave blank for "
            "endpoints that don't require auth (e.g. local vLLM)."
        ),
    ),
    ConfigField(
        module="llm", key="reranker_batch_size",
        label="Reranker Batch Size", kind="int", category="tunable",
        help=(
            "Number of (query, document) pairs to score per LLM API "
            "call when provider='openai-rerank-chat' (and the SIRA "
            "per-query rerank stage, which honors the same knob). "
            "Default 1 = per-call mode (one HTTP request per chunk; "
            "robust, slow). N > 1 = batch mode (pack N chunks into "
            "one request, model returns a JSON array of scores; "
            "fast, slightly less robust — a single bad LLM call "
            "zeros the whole batch). Empirically 5–10 is the "
            "practical sweet spot on long-context LLMs; smaller is "
            "safer on output-token-limited models. Ignored for "
            "huggingface / ollama / openai-rerank-dedicated "
            "providers (those have their own batching)."
        ),
    ),
]


# ── Retrieval section ──────────────────────────────────────────


_RETRIEVAL_FIELDS: list[ConfigField] = [
    # Features
    ConfigField(
        module="retrieval", key="enable_grouping",
        label="Stage 4.7 Grouping", kind="bool", category="feature",
        help=(
            "Cluster retrieved chunks by hierarchy_path; auto-commit "
            "when groups separate cleanly, otherwise return "
            "disambiguation cards. See D-049."
        ),
    ),
    # Tunables
    ConfigField(
        module="retrieval", key="gap_threshold",
        label="Group Gap Threshold (cosine distance)",
        kind="float", category="tunable",
        help=(
            "Distance gap between top two groups below which the "
            "pipeline asks the user to disambiguate instead of "
            "auto-committing. Default 0.05; tighter = more "
            "disambiguation prompts."
        ),
    ),
    ConfigField(
        module="pipeline", key="max_distance_threshold",
        label="Max Distance Threshold (relevance filter)",
        kind="float", category="tunable",
        help=(
            "Stage 4.5 cosine-distance cap. Chunks above this are "
            "dropped; if all are dropped the pipeline returns the "
            "not-found response. Empty / 0 = filter disabled."
        ),
    ),
    ConfigField(
        module="pipeline", key="top_k_cap",
        label="Top-K Cap (max chunks per query)", kind="int", category="tunable",
        help=(
            "Hard upper limit on chunks retrieved per query. Applied "
            "AFTER per-query-type widening — e.g. SUMMARIZE queries "
            "normally retrieve 50 chunks; setting this to 25 caps them "
            "at 25 regardless of intent. Empty / 0 = no cap (per-type "
            "widening unconstrained, current default behavior)."
        ),
    ),
    ConfigField(
        module="retrieval", key="broad_query_top_k",
        label="Broad Query Top-K",
        kind="int", category="tunable",
        help=(
            "Number of chunks retrieved for broad / breadth queries "
            "(CROSS_DOC, CROSS_MNO_COMPARISON, STANDARDS_COMPARISON, "
            "FEATURE_LEVEL, SUMMARIZE, TRACEABILITY, RELEASE_DIFF). "
            "Default 25. Resolved in order: this value > env var "
            "NORA_BROAD_QUERY_TOP_K > default 25."
        ),
    ),
    ConfigField(
        module="retrieval", key="narrow_query_top_k",
        label="Narrow Query Top-K",
        kind="int", category="tunable",
        help=(
            "Number of chunks retrieved for narrow / lookup queries "
            "(SINGLE_DOC, FACT, GENERAL). Default 10. Resolved in "
            "order: this value > env var NORA_NARROW_QUERY_TOP_K > "
            "default 10."
        ),
    ),
    ConfigField(
        module="retrieval", key="bm25_weight_by_type",
        label="BM25 Weight by QueryType",
        kind="dict_by_query_type", value_kind="float",
        category="tunable",
        help=(
            "Per-QueryType BM25 weight in the RRF fusion. 0.0 = pure "
            "dense (no BM25 contribution). Empirical defaults: 0.5 for "
            "SINGLE_DOC / FACT / STANDARDS_COMPARISON / TRACEABILITY "
            "(specific tokens benefit from term-match), 0.2 for "
            "SUMMARIZE (mostly dense — user paraphrases), 0.0 for "
            "CROSS_DOC / FEATURE_LEVEL / CROSS_MNO_COMPARISON / "
            "RELEASE_DIFF / GENERAL (parent chunks too thin to compete "
            "with BM25-favored leaves). Tune per-corpus."
        ),
    ),
]


CONFIG_SECTIONS: list[ConfigSection] = [
    ConfigSection(
        module="llm",
        title="LLM & Embedding",
        description=(
            "Query-time language model and the embedding model used "
            "for both indexing and query embedding. Mismatch between "
            "the vectorstore's build-time embedding and the active "
            "query-time embedding is the most common retrieval-quality "
            "bug — keep them in sync."
        ),
        fields=_LLM_FIELDS,
    ),
    ConfigSection(
        module="retrieval",
        title="Retrieval & Grouping",
        description=(
            "Stage 4.5 (relevance threshold) and Stage 4.7 (hierarchy "
            "grouping) knobs. Threshold is calibrated per embedding "
            "model — re-sweep when the model changes (see RETRIEVAL.md "
            "§10.3)."
        ),
        fields=_RETRIEVAL_FIELDS,
    ),
]


# Convenience accessors for routes/tests


def find_field(module: str, key: str) -> ConfigField | None:
    for section in CONFIG_SECTIONS:
        for f in section.fields:
            if f.module == module and f.key == key:
                return f
    return None


def all_fields() -> list[ConfigField]:
    out: list[ConfigField] = []
    for s in CONFIG_SECTIONS:
        out.extend(s.fields)
    return out
