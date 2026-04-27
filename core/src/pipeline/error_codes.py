"""Structured error codes for all pipeline stages.

Each code has format: {MODULE}-{SEVERITY}{NUMBER}
  MODULE: EXT, PRF, PRS, RES, TAX, STD, GRF, VEC, EVL, PIP, ENV, MDL
  SEVERITY: E=error, W=warning
  NUMBER: 3-digit

Usage:
    from src.pipeline.error_codes import PipelineError, CODES

    raise PipelineError("EXT-E001", context={"file": "foo.pdf"})
    # or just log it:
    logger.error(CODES["EXT-E001"].format(file="foo.pdf"))
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ErrorDef:
    code: str
    message: str
    hint: str

    def format(self, **kwargs: str) -> str:
        """Format message with context variables."""
        msg = self.message
        for k, v in kwargs.items():
            msg = msg.replace(f"{{{k}}}", str(v))
        return f"[{self.code}] {msg}"


class PipelineError(Exception):
    """Structured pipeline error with error code."""

    def __init__(self, code: str, context: dict | None = None, cause: Exception | None = None):
        self.code = code
        self.context = context or {}
        self.cause = cause
        defn = CODES.get(code)
        if defn:
            self.message = defn.format(**self.context)
            self.hint = defn.hint
        else:
            self.message = f"[{code}] Unknown error"
            self.hint = ""
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Error catalog
# ---------------------------------------------------------------------------

_DEFS: list[ErrorDef] = [
    # --- Extraction (EXT) ---
    ErrorDef("EXT-E001", "PDF extraction failed for {file}: {reason}", "Check if PDF is corrupted or password-protected"),
    ErrorDef("EXT-E002", "No text blocks extracted from {file}", "May be a scanned PDF — OCR not supported yet"),
    ErrorDef("EXT-E003", "Table extraction failed for {file}: {reason}", "Check pdfplumber compatibility"),
    ErrorDef("EXT-W001", "Low block count ({count}) for {file}", "Document may be mostly images"),
    ErrorDef("EXT-W002", "No tables found in {file}", "Expected for some documents"),

    # --- Profiler (PRF) ---
    ErrorDef("PRF-E001", "No heading patterns detected in {file}", "Check font size distribution — may need manual profile"),
    ErrorDef("PRF-E002", "No requirement ID patterns found in {file}", "Document may not contain structured requirements"),
    ErrorDef("PRF-W001", "Only {count} heading samples at level {level}", "May produce unreliable heading detection"),
    ErrorDef("PRF-W002", "Zone classification incomplete — {count}/{total} zones detected", "Review zone keywords in profile"),

    # --- Parser (PRS) ---
    ErrorDef("PRS-E001", "Profile validation failed: {reason}", "Regenerate profile or check corrections/profile.json"),
    ErrorDef("PRS-E002", "Section numbering gaps detected in {file}", "May indicate extraction issues"),
    ErrorDef("PRS-W001", "Requirement count ({count}) unusually low for {file}", "Check profile heading rules"),
    ErrorDef("PRS-W002", "Max section depth ({depth}) exceeds 6 in {file}", "Deep nesting may indicate parsing issues"),

    # --- Resolver (RES) ---
    ErrorDef("RES-E001", "Cross-reference resolution failed for {file}: {reason}", "Check tree file integrity"),
    ErrorDef("RES-W001", "Unresolved cross-plan reference: {ref} in {file}", "Referenced plan may not be in corpus"),
    ErrorDef("RES-W002", "No standards references found in {file}", "Document may not reference 3GPP specs"),

    # --- Taxonomy (TAX) ---
    ErrorDef("TAX-E001", "Feature extraction failed for {file}: {reason}", "Check LLM provider connection"),
    ErrorDef("TAX-E002", "Taxonomy consolidation failed: {reason}", "Check per-document feature files"),
    ErrorDef("TAX-W001", "Feature count ({count}) unusually high for {file}", "May indicate over-segmentation"),
    ErrorDef("TAX-W002", "Using correction file for taxonomy", "corrections/taxonomy.json overrides auto-generated"),

    # --- Standards (STD) ---
    ErrorDef("STD-E001", "Failed to download spec {spec}: {reason}", "Check network connectivity and 3GPP FTP availability"),
    ErrorDef("STD-E002", "Failed to parse spec {spec}: {reason}", "DOCX format may differ from expected structure"),
    ErrorDef("STD-W001", "Spec {spec} release {release} not found on 3GPP FTP", "May use older release or check spec number"),
    ErrorDef("STD-W002", "LibreOffice not available for DOC->DOCX conversion", "Install: sudo apt install libreoffice-writer"),

    # --- Graph (GRF) ---
    ErrorDef("GRF-E001", "Graph construction failed: {reason}", "Check input data completeness"),
    ErrorDef("GRF-W001", "Multiple connected components ({count})", "Some nodes may be isolated — check cross-references"),
    ErrorDef("GRF-W002", "No standard_section nodes created", "Standards data may not be available"),

    # --- Vector Store (VEC) ---
    ErrorDef("VEC-E001", "Embedding failed: {reason}", "Check sentence-transformers installation and model availability"),
    ErrorDef("VEC-E002", "ChromaDB storage failed: {reason}", "Check disk space and permissions"),
    ErrorDef("VEC-W001", "Duplicate chunks found ({count})", "Deduplication will keep longer text variant"),

    # --- Eval (EVL) ---
    ErrorDef("EVL-E001", "Evaluation failed for question {qid}: {reason}", "Check pipeline dependencies"),
    ErrorDef("EVL-W001", "Low overall score ({score:.0%}) for {qid}", "Review ground truth or pipeline output"),

    # --- Pipeline (PIP) ---
    ErrorDef("PIP-E001", "Stage {stage} failed: {reason}", "Check stage-specific error above"),
    ErrorDef("PIP-E002", "Required input missing for stage {stage}: {path}", "Run preceding stages first"),
    ErrorDef("PIP-W001", "Stage {stage} skipped: {reason}", "May affect downstream stages"),

    # --- Environment (ENV) ---
    ErrorDef("ENV-E001", "Document root does not exist: {path}", "Create directory or check path"),
    ErrorDef("ENV-E002", "No documents found in {path}", "Place source documents in documents/ subdirectory"),

    # --- Model (MDL) ---
    ErrorDef("MDL-E001", "Ollama server not reachable at {url}", "Start Ollama: ollama serve"),
    ErrorDef("MDL-E002", "Model {model} not available on Ollama", "Pull it: ollama pull {model}"),
    ErrorDef("MDL-W001", "Model {model} may not fit available memory ({available}GB)", "Consider a smaller model"),
]

CODES: dict[str, ErrorDef] = {d.code: d for d in _DEFS}
