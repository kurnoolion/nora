"""Normalized layout-parsing contract for the MNO-C table/figure bake-off.

Defines the `LayoutProvider` protocol plus the normalized result types that every
provider maps its native output onto, so Docling / PaddleOCR PP-Structure / Hiro
can be compared apples-to-apples on the same PDFs.

This is a SPIKE harness (lives under experiments/, deliberately decoupled from
core/src). If a provider wins the bake-off, THIS protocol is the shape to promote
into a core module — it mirrors NORA's existing protocol-based abstractions
(LLMProvider / EmbeddingProvider / VectorStoreProvider). A promoted version would
map `LayoutBlock` onto `core.src.models.document.ContentBlock`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

# Normalized block kinds. Every provider maps its own taxonomy onto these so the
# summary can compare like with like. Unknown labels fall back to "other".
KINDS = (
    "title", "section_header", "text", "list", "table", "figure",
    "caption", "formula", "header", "footer", "other",
)


@dataclass
class LayoutBlock:
    """One region of a page, normalized across providers."""
    kind: str
    page: int                 # 1-based; 0 when the provider doesn't report it
    order: int                # reading-order index within the document
    bbox: tuple[float, float, float, float] | None = None  # (x0,y0,x1,y1) if known
    text: str = ""            # plain text (text-like blocks; a text rendering for tables)
    html: str = ""            # HTML (tables emit <table>…; empty otherwise)
    image_path: str = ""      # figures: path to a saved crop, if the provider emits one
    meta: dict = field(default_factory=dict)  # provider-native extras (raw label, etc.)


@dataclass
class LayoutResult:
    """A provider's full parse of one document."""
    provider: str
    source: str               # document filename (never a full path — keep it terse)
    blocks: list[LayoutBlock] = field(default_factory=list)
    page_count: int = 0
    seconds: float = 0.0
    ok: bool = True
    error: str = ""

    @property
    def tables(self) -> list[LayoutBlock]:
        return [b for b in self.blocks if b.kind == "table"]

    @property
    def figures(self) -> list[LayoutBlock]:
        return [b for b in self.blocks if b.kind == "figure"]


@runtime_checkable
class LayoutProvider(Protocol):
    """The contract each engine's adapter implements."""
    name: str

    def available(self) -> tuple[bool, str]:
        """(is_usable, reason). False when deps aren't installed or a required
        service isn't reachable — the harness skips the provider, never crashes."""
        ...

    def parse(self, pdf_path: Path, image_dir: Path | None = None) -> LayoutResult:
        """Parse a PDF into normalized blocks. When `image_dir` is given, figure
        crops are saved there and referenced via `LayoutBlock.image_path`. Must
        not raise — capture failures into `LayoutResult(ok=False, error=...)` so
        one bad doc/provider doesn't sink the run."""
        ...


def normalize_kind(raw: str, mapping: dict[str, str]) -> str:
    """Map a provider-native label to a normalized KIND (fallback: 'other')."""
    key = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    return mapping.get(key, "other")


def save_result(result: LayoutResult, out_dir: Path) -> Path:
    """Write `<stem>__<provider>.json` (machine) and `.md` (human) into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{Path(result.source).stem}__{result.provider}"
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / f"{stem}.md").write_text(to_markdown(result), encoding="utf-8")
    return json_path


def to_markdown(result: LayoutResult) -> str:
    """Reading-order rendering for eyeballing one provider's output on one doc."""
    head = (
        f"# {result.provider} — {result.source}\n\n"
        f"_pages={result.page_count} blocks={len(result.blocks)} "
        f"tables={len(result.tables)} figures={len(result.figures)} "
        f"seconds={result.seconds:.1f} ok={result.ok}_\n"
    )
    if not result.ok:
        return head + f"\n**ERROR:** {result.error}\n"
    out = [head]
    for b in result.blocks:
        tag = f"<!-- p{b.page} #{b.order} {b.kind} -->"
        if b.kind in ("title", "section_header"):
            out.append(f"{tag}\n## {b.text}\n")
        elif b.kind == "table":
            body = b.html or (f"```\n{b.text}\n```" if b.text else "_(empty table)_")
            out.append(f"{tag}\n**[TABLE p{b.page}]**\n\n{body}\n")
        elif b.kind == "figure":
            cap = f"\ncaption: {b.text}" if b.text else ""
            img = f"![figure]({b.image_path})" if b.image_path else "_(image not extracted)_"
            out.append(f"{tag}\n**[FIGURE p{b.page}]** {img}{cap}\n")
        else:
            out.append(f"{tag}\n{b.text}\n")
    return "\n".join(out)
