"""Pipeline stage functions.

Each stage function takes a PipelineContext and returns a StageResult.
Imports are lazy within each function to tolerate missing dependencies.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.src.pipeline.runner import PipelineContext

logger = logging.getLogger(__name__)


@dataclass
class StageResult:
    """Result from running a single pipeline stage."""

    stage: str
    status: str  # OK, WARN, FAIL, SKIP
    elapsed_seconds: float
    stats: dict = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("OK", "WARN")


def _fail(stage: str, code: str, message: str, elapsed: float = 0.0) -> StageResult:
    return StageResult(
        stage=stage, status="FAIL", elapsed_seconds=elapsed,
        error_code=code, error_message=message,
    )


# ---------------------------------------------------------------------------
# Stage 1: extract
# ---------------------------------------------------------------------------

def run_extract(ctx: PipelineContext) -> StageResult:
    """Extract documents into normalized IR."""
    t0 = time.time()
    stage = "extract"
    out_dir = ctx.stage_output(stage)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from core.src.extraction.registry import extract_document, infer_metadata_from_path, supported_extensions
    except ImportError as e:
        return _fail(stage, "EXT-E001", f"Import error: {e}", time.time() - t0)

    exts = supported_extensions()
    files = sorted(
        f for f in ctx.documents_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in exts
    )

    if not files:
        return _fail(stage, "ENV-E002", f"No documents in {ctx.documents_dir}", time.time() - t0)

    stats = {"docs": 0, "blocks": 0, "tables": 0, "failed": 0}
    warnings: list[str] = []
    ir_paths: list[str] = []

    for f in files:
        metadata = infer_metadata_from_path(f)
        try:
            ir = extract_document(
                f, mno=metadata["mno"], release=metadata["release"],
                doc_type=metadata["doc_type"],
            )
            out_path = out_dir / f"{f.stem}_ir.json"
            ir.save_json(out_path)
            ir_paths.append(str(out_path))
            stats["docs"] += 1
            stats["blocks"] += ir.block_count
            tbl_count = sum(1 for b in ir.content_blocks if b.type.value == "table")
            stats["tables"] += tbl_count
            if ir.block_count < 10:
                warnings.append(f"EXT-W001: Low blocks ({ir.block_count}) in {f.name}")
        except Exception as e:
            stats["failed"] += 1
            warnings.append(f"EXT-E001: {f.name}: {e}")

    ctx.state["ir_paths"] = ir_paths
    elapsed = time.time() - t0
    status = "WARN" if stats["failed"] > 0 else "OK"
    return StageResult(stage=stage, status=status, elapsed_seconds=elapsed,
                       stats=stats, warnings=warnings)


# ---------------------------------------------------------------------------
# Stage 2: profile
# ---------------------------------------------------------------------------

def run_profile(ctx: PipelineContext) -> StageResult:
    """Create or load document profile."""
    t0 = time.time()
    stage = "profile"
    out_dir = ctx.stage_output(stage)
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_out = out_dir / "profile.json"

    # Check for correction override
    correction = ctx.correction("profile.json")
    if correction:
        shutil.copy2(correction, profile_out)
        ctx.state["profile_path"] = str(profile_out)
        return StageResult(
            stage=stage, status="OK", elapsed_seconds=time.time() - t0,
            stats={"source": "correction", "path": str(correction)},
            warnings=["TAX-W002: Using correction file for profile"],
        )

    try:
        from core.src.models.document import DocumentIR
        from core.src.profiler.profiler import DocumentProfiler
    except ImportError as e:
        return _fail(stage, "PRF-E001", f"Import error: {e}", time.time() - t0)

    # Load extracted IRs
    extract_dir = ctx.stage_output("extract")
    ir_files = sorted(extract_dir.glob("*_ir.json"))
    if not ir_files:
        return _fail(stage, "PIP-E002", f"No IR files in {extract_dir}", time.time() - t0)

    docs = [DocumentIR.load_json(f) for f in ir_files]
    profiler = DocumentProfiler()
    profile = profiler.create_profile(docs, profile_name="auto")
    profile.save_json(profile_out)

    stats = {
        "heading_levels": len(profile.heading_detection.levels),
        "req_patterns": 1 if profile.requirement_id.pattern else 0,
        "zones": len(profile.document_zones),
        "docs_analyzed": len(docs),
    }

    warnings: list[str] = []
    if stats["heading_levels"] == 0:
        warnings.append("PRF-E001: No heading patterns detected")
    if stats["req_patterns"] == 0:
        warnings.append("PRF-E002: No requirement ID patterns found")

    ctx.state["profile_path"] = str(profile_out)
    return StageResult(stage=stage, status="WARN" if warnings else "OK",
                       elapsed_seconds=time.time() - t0, stats=stats, warnings=warnings)


# ---------------------------------------------------------------------------
# Stage 3: parse
# ---------------------------------------------------------------------------

def run_parse(ctx: PipelineContext) -> StageResult:
    """Parse extracted documents into requirement trees."""
    t0 = time.time()
    stage = "parse"
    out_dir = ctx.stage_output(stage)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from core.src.models.document import DocumentIR
        from core.src.profiler.profile_schema import DocumentProfile
        from core.src.parser.structural_parser import GenericStructuralParser
    except ImportError as e:
        return _fail(stage, "PRS-E001", f"Import error: {e}", time.time() - t0)

    profile_path = ctx.state.get("profile_path") or str(ctx.stage_output("profile") / "profile.json")
    if not Path(profile_path).exists():
        return _fail(stage, "PIP-E002", f"Profile not found: {profile_path}", time.time() - t0)

    profile = DocumentProfile.load_json(Path(profile_path))
    parser = GenericStructuralParser(profile)

    extract_dir = ctx.stage_output("extract")
    ir_files = sorted(extract_dir.glob("*_ir.json"))
    if not ir_files:
        return _fail(stage, "PIP-E002", f"No IR files in {extract_dir}", time.time() - t0)

    stats = {"docs": 0, "reqs": 0, "max_depth": 0, "toc": 0, "struck": 0, "cascade": 0, "revhist": 0, "defs": 0}
    tree_paths: list[str] = []
    warnings: list[str] = []

    for f in ir_files:
        try:
            doc = DocumentIR.load_json(f)
            tree = parser.parse(doc)
            out_name = f.stem.replace("_ir", "_tree") + ".json"
            out_path = out_dir / out_name
            tree.save_json(out_path)
            tree_paths.append(str(out_path))
            stats["docs"] += 1
            stats["reqs"] += len(tree.requirements)
            depth = max((r.section_number.count(".") for r in tree.requirements), default=0) + 1
            stats["max_depth"] = max(stats["max_depth"], depth)
            stats["toc"] += tree.parse_stats.toc_blocks_dropped
            stats["struck"] += tree.parse_stats.struck_blocks_dropped
            stats["cascade"] += tree.parse_stats.cascade_blocks_dropped
            stats["revhist"] += tree.parse_stats.revhist_blocks_dropped
            stats["defs"] += tree.parse_stats.defs_extracted
        except Exception as e:
            warnings.append(f"PRS-E001: {f.name}: {e}")

    ctx.state["tree_paths"] = tree_paths
    return StageResult(stage=stage, status="WARN" if warnings else "OK",
                       elapsed_seconds=time.time() - t0, stats=stats, warnings=warnings)


# ---------------------------------------------------------------------------
# Stage 4: resolve
# ---------------------------------------------------------------------------

def run_resolve(ctx: PipelineContext) -> StageResult:
    """Resolve cross-references across parsed trees."""
    t0 = time.time()
    stage = "resolve"
    out_dir = ctx.stage_output(stage)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from core.src.parser.structural_parser import RequirementTree
        from core.src.resolver.resolver import CrossReferenceResolver
    except ImportError as e:
        return _fail(stage, "RES-E001", f"Import error: {e}", time.time() - t0)

    parse_dir = ctx.stage_output("parse")
    tree_files = sorted(parse_dir.glob("*_tree.json"))
    if not tree_files:
        return _fail(stage, "PIP-E002", f"No tree files in {parse_dir}", time.time() - t0)

    trees = [RequirementTree.load_json(f) for f in tree_files]
    resolver = CrossReferenceResolver(trees)
    manifests = resolver.resolve_all()

    stats = {"internal": 0, "cross_plan": 0, "standards": 0, "unresolved": 0}
    for m in manifests:
        out_path = out_dir / f"{m.plan_id}_xrefs.json"
        m.save_json(out_path)
        s = m.summary
        stats["internal"] += s.resolved_internal
        stats["cross_plan"] += s.resolved_cross_plan
        stats["standards"] += s.resolved_standards
        stats["unresolved"] += s.broken_internal

    warnings: list[str] = []
    if stats["unresolved"] > 0:
        warnings.append(f"RES-W001: {stats['unresolved']} unresolved internal refs")

    return StageResult(stage=stage, status="WARN" if warnings else "OK",
                       elapsed_seconds=time.time() - t0, stats=stats, warnings=warnings)


# ---------------------------------------------------------------------------
# Stage 5: taxonomy
# ---------------------------------------------------------------------------

def run_taxonomy(ctx: PipelineContext) -> StageResult:
    """Extract feature taxonomy from parsed trees."""
    t0 = time.time()
    stage = "taxonomy"
    out_dir = ctx.stage_output(stage)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Check for correction override
    correction = ctx.correction("taxonomy.json")
    if correction:
        shutil.copy2(correction, out_dir / "taxonomy.json")
        ctx.state["taxonomy_path"] = str(out_dir / "taxonomy.json")
        return StageResult(
            stage=stage, status="OK", elapsed_seconds=time.time() - t0,
            stats={"source": "correction", "path": str(correction)},
            warnings=["TAX-W002: Using correction file for taxonomy"],
        )

    try:
        from core.src.parser.structural_parser import RequirementTree
        from core.src.taxonomy.extractor import FeatureExtractor
        from core.src.taxonomy.consolidator import TaxonomyConsolidator
    except ImportError as e:
        return _fail(stage, "TAX-E001", f"Import error: {e}", time.time() - t0)

    # Create LLM provider
    llm = ctx.create_llm_provider()

    parse_dir = ctx.stage_output("parse")
    tree_files = sorted(parse_dir.glob("*_tree.json"))
    if not tree_files:
        return _fail(stage, "PIP-E002", f"No tree files in {parse_dir}", time.time() - t0)

    extractor = FeatureExtractor(llm)
    all_doc_features = []

    for f in tree_files:
        tree = RequirementTree.load_json(f)
        doc_features = extractor.extract(tree)
        doc_out = out_dir / f"{tree.plan_id}_features.json"
        doc_features.save_json(doc_out)
        all_doc_features.append(doc_features)

    consolidator = TaxonomyConsolidator()
    taxonomy = consolidator.consolidate(all_doc_features)
    taxonomy_path = out_dir / "taxonomy.json"
    taxonomy.save_json(taxonomy_path)

    ctx.state["taxonomy_path"] = str(taxonomy_path)
    stats = {"features": len(taxonomy.features), "docs": len(all_doc_features)}
    return StageResult(stage=stage, status="OK", elapsed_seconds=time.time() - t0, stats=stats)


# ---------------------------------------------------------------------------
# Stage 6: standards
# ---------------------------------------------------------------------------

def run_standards(ctx: PipelineContext) -> StageResult:
    """Ingest referenced 3GPP standards."""
    t0 = time.time()
    stage = "standards"
    out_dir = ctx.stage_output(stage)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from core.src.standards.reference_collector import StandardsReferenceCollector
        from core.src.standards.spec_downloader import SpecDownloader
        from core.src.standards.spec_parser import SpecParser
        from core.src.standards.section_extractor import SectionExtractor
    except ImportError as e:
        return _fail(stage, "STD-E001", f"Import error: {e}", time.time() - t0)

    resolve_dir = ctx.stage_output("resolve")
    parse_dir = ctx.stage_output("parse")

    collector = StandardsReferenceCollector()
    try:
        index = collector.collect(manifest_dir=resolve_dir, trees_dir=parse_dir)
    except Exception as e:
        return _fail(stage, "STD-E001", f"Reference collection failed: {e}", time.time() - t0)

    index_path = out_dir / "reference_index.json"
    index.save_json(index_path)

    # Download + parse + extract
    downloader = SpecDownloader(
        cache_dir=out_dir, source=ctx.standards_source
    )
    spec_parser = SpecParser()
    extractor = SectionExtractor()

    stats = {"specs_found": len(index.specs), "downloaded": 0, "parsed": 0, "extracted": 0, "failed": 0}
    warnings: list[str] = []

    for spec_ref in index.specs:
        if spec_ref.release_num <= 0:
            continue
        label = f"TS {spec_ref.spec} Rel-{spec_ref.release_num}"
        try:
            doc_path = downloader.download(spec_ref.spec, spec_ref.release_num)
            if not doc_path:
                warnings.append(f"STD-W001: {label} not found")
                stats["failed"] += 1
                continue
            stats["downloaded"] += 1

            spec_dir = out_dir / f"TS_{spec_ref.spec}" / f"Rel-{spec_ref.release_num}"
            spec_doc = spec_parser.parse(doc_path)
            spec_doc.save_json(spec_dir / "spec_parsed.json")
            stats["parsed"] += 1

            result = extractor.extract(spec_doc, spec_ref.sections, source_plans=spec_ref.source_plans)
            result.save_json(spec_dir / "sections.json")
            stats["extracted"] += 1
        except Exception as e:
            warnings.append(f"STD-E002: {label}: {e}")
            stats["failed"] += 1

    return StageResult(stage=stage, status="WARN" if stats["failed"] > 0 else "OK",
                       elapsed_seconds=time.time() - t0, stats=stats, warnings=warnings)


# ---------------------------------------------------------------------------
# Stage 7: graph
# ---------------------------------------------------------------------------

def run_graph(ctx: PipelineContext) -> StageResult:
    """Build the knowledge graph."""
    t0 = time.time()
    stage = "graph"
    out_dir = ctx.stage_output(stage)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from core.src.graph.builder import KnowledgeGraphBuilder
    except ImportError as e:
        return _fail(stage, "GRF-E001", f"Import error: {e}", time.time() - t0)

    builder = KnowledgeGraphBuilder()
    taxonomy_path = Path(ctx.state.get("taxonomy_path", ctx.stage_output("taxonomy") / "taxonomy.json"))

    try:
        graph = builder.build(
            trees_dir=ctx.stage_output("parse"),
            manifests_dir=ctx.stage_output("resolve"),
            taxonomy_path=taxonomy_path,
            standards_dir=ctx.stage_output("standards"),
        )
        graph_stats = builder.compute_stats()
    except Exception as e:
        return _fail(stage, "GRF-E001", f"Graph build failed: {e}", time.time() - t0)

    # Save
    import networkx as nx
    graph_path = out_dir / "knowledge_graph.json"
    with open(graph_path, "w") as f:
        json.dump(nx.node_link_data(graph), f, indent=2)
    graph_stats.save_json(out_dir / "graph_stats.json")

    ctx.state["graph_path"] = str(graph_path)

    # Connected components
    cc = nx.number_weakly_connected_components(graph)

    stats = {
        "nodes": graph_stats.total_nodes,
        "edges": graph_stats.total_edges,
        "components": cc,
    }
    warnings: list[str] = []
    if cc > 1:
        warnings.append(f"GRF-W001: {cc} connected components")

    return StageResult(stage=stage, status="WARN" if warnings else "OK",
                       elapsed_seconds=time.time() - t0, stats=stats, warnings=warnings)


# ---------------------------------------------------------------------------
# Stage 8: vectorstore
# ---------------------------------------------------------------------------

def run_vectorstore(ctx: PipelineContext) -> StageResult:
    """Build the vector store."""
    t0 = time.time()
    stage = "vectorstore"
    out_dir = ctx.stage_output(stage)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from core.src.vectorstore import make_embedder
        from core.src.vectorstore.config import VectorStoreConfig
        from core.src.vectorstore.builder import VectorStoreBuilder
        from core.src.vectorstore.store_chroma import ChromaDBStore
    except ImportError as e:
        return _fail(stage, "VEC-E001", f"Import error: {e}", time.time() - t0)

    config = VectorStoreConfig(
        persist_directory=str(out_dir),
        embedding_provider=ctx.embedding_provider,
        embedding_model=ctx.embedding_model,
    )
    embedder = make_embedder(config)
    store = ChromaDBStore(
        persist_directory=config.persist_directory,
        collection_name=config.collection_name,
        distance_metric=config.distance_metric,
    )

    builder = VectorStoreBuilder(embedder=embedder, store=store, config=config)
    taxonomy_path = Path(ctx.state.get("taxonomy_path", ctx.stage_output("taxonomy") / "taxonomy.json"))

    try:
        build_stats = builder.build(
            trees_dir=ctx.stage_output("parse"),
            taxonomy_path=taxonomy_path,
            rebuild=True,
        )
    except Exception as e:
        return _fail(stage, "VEC-E001", f"Build failed: {e}", time.time() - t0)

    config.save_json(out_dir / "config.json")
    build_stats.save_json(out_dir / "build_stats.json")

    stats = {
        "chunks": build_stats.total_chunks,
        "model": build_stats.embedding_model,
        "dedup": 0,
    }
    return StageResult(stage=stage, status="OK", elapsed_seconds=time.time() - t0, stats=stats)


# ---------------------------------------------------------------------------
# Stage 9: eval
# ---------------------------------------------------------------------------

def run_eval(ctx: PipelineContext) -> StageResult:
    """Run evaluation."""
    t0 = time.time()
    stage = "eval"
    out_dir = ctx.stage_output(stage)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from core.src.eval.questions import ALL_QUESTIONS
        from core.src.eval.runner import EvalRunner
        from core.src.query.pipeline import load_graph
        from core.src.vectorstore import make_embedder
        from core.src.vectorstore.config import VectorStoreConfig
        from core.src.vectorstore.store_chroma import ChromaDBStore
    except ImportError as e:
        return _fail(stage, "EVL-E001", f"Import error: {e}", time.time() - t0)

    # Load graph
    graph_path = Path(ctx.state.get("graph_path", ctx.stage_output("graph") / "knowledge_graph.json"))
    if not graph_path.exists():
        return _fail(stage, "PIP-E002", f"Graph not found: {graph_path}", time.time() - t0)
    graph = load_graph(graph_path)

    # Load vector store
    vs_dir = ctx.stage_output("vectorstore")
    vs_config_path = vs_dir / "config.json"
    vs_config = (
        VectorStoreConfig.load_json(vs_config_path)
        if vs_config_path.exists()
        else VectorStoreConfig(
            persist_directory=str(vs_dir),
            embedding_provider=ctx.embedding_provider,
            embedding_model=ctx.embedding_model,
        )
    )

    embedder = make_embedder(vs_config)
    store = ChromaDBStore(
        persist_directory=vs_config.persist_directory, collection_name=vs_config.collection_name,
        distance_metric=vs_config.distance_metric,
    )

    # Load user-supplied eval questions from Excel if available
    questions = list(ALL_QUESTIONS)
    user_questions = _load_user_eval_questions(ctx.eval_dir)
    if user_questions:
        questions.extend(user_questions)

    # Create synthesizer
    synthesizer = None
    llm = ctx.create_llm_provider(require_real=False)
    if llm and not hasattr(llm, "_is_mock"):
        from core.src.query.synthesizer import LLMSynthesizer
        synthesizer = LLMSynthesizer(llm)

    # Pre-retrieval query rewriter — when a real LLM is available,
    # use it to expand concept-shaped queries with telecom-specific
    # paraphrases before retrieval. Per-query-type policy gates which
    # query types actually call the rewriter (see
    # `pipeline._TYPE_REWRITE_ENABLED`); this construction just makes
    # the rewriter available. Falls back to the mock (no rewrites)
    # when the LLM is unavailable, preserving deterministic behavior.
    rewriter = None
    if llm and not hasattr(llm, "_is_mock"):
        from core.src.query.rewriter import LLMQueryRewriter
        rewriter = LLMQueryRewriter(llm)

    runner = EvalRunner(
        graph=graph, embedder=embedder, store=store,
        synthesizer=synthesizer, rewriter=rewriter,
    )
    report = runner.run_all(questions)

    # Save report
    report_path = out_dir / "report.json"
    with open(report_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)

    stats = {
        "questions": len(questions),
        "user_questions": len(user_questions),
        "overall": f"{report.avg_overall:.1%}",
        "completeness": f"{report.avg_completeness:.1%}",
        "accuracy": f"{report.avg_accuracy:.1%}",
        "citation": f"{report.avg_citation_quality:.1%}",
    }
    return StageResult(stage=stage, status="OK", elapsed_seconds=time.time() - t0, stats=stats)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_user_eval_questions(eval_dir: Path | None) -> list:
    """Load user-supplied evaluation questions from Excel files.

    Expected Excel columns:
        question_id, category, question, expected_plans (comma-sep),
        expected_req_ids (comma-sep), expected_features (comma-sep),
        expected_standards (comma-sep), expected_concepts (comma-sep),
        min_plans (int), min_chunks (int)
    """
    if not eval_dir or not eval_dir.exists():
        return []

    xlsx_files = list(eval_dir.glob("*.xlsx")) + list(eval_dir.glob("*.xls"))
    if not xlsx_files:
        return []

    questions = []
    try:
        import openpyxl
    except ImportError:
        logger.warning("openpyxl not available — cannot load Excel eval questions")
        return []

    for xlsx_path in xlsx_files:
        try:
            wb = openpyxl.load_workbook(xlsx_path, read_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if len(rows) < 2:
                continue

            headers = [str(h).strip().lower() if h else "" for h in rows[0]]
            for row in rows[1:]:
                data = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
                if not data.get("question"):
                    continue

                from core.src.eval.questions import EvalQuestion, GroundTruth
                q = EvalQuestion(
                    question_id=str(data.get("question_id", f"USER_{len(questions)+1:03d}")),
                    category=str(data.get("category", "general")),
                    question=str(data["question"]),
                    ground_truth=GroundTruth(
                        expected_plans=_split_csv(data.get("expected_plans", "")),
                        expected_req_ids=_split_csv(data.get("expected_req_ids", "")),
                        expected_features=_split_csv(data.get("expected_features", "")),
                        expected_standards=_split_csv(data.get("expected_standards", "")),
                        expected_concepts=_split_csv(data.get("expected_concepts", "")),
                        min_plans=int(data.get("min_plans", 1)),
                        min_chunks=int(data.get("min_chunks", 1)),
                    ),
                )
                questions.append(q)
            wb.close()
        except Exception as e:
            logger.warning(f"Failed to load {xlsx_path.name}: {e}")

    if questions:
        logger.info(f"Loaded {len(questions)} user eval questions from {eval_dir}")
    return questions


def _split_csv(value) -> list[str]:
    """Split comma-separated string into list, stripping whitespace."""
    if not value:
        return []
    return [s.strip() for s in str(value).split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Stage function registry
# ---------------------------------------------------------------------------

STAGE_FUNCS: dict[str, callable] = {
    "extract": run_extract,
    "profile": run_profile,
    "parse": run_parse,
    "resolve": run_resolve,
    "taxonomy": run_taxonomy,
    "standards": run_standards,
    "graph": run_graph,
    "vectorstore": run_vectorstore,
    "eval": run_eval,
}
