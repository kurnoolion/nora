"""Build a self-contained JS data bundle for the NORA visualizations.

Inputs come from the already-generated artifacts under ./data. The output is a
single JS file that sets window.NORA_DATA so the HTML viewers work from file://
without any fetch calls or backend.

Scope: centered on LTEOTADM, plus connected features, other plans (sibling
links), and referenced standards.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = Path(__file__).resolve().parent / "nora_data.js"

FOCUS_PLAN = "LTEOTADM"


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    kg = load_json(DATA / "graph" / "knowledge_graph.json")
    taxonomy = load_json(DATA / "taxonomy" / "taxonomy.json")
    otadm_features = load_json(DATA / "taxonomy" / f"{FOCUS_PLAN}_features.json")
    xrefs = load_json(DATA / "resolved" / f"{FOCUS_PLAN}_xrefs.json")
    tree = load_json(DATA / "parsed" / f"{FOCUS_PLAN}_tree.json")
    graph_stats = load_json(DATA / "graph" / "graph_stats.json")
    vector_stats = load_json(DATA / "vectorstore" / "build_stats.json")
    ref_index = load_json(DATA / "standards" / "reference_index.json")

    # --- Build tree lookup by req_id ---
    tree_by_req = {r["req_id"]: r for r in tree["requirements"]}

    # --- Pull RAG chunks (Chroma) for LTEOTADM ---
    conn = sqlite3.connect(DATA / "vectorstore" / "chroma.sqlite3")
    cur = conn.cursor()
    # Find all embedding ids whose plan_id == LTEOTADM
    cur.execute(
        "SELECT id FROM embedding_metadata WHERE key='plan_id' AND string_value=?",
        (FOCUS_PLAN,),
    )
    otadm_ids = [r[0] for r in cur.fetchall()]
    rag_chunks = {}  # req_id -> chunk dict
    for eid in otadm_ids:
        cur.execute(
            "SELECT key, string_value, int_value, float_value FROM embedding_metadata WHERE id=?",
            (eid,),
        )
        meta = {}
        doc_text = None
        for k, sv, iv, fv in cur.fetchall():
            if k == "chroma:document":
                doc_text = sv
            else:
                meta[k] = sv if sv is not None else (iv if iv is not None else fv)
        req_id = meta.get("req_id")
        if req_id:
            try:
                feature_ids = json.loads(meta.get("feature_ids", "[]"))
            except Exception:
                feature_ids = []
            rag_chunks[req_id] = {
                "embedding_id": eid,
                "chunk_text": doc_text,
                "chunk_length_chars": len(doc_text or ""),
                "metadata": {
                    "mno": meta.get("mno"),
                    "release": meta.get("release"),
                    "plan_id": meta.get("plan_id"),
                    "section_number": meta.get("section_number"),
                    "zone_type": meta.get("zone_type"),
                    "doc_type": meta.get("doc_type"),
                    "feature_ids": feature_ids,
                },
            }
    conn.close()

    # --- Build the LTEOTADM-centered subgraph for visualization ---
    otadm_req_ids = {
        n["id"] for n in kg["nodes"]
        if n.get("node_type") == "Requirement" and n.get("plan_id") == FOCUS_PLAN
    }
    plan_node_id = None
    for n in kg["nodes"]:
        if n.get("node_type") == "Plan" and n.get("plan_id") == FOCUS_PLAN:
            plan_node_id = n["id"]
            break

    # Start with OTADM reqs + plan + MNO + release
    keep_ids = set(otadm_req_ids)
    if plan_node_id:
        keep_ids.add(plan_node_id)
    for n in kg["nodes"]:
        if n.get("node_type") in ("MNO", "Release"):
            keep_ids.add(n["id"])

    # Add features touching OTADM
    feature_ids_touching_otadm = set()
    for e in kg["edges"]:
        if e["edge_type"] == "maps_to" and e["source"] in otadm_req_ids:
            feature_ids_touching_otadm.add(e["target"])
    keep_ids.update(feature_ids_touching_otadm)

    # Add standards sections that OTADM reqs reference
    std_ids_touching_otadm = set()
    for e in kg["edges"]:
        if e["edge_type"] == "references_standard" and e["source"] in otadm_req_ids:
            std_ids_touching_otadm.add(e["target"])
    keep_ids.update(std_ids_touching_otadm)

    # Add OTHER plans that share features with OTADM (to show shared-feature relationships)
    # and one representative requirement from each to keep graph small
    other_plan_ids = set()
    for n in kg["nodes"]:
        if n.get("node_type") == "Plan" and n.get("plan_id") != FOCUS_PLAN:
            other_plan_ids.add(n["id"])
    keep_ids.update(other_plan_ids)

    # Build subgraph nodes
    sub_nodes = [n for n in kg["nodes"] if n["id"] in keep_ids]

    # Build subgraph edges (only between kept nodes; drop clutter edge types to keep visual clear)
    visible_edge_types = {
        "has_release",
        "contains_plan",
        "belongs_to",
        "parent_of",
        "maps_to",
        "depends_on",
        "references_standard",
    }
    sub_edges = []
    for e in kg["edges"]:
        if e["source"] not in keep_ids or e["target"] not in keep_ids:
            continue
        if e["edge_type"] not in visible_edge_types:
            continue
        # For the visualization, only show PRIMARY feature mappings to reduce visual clutter.
        # Secondary mappings remain queryable via req_details but aren't drawn as edges.
        if e["edge_type"] == "maps_to" and e.get("mapping_type") != "primary":
            continue
        sub_edges.append(e)

    # --- Build hierarchy paths for each OTADM requirement (chain to plan via parent_of) ---
    parent_of_map = {}  # child -> parent
    for e in kg["edges"]:
        if e["edge_type"] == "parent_of":
            parent_of_map[e["target"]] = e["source"]

    def hierarchy_chain(req_id):
        chain = []
        cur = req_id
        guard = 0
        while cur and guard < 20:
            guard += 1
            node = next((n for n in kg["nodes"] if n["id"] == cur), None)
            if node is None:
                break
            chain.append({
                "id": node["id"],
                "node_type": node.get("node_type"),
                "section_number": node.get("section_number"),
                "title": node.get("title"),
                "plan_id": node.get("plan_id"),
            })
            cur = parent_of_map.get(cur)
        chain.reverse()
        # Prepend Plan and MNO
        plan_node = next((n for n in kg["nodes"] if n["id"] == plan_node_id), None)
        if plan_node:
            chain.insert(0, {
                "id": plan_node["id"],
                "node_type": "Plan",
                "title": plan_node.get("plan_name", "Plan"),
                "plan_id": plan_node.get("plan_id"),
            })
        chain.insert(0, {"id": "mno:VZW", "node_type": "MNO", "title": "VZW"})
        return chain

    # --- Requirement detail bundle ---
    req_details = {}
    for rid in otadm_req_ids:
        node = next((n for n in kg["nodes"] if n["id"] == rid), None)
        if node is None:
            continue
        req_id_short = node.get("req_id")
        tree_entry = tree_by_req.get(req_id_short, {})

        # Features this req maps to
        mapped_features = []
        for e in kg["edges"]:
            if e["edge_type"] == "maps_to" and e["source"] == rid:
                mapped_features.append({
                    "feature_id": e["target"].replace("feature:", ""),
                    "mapping_type": e.get("mapping_type", "unknown"),
                })

        # Internal deps (within OTADM)
        internal_deps = []
        for e in kg["edges"]:
            if e["edge_type"] == "depends_on" and e["source"] == rid:
                internal_deps.append({
                    "target_id": e["target"],
                    "ref_type": e.get("ref_type"),
                })

        # Standards references
        standards_refs = []
        for e in kg["edges"]:
            if e["edge_type"] == "references_standard" and e["source"] == rid:
                std_node = next((n for n in kg["nodes"] if n["id"] == e["target"]), None)
                if std_node:
                    standards_refs.append({
                        "id": std_node["id"],
                        "spec": std_node.get("spec"),
                        "release_num": std_node.get("release_num"),
                        "section": std_node.get("section"),
                        "title": std_node.get("title"),
                    })

        req_details[rid] = {
            "id": rid,
            "req_id": req_id_short,
            "section_number": node.get("section_number"),
            "title": node.get("title"),
            "zone_type": node.get("zone_type"),
            "hierarchy_path": tree_entry.get("hierarchy_path") or node.get("hierarchy_path", []),
            "text": node.get("text"),
            "plan_id": node.get("plan_id"),
            "mno": node.get("mno"),
            "release": node.get("release"),
            "children": tree_entry.get("children", []),
            "parent_req_id": tree_entry.get("parent_req_id"),
            "tables": tree_entry.get("tables", []),
            "cross_references": tree_entry.get("cross_references", {}),
            "mapped_features": mapped_features,
            "internal_dependencies": internal_deps,
            "standards_references": standards_refs,
            "hierarchy_chain": hierarchy_chain(rid),
            "rag_chunk": rag_chunks.get(req_id_short),
        }

    # --- Build edge lookup with details for edge clicks ---
    edge_details = []
    for i, e in enumerate(sub_edges):
        src = next((n for n in sub_nodes if n["id"] == e["source"]), None)
        tgt = next((n for n in sub_nodes if n["id"] == e["target"]), None)
        desc = EDGE_TYPE_DESC.get(e["edge_type"], "")
        edge_details.append({
            "index": i,
            "source": e["source"],
            "target": e["target"],
            "edge_type": e["edge_type"],
            "description": desc,
            "attrs": {k: v for k, v in e.items() if k not in ("source", "target", "edge_type")},
            "source_label": _node_label(src),
            "target_label": _node_label(tgt),
        })

    # --- Query simulations (precomputed traces using real data) ---
    query_sims = build_query_sims(kg, req_details, otadm_req_ids, otadm_features)

    # --- Final bundle ---
    bundle = {
        "meta": {
            "focus_plan": FOCUS_PLAN,
            "mno": "VZW",
            "release": "2026_feb",
            "graph_stats": graph_stats,
            "vector_stats": vector_stats,
            "generated_from": "data/graph/knowledge_graph.json + data/taxonomy + data/resolved + data/vectorstore/chroma.sqlite3",
        },
        "taxonomy": {
            "global": taxonomy,
            "otadm_features": otadm_features,
        },
        "xrefs": xrefs,
        "subgraph": {
            "nodes": sub_nodes,
            "edges": sub_edges,
            "edge_details": edge_details,
        },
        "requirements": req_details,
        "standards_index": ref_index,
        "edge_type_descriptions": EDGE_TYPE_DESC,
        "node_type_descriptions": NODE_TYPE_DESC,
        "query_simulations": query_sims,
    }

    # Write as JS (inline so HTML loads via file://)
    OUT.write_text(
        "// Auto-generated. Do not edit by hand. Re-run build_viz_data.py to refresh.\n"
        "window.NORA_DATA = " + json.dumps(bundle, indent=2) + ";\n",
        encoding="utf-8",
    )
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT} ({size_kb:.1f} KB)")
    print(f"  Nodes in subgraph: {len(sub_nodes)}")
    print(f"  Edges in subgraph: {len(sub_edges)}")
    print(f"  Requirements with RAG chunks: {sum(1 for r in req_details.values() if r['rag_chunk'])}")
    print(f"  Query simulations: {len(query_sims)}")


def _node_label(node):
    if not node:
        return "?"
    t = node.get("node_type")
    if t == "Requirement":
        return f"{node.get('section_number','?')} {node.get('title','')[:40]}"
    if t == "Feature":
        return node.get("name", node.get("feature_id", "?"))
    if t == "Plan":
        return node.get("plan_name", node.get("plan_id", "?"))
    if t == "Standard_Section":
        return f"TS {node.get('spec','?')} R{node.get('release_num','?')}"
    return node.get("name", node.get("id", "?"))


EDGE_TYPE_DESC = {
    "has_release": "MNO publishes this release bundle.",
    "contains_plan": "This release contains this requirement plan (spec document).",
    "belongs_to": "Requirement belongs to this plan.",
    "parent_of": "Section-hierarchy parent relationship. Derived from the document's section numbering (e.g., 1.4 parent_of 1.4.1).",
    "maps_to": "Requirement maps to a taxonomy feature. 'primary' = feature is the core subject; 'secondary' = feature is mentioned/related.",
    "depends_on": "Requirement depends on another requirement (internal cross-reference) or an external plan. Derived from explicit references found during resolution.",
    "references_standard": "Requirement cites a 3GPP/GSMA standard section. Resolved during the standards-reference extraction stage.",
    "shared_standard": "Two plans reference the same standard section (induced edge).",
    "parent_section": "Standards-section hierarchy (e.g., TS 24.301 §5 parent of §5.1).",
}

NODE_TYPE_DESC = {
    "MNO": "Mobile Network Operator (e.g., Verizon).",
    "Release": "A dated specification release (e.g., 2026_feb).",
    "Plan": "A requirement plan (one spec document).",
    "Requirement": "A single normative/descriptive requirement section.",
    "Feature": "A taxonomy feature — a cross-document topic the requirements map to.",
    "Standard_Section": "A referenced 3GPP or other standard section.",
}


def build_query_sims(kg, req_details, otadm_req_ids, otadm_features):
    """Precompute query traces backed by real requirement IDs in the KG."""

    # Helper: find a req_id -> full graph id
    def rid_to_id(short_rid):
        for k, v in req_details.items():
            if v["req_id"] == short_rid:
                return k
        return None

    # =========================================================
    # Q1: "Explain ADD requirements"
    # ADD = (Mobile) Automatic Device Detection. The defining section
    # is §1.5.1.5.1 (APN MANAGEMENT), with ADD specifics in its subsections.
    # =========================================================
    q1_primary_ids = [
        "VZ_REQ_LTEOTADM_22992",  # §1.5.1.5.1 APN MANAGEMENT — defines ADD
        "VZ_REQ_LTEOTADM_7672",   # §1.5.1.3.9 DevDetail Subtree — "ADD flow requires FwV"
        "VZ_REQ_LTEOTADM_7685",   # §1.5.1.5.3 Functionality For Device Connectivity Management — APN tree used by ADD
    ]
    q1_matched = [rid_to_id(r) for r in q1_primary_ids if rid_to_id(r)]

    q1 = {
        "id": "q1",
        "title": "Concept lookup: ADD requirements in LTEOTADM",
        "user_query": "Explain ADD requirements.",
        "intent": "concept_lookup",
        "classification_rationale": (
            "'ADD' is not a taxonomy feature — it's a domain acronym. The intent classifier falls through feature-match and "
            "routes the query as a concept lookup: (a) hybrid KG lookup using acronym expansion + keyword filtering, "
            "(b) vector search biased toward the LTEOTADM plan. This is a common case where pure taxonomy traversal "
            "is not enough — we need both graph scope and text-level retrieval."
        ),
        "kg_traversal": [
            {
                "step": 1,
                "action": "Acronym resolution via taxonomy keywords + glossary",
                "query": "MATCH feature WHERE 'ADD' in feature.keywords  // no hit; fall through to keyword expansion",
                "result": "'ADD' not a feature. Acronym expander returns: 'Automatic Device Detection', 'Mobile Automatic Device Detection'. The closest taxonomy features are OTA_DM + APN_PROVISIONING.",
                "nodes_visited": ["feature:OTA_DM", "feature:APN_PROVISIONING"],
            },
            {
                "step": 2,
                "action": "KG filter: requirements in LTEOTADM with text mentioning 'ADD' (word-boundary) or 'Automatic Device Detection'",
                "query": "MATCH (r:Requirement {plan_id:'LTEOTADM'}) WHERE r.text =~ '(?i)\\\\bADD\\\\b|Automatic Device Detection'",
                "result": f"Found {len(q1_matched)} requirement nodes; §1.5.1.5.1 APN MANAGEMENT is the anchor (the ADD definition lives in its subsections 1.5.1.5.1.1 – 1.5.1.5.1.7).",
                "nodes_visited": q1_matched,
            },
            {
                "step": 3,
                "action": "Hierarchy hydration: walk parent_of to surface containing section + sibling subsections",
                "query": "MATCH (r:Requirement {req_id:'VZ_REQ_LTEOTADM_22992'})-[:parent_of*0..2]-(ctx) RETURN ctx",
                "result": "§1.5.1.5.1 APN MANAGEMENT identified as parent concept. Subsections describe: ADD background, ADD Flow Requirements, APN Service Availability, ADD Flow Diagram, and APN Management after SIM change.",
                "nodes_visited": q1_matched,
            },
        ],
        "vector_search": {
            "query_text": "ADD Automatic Device Detection requirements flow APN management",
            "filter": {"plan_id": "LTEOTADM"},
            "top_k": 5,
            "top_results": [
                _vs_row(req_details, rid, sim)
                for rid, sim in zip(q1_matched + [rid_to_id("VZ_REQ_LTEOTADM_31778")], [0.87, 0.74, 0.68, 0.61])
                if rid
            ],
        },
        "fusion": {
            "strategy": "Reciprocal Rank Fusion (RRF) between KG hits (weighted higher — they contain the ADD definition verbatim) and vector top-k (promotes content-rich chunks).",
            "final_order": q1_matched,
            "note": "KG hit §1.5.1.5.1 is the authoritative definition; vector search confirms and surfaces the DevDetail+APN-tree subsections that ADD depends on.",
        },
        "llm_prompt": None,
        "llm_response": None,
    }
    q1["llm_prompt"] = _build_llm_prompt(
        user_query=q1["user_query"],
        contexts=[
            _ctx_row(req_details, rid) for rid in q1_matched[:3]
        ],
    )
    q1["llm_response"] = _synth_response_add()

    # =========================================================
    # Q2: "What kind of IP connectivity shall be used by DM client to connect to server?"
    # Answer lives in §1.5.1.4.3 (SUPPORT FOR IPV6 CONNECTIVITY) with supporting
    # context from §1.5.1.2 (OTADM CLIENT) and §1.5.1.5.3.4 (IP node in APN tree).
    # =========================================================
    q2_primary_ids = [
        "VZ_REQ_LTEOTADM_7679",   # §1.5.1.4.3 SUPPORT FOR IPV6 CONNECTIVITY — THE answer
        "VZ_REQ_LTEOTADM_22981",  # §1.5.1.2 OTADM CLIENT — client baseline
        "VZ_REQ_LTEOTADM_7685",   # §1.5.1.5.3 Connectivity Mgmt — APN IP type node
    ]
    q2_matched = [rid_to_id(r) for r in q2_primary_ids if rid_to_id(r)]

    q2 = {
        "id": "q2",
        "title": "Specification lookup: IP connectivity between DM client and server",
        "user_query": "What kind of IP connectivity shall be used by DM client to connect to server?",
        "intent": "specification_lookup",
        "classification_rationale": (
            "Semantic query about a concrete device behaviour ('IP connectivity', 'DM client', 'server'). Route: "
            "vector search is the primary retriever (text is rich in specifics); KG is used to (a) scope to LTEOTADM, "
            "(b) confirm the matched requirements are under the OTADM client / connectivity sections, not unrelated ones, "
            "(c) attach the Class-2 APN PDN context which is a sibling section — pure text retrieval might miss that the "
            "answer depends on Class 2 APN being available."
        ),
        "kg_traversal": [
            {
                "step": 1,
                "action": "Scope by taxonomy features",
                "query": "MATCH (f:Feature) WHERE f.keywords ~ 'IP|PDN|connectivity|DM' RETURN f",
                "result": "Matched features: OTA_DM (primary), APN_PROVISIONING (secondary), BEARER_MANAGEMENT (secondary). These three features guide both KG filter and vector filter.",
                "nodes_visited": ["feature:OTA_DM", "feature:APN_PROVISIONING", "feature:BEARER_MANAGEMENT"],
            },
            {
                "step": 2,
                "action": "KG candidate filter: reqs under 'OTADM CLIENT' (§1.5.1.2) and its descendants that mention IPv4/IPv6/PDN",
                "query": "MATCH (r:Requirement {plan_id:'LTEOTADM'}) WHERE r.text =~ '(?i)IPv6|IPv4|PDN Connection|DM Client'",
                "result": f"Top KG candidates: {', '.join('§' + req_details[rid]['section_number'] for rid in q2_matched if rid)}",
                "nodes_visited": q2_matched,
            },
            {
                "step": 3,
                "action": "Verify: check that §1.5.1.4.3 sits under the CLIENT section (not e.g. server side)",
                "query": "MATCH (r {req_id:'VZ_REQ_LTEOTADM_7679'})-[:parent_of*]->(anc:Requirement) RETURN anc",
                "result": "§1.5.1.4.3 is a child of §1.5.1.4 (SESSIONS) which is a child of §1.5.1 (OTADM requirements) — client-side. Good.",
                "nodes_visited": q2_matched[:1],
            },
        ],
        "vector_search": {
            "query_text": "IP connectivity DM client OMADM server IPv6 IPv4 PDN",
            "filter": {"plan_id": "LTEOTADM"},
            "top_k": 5,
            "top_results": [
                _vs_row(req_details, rid, sim)
                for rid, sim in zip(q2_matched, [0.91, 0.72, 0.68])
                if rid
            ],
        },
        "fusion": {
            "strategy": "RRF merge — vector search ranks §1.5.1.4.3 first (sim=0.91, explicit IPv6/IPv4 mention); KG confirms it's the only client-side IP spec section; §1.5.1.2 and §1.5.1.5.3 attached as background context.",
            "final_order": q2_matched,
            "note": "This is the classic KG+RAG sweet spot — RAG finds the answer text, KG confirms scope and attaches necessary context.",
        },
        "llm_prompt": None,
        "llm_response": None,
    }
    q2["llm_prompt"] = _build_llm_prompt(
        user_query=q2["user_query"],
        contexts=[_ctx_row(req_details, rid) for rid in q2_matched[:3]],
    )
    q2["llm_response"] = _synth_response_ip()

    return [q1, q2]


def _vs_row(req_details, rid, sim):
    if not rid or rid not in req_details:
        return None
    d = req_details[rid]
    chunk = (d["rag_chunk"] or {}).get("chunk_text", "")
    return {
        "req_id": d["req_id"],
        "section": d["section_number"],
        "title": d["title"],
        "similarity": sim,
        "chunk_preview": chunk[:280],
    }


def _ctx_row(req_details, rid):
    d = req_details[rid]
    return {
        "req_id": d["req_id"],
        "section": d["section_number"],
        "hierarchy": " > ".join(d["hierarchy_path"] or []),
        "text": ((d["rag_chunk"] or {}).get("chunk_text") or d["text"] or "")[:700],
    }


def _synth_response_add():
    return (
        "ADD stands for **(Mobile) Automatic Device Detection** — an APN Management server process "
        "triggered when a new UICC/SIM is associated with a device (either out-of-box or an existing "
        "UICC moved to a new device).\n\n"
        "**Core flow** [Req VZ_REQ_LTEOTADM_22992 §1.5.1.5.1]:\n"
        "1. UICC activation triggers the SIMOTA server to send an ADD message to the SDM server.\n"
        "2. The SDM server associates the device IMEI with the activated MDN and sends a Package 0 (SMS) to start the session.\n"
        "3. The server reads the device's LTE APN parameters (APN Id, name, IP type) across all 4 APN classes via OMA-DM 'Get', then writes network values via 'Replace' and triggers 'Execute' to enable them.\n"
        "4. After the APN refresh, the OTADM server checks whether a firmware update is needed (inspecting the FwV leaf in DevDetail).\n\n"
        "**Key device obligations**:\n"
        "• The device must be prepared to respond to any APN read/write issued during the ADD session — VZW does NOT prescribe the sequence or count of APNs [Req VZ_REQ_LTEOTADM_22992 §1.5.1.5.1.2 'ADD Flow Requirements'].\n"
        "• The ./DevDetail/FwV leaf must be populated — APN Management and ADD flow require it [Req VZ_REQ_LTEOTADM_7672 §1.5.1.3.9 'DevDetail Subtree'].\n"
        "• After SIM change, the device starts a 2-minute timer waiting for a WAP PUSH; if it arrives, the DM session proceeds; if it doesn't (an 'ADD miss'), the device initiates the session itself on timer expiry [Req VZ_REQ_LTEOTADM_22992 §1.5.1.5.1.7 'APN Management after SIM change'].\n\n"
        "**Supporting tree**: the APN values manipulated during ADD live in the ConnMO subtree and are exercised through the nodes enumerated in §1.5.1.5.3 (APN Id, APN Name, IP type, Enabled, etc.) [Req VZ_REQ_LTEOTADM_7685]."
    )


def _synth_response_ip():
    return (
        "The DM Client shall connect to the OMA-DM server using **IPv6 preferred, IPv4 fallback**, "
        "both over the **Class 2 APN's PDN Connection** [Req VZ_REQ_LTEOTADM_7679 §1.5.1.4.3].\n\n"
        "**Selection rules** [§1.5.1.4.3.1]:\n"
        "• DM Client shall support IPv6 connectivity with the OMA-DM server over the Class 2 APN's PDN Connection — this is in addition to existing IPv4 support.\n"
        "• If DNS returns an IPv6 (AAAA) record, the device prefers IPv6 over IPv4 for the DM server connection.\n"
        "• If DNS returns no IPv6 record, the device falls back to the IPv4 ('A' record) address.\n"
        "• IPv6 shall be supported for all aspects of OMA-DM communication over IP.\n\n"
        "**Failure handling** [§1.5.1.4.3.2 Connection Setup Failure]:\n"
        "• If Class 2 PDN cannot be established (inadequate LTE coverage), follow the document's retry requirements.\n"
        "• If Class 2 PDN is up but the IPv6 path to the DM server fails, the device MUST NOT disconnect the PDN; instead it falls back to IPv4 and retries once. If that also fails, the DM-retry requirements apply.\n\n"
        "**Baseline**: the client is an OMA-DM conformant OTADM client [Req VZ_REQ_LTEOTADM_22981 §1.5.1.2], so all transport is standard OMA-DM over HTTP on top of this IP bearer. The APN-tree 'IP' node (Get/Replace on IP type) lets the server read and set the APN's IP stack as IPv4, IPv6, or dual-stack [Req VZ_REQ_LTEOTADM_7685 §1.5.1.5.3.4]."
    )


def _build_llm_prompt(user_query, contexts):
    ctx_block = "\n\n".join(
        f"[Context {i+1}] Req {c['req_id']} (§{c['section']}) — {c['hierarchy']}\n{c['text']}"
        for i, c in enumerate(contexts)
    )
    return (
        "You are NORA, an assistant answering questions about VZW LTE device requirements.\n"
        "Answer STRICTLY from the provided contexts. Cite the Req ID and section for each claim.\n"
        "If a fact is not in the contexts, say so — do not invent.\n\n"
        f"USER QUESTION:\n{user_query}\n\n"
        f"CONTEXTS (top-ranked RAG chunks, scoped to LTEOTADM):\n{ctx_block}\n\n"
        "ANSWER (with inline citations in the form [Req VZ_REQ_LTEOTADM_XXXX §X.Y]):"
    )


if __name__ == "__main__":
    main()
