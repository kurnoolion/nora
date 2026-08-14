"""Tests for the NORA → BEIR adapter — multi-cell partitioning (multi-mno-sira).

Focused on the cell-identity + fail-loud behavior added for multi-MNO SIRA
(D-DRAFT-3/5/6). The single-dataset emission path predates this strand and
is exercised end-to-end via the SIRA sandbox runs, not unit-tested here.
"""

from __future__ import annotations

import pytest

import json

import io

from sandbox.adapter.nora_to_beir import (
    _cell_dirname,
    _emit_corpus,
    _emit_multi_cell,
    _emit_multigranularity_rows,
    _filter_trees_by_only,
    _parse_only,
    _partition_trees_by_cell,
    _RELEASE_RE,
)


def _read_ids(path) -> set:
    with open(path, encoding="utf-8") as f:
        return {json.loads(line)["_id"] for line in f if line.strip()}


def _tree(mno: str, release: str, plan_id: str = "PLAN") -> dict:
    return {"mno": mno, "release": release, "plan_id": plan_id, "requirements": []}


def _tree_with_req(mno: str, release: str, plan_id: str, req_id: str) -> dict:
    return {
        "mno": mno, "release": release,
        "plan_id": plan_id, "plan_name": f"{plan_id} plan",
        "requirements": [{
            "req_id": req_id, "title": f"{req_id} title",
            "text": f"body of {req_id}", "section_number": "1.1",
        }],
    }


# ── _RELEASE_RE — the MMMYYYY convention ──────────────────────────

@pytest.mark.parametrize("good", ["Feb2026", "Jan2025", "Dec2099", "Oct2025"])
def test_release_re_accepts_mmmyyyy(good):
    assert _RELEASE_RE.match(good)


@pytest.mark.parametrize("bad", [
    "OA-baseline",      # the legacy free-form label
    "February 2026",    # the document release_date format (display-only)
    "Feb-2026",         # punctuation
    "feb2026",          # lowercase month
    "FEB2026",          # uppercase month
    "Feb26",            # 2-digit year
    "Q1-2026",          # quarter phrasing
    "Feb2026x",         # trailing junk
    "",                 # empty
])
def test_release_re_rejects_non_mmmyyyy(bad):
    assert not _RELEASE_RE.match(bad)


# ── _cell_dirname — source-case-preserved naming ──────────────────

def test_cell_dirname_double_underscore_source_case():
    assert _cell_dirname(("VZW", "Feb2026")) == "VZW__Feb2026"
    assert _cell_dirname(("TMO", "Jan2026")) == "TMO__Jan2026"


# ── _partition_trees_by_cell ──────────────────────────────────────

def test_partition_groups_by_cell():
    trees = [
        _tree("VZW", "Feb2026", "A"),
        _tree("VZW", "Feb2026", "B"),
        _tree("TMO", "Jan2026", "C"),
        _tree("VZW", "Oct2025", "D"),
    ]
    cells = _partition_trees_by_cell(trees)
    assert set(cells) == {("VZW", "Feb2026"), ("TMO", "Jan2026"), ("VZW", "Oct2025")}
    assert [t["plan_id"] for t in cells[("VZW", "Feb2026")]] == ["A", "B"]
    assert len(cells[("VZW", "Oct2025")]) == 1


def test_partition_fail_loud_on_non_mmmyyyy_release():
    trees = [_tree("VZW", "Feb2026", "ok"), _tree("VZW", "OA-baseline", "bad")]
    with pytest.raises(ValueError) as exc:
        _partition_trees_by_cell(trees)
    msg = str(exc.value)
    assert "OA-baseline" in msg          # names the offender
    assert "MMMYYYY" in msg              # names the expected shape
    assert "input directory" in msg      # points at the fix location


def test_partition_fail_loud_collects_all_violations():
    trees = [
        _tree("VZW", "bad1", "p1"),
        _tree("VZW", "Feb2026", "ok"),
        _tree("TMO", "bad2", "p2"),
    ]
    with pytest.raises(ValueError) as exc:
        _partition_trees_by_cell(trees)
    msg = str(exc.value)
    assert "bad1" in msg and "bad2" in msg   # both reported in one pass
    assert "2 tree(s)" in msg


def test_partition_fail_loud_on_missing_mno():
    trees = [_tree("", "Feb2026", "no-mno")]
    with pytest.raises(ValueError, match="missing MNO"):
        _partition_trees_by_cell(trees)


# ── _emit_multi_cell — on-disk layout + partition end-to-end ──────

def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_emit_multi_cell_layout_and_partition(tmp_path):
    trees = [
        _tree_with_req("VZW", "Feb2026", "A", "req:A:1"),
        _tree_with_req("VZW", "Feb2026", "B", "req:B:1"),
        _tree_with_req("TMO", "Jan2026", "C", "req:C:1"),
    ]
    names = _emit_multi_cell(
        trees, tmp_path, section_max_depth=2, wipe_index=False, wipe_all=False,
    )
    # two cells, source-case-preserved names
    assert sorted(names) == ["TMO__Jan2026", "VZW__Feb2026"]

    vzw = tmp_path / "VZW__Feb2026" / "raw"
    tmo = tmp_path / "TMO__Jan2026" / "raw"
    # each cell has the four files; queries/qrels carry a single dummy
    # index-build row (keeps SIRA's bm25 eval+pick-best alive so
    # index/best is produced) targeting a real corpus id.
    for raw in (vzw, tmo):
        assert (raw / "corpus.jsonl").is_file()
        assert (raw / "metadata.json").is_file()
        q = _read_jsonl(raw / "queries-test.jsonl")
        qr = _read_jsonl(raw / "qrels-test.jsonl")
        assert len(q) == 1 and q[0]["_id"] == "_idxbuild_0"
        assert len(qr) == 1 and qr[0]["query-id"] == "_idxbuild_0"
        corpus_ids = {r["_id"] for r in _read_jsonl(raw / "corpus.jsonl")}
        assert qr[0]["corpus-id"] in corpus_ids   # qrel target is real

    # partition correctness: VZW cell holds A+B's reqs, TMO holds C's.
    # (doc:/section: multigranularity rows also present — tested in
    # plan-aware-sira; here we only assert the per-req partitioning.)
    vzw_ids = {r["_id"] for r in _read_jsonl(vzw / "corpus.jsonl")}
    tmo_ids = {r["_id"] for r in _read_jsonl(tmo / "corpus.jsonl")}
    assert {"req:A:1", "req:B:1"} <= vzw_ids
    assert "req:C:1" not in vzw_ids
    assert "req:C:1" in tmo_ids
    assert "req:A:1" not in tmo_ids


def test_emit_multi_cell_metadata_name_is_cell_dir(tmp_path):
    trees = [_tree_with_req("VZW", "Feb2026", "A", "req:A:1")]
    _emit_multi_cell(trees, tmp_path, section_max_depth=2,
                     wipe_index=False, wipe_all=False)
    meta = json.loads((tmp_path / "VZW__Feb2026" / "raw" / "metadata.json").read_text())
    assert meta["name"] == "VZW__Feb2026"


def test_emit_multi_cell_same_reqid_isolated_across_cells(tmp_path):
    # The SAME req_id in two cells (release-diff case) must NOT collide —
    # each cell is its own corpus. (D-DRAFT-4 composite identity at the
    # ingest layer: cells are physically separate.)
    trees = [
        _tree_with_req("VZW", "Oct2025", "A", "req:FOO:5.1"),
        _tree_with_req("VZW", "Feb2026", "A", "req:FOO:5.1"),
    ]
    _emit_multi_cell(trees, tmp_path, section_max_depth=2,
                     wipe_index=False, wipe_all=False)
    oct_ids = {r["_id"] for r in _read_jsonl(
        tmp_path / "VZW__Oct2025" / "raw" / "corpus.jsonl")}
    feb_ids = {r["_id"] for r in _read_jsonl(
        tmp_path / "VZW__Feb2026" / "raw" / "corpus.jsonl")}
    # same req_id present in BOTH cells, independently — not deduped away
    assert "req:FOO:5.1" in oct_ids
    assert "req:FOO:5.1" in feb_ids


# ── _emit_multigranularity_rows — per-req plan grouping (D-DRAFT-1) ──
# and leading-id section derivation (D-DRAFT-2 §4 adapter fix).


def _emit_multi(trees, section_max_depth=2):
    buf = io.StringIO()
    n_doc, n_section = _emit_multigranularity_rows(trees, buf, section_max_depth)
    rows = {r["_id"]: r for r in
            (json.loads(l) for l in buf.getvalue().splitlines() if l.strip())}
    return rows, n_doc, n_section


# A leading-id-shaped tree (D-DRAFT-2): headings carry a section_number but
# NO req_id; requirements carry a req_id + parent_section + per-req plan_id
# but no section_number of their own. Two plans (FOO, BAR) in one document.
def _leading_id_tree():
    return {
        "mno": "MNOB", "release": "Jun2026", "plan_id": "", "plan_name": "",
        "detection_mode": "leading_id_body",
        "requirements": [
            {"req_id": "", "section_number": "1", "title": "General",
             "parent_section": "", "plan_id": ""},
            {"req_id": "", "section_number": "1.1", "title": "Device",
             "parent_section": "1", "plan_id": ""},
            {"req_id": "ABC-FOO-001", "section_number": "", "title": "",
             "parent_section": "1.1", "hierarchy_path": ["General", "Device"],
             "plan_id": "FOO"},
            {"req_id": "ABC-FOO-002", "section_number": "", "title": "",
             "parent_section": "1.1", "hierarchy_path": ["General", "Device"],
             "plan_id": "FOO"},
            {"req_id": "", "section_number": "1.2", "title": "Network",
             "parent_section": "1", "plan_id": ""},
            {"req_id": "ABC-BAR-010", "section_number": "", "title": "",
             "parent_section": "1.2", "hierarchy_path": ["General", "Network"],
             "plan_id": "BAR"},
        ],
    }


class TestMultigranularityPerReqPlan:
    def test_one_document_yields_one_doc_row_per_plan(self):
        rows, n_doc, _ = _emit_multi([_leading_id_tree()])
        assert "doc:FOO" in rows and "doc:BAR" in rows
        assert n_doc == 2

    def test_doc_rows_carry_only_their_plans_reqs(self):
        rows, _, _ = _emit_multi([_leading_id_tree()])
        assert "ABC-FOO-001" in rows["doc:FOO"]["text"]
        assert "ABC-FOO-002" in rows["doc:FOO"]["text"]
        assert "ABC-BAR-010" not in rows["doc:FOO"]["text"]
        assert "ABC-BAR-010" in rows["doc:BAR"]["text"]
        assert "ABC-FOO-001" not in rows["doc:BAR"]["text"]

    def test_section_rows_emitted_for_leading_id_corpus(self):
        # The §4 gap: previously zero section rows because headings (no
        # req_id) were skipped. Now reqs map to their parent_section.
        rows, _, n_section = _emit_multi([_leading_id_tree()])
        assert "section:FOO:1.1" in rows
        assert "section:FOO:1" in rows
        assert "section:BAR:1.2" in rows
        assert n_section >= 3

    def test_section_titles_come_from_heading_catalog(self):
        # Heading nodes (id-less) still supply the section title.
        rows, _, _ = _emit_multi([_leading_id_tree()])
        assert "Device" in rows["section:FOO:1.1"]["title"]
        assert "Network" in rows["section:BAR:1.2"]["title"]

    def test_section_descendants_grouped_by_parent_section(self):
        rows, _, _ = _emit_multi([_leading_id_tree()])
        assert "ABC-FOO-001" in rows["section:FOO:1.1"]["text"]
        assert "ABC-FOO-002" in rows["section:FOO:1.1"]["text"]
        assert "ABC-BAR-010" not in rows["section:FOO:1.1"]["text"]


class TestMultigranularitySinglePlanBackCompat:
    """Heading-anchored single-plan tree (MNO-A shape): one doc row, and
    section rows keyed off the reqs' own section_numbers — unchanged."""

    def _verizon_tree(self):
        return {
            "plan_id": "FOOPLAN", "plan_name": "Foo Plan",
            "requirements": [
                {"req_id": "R-1", "section_number": "5", "title": "Sec5",
                 "parent_section": "", "plan_id": "FOOPLAN"},
                {"req_id": "R-2", "section_number": "5.1", "title": "Sec51",
                 "parent_section": "5", "plan_id": "FOOPLAN"},
            ],
        }

    def test_single_doc_row(self):
        rows, n_doc, _ = _emit_multi([self._verizon_tree()])
        assert n_doc == 1 and "doc:FOOPLAN" in rows

    def test_section_rows_use_own_section_number(self):
        rows, _, _ = _emit_multi([self._verizon_tree()])
        assert "section:FOOPLAN:5" in rows
        assert "section:FOOPLAN:5.1" in rows
        # Section 5 holds both; 5.1 holds only R-2.
        assert "R-1" in rows["section:FOOPLAN:5"]["text"]
        assert "R-2" in rows["section:FOOPLAN:5"]["text"]
        assert "R-1" not in rows["section:FOOPLAN:5.1"]["text"]

    def test_heading_mode_table_anchored_excluded_and_no_plan_split(self):
        # Gating: in heading mode (default), a req with no section_number of
        # its own (MNO-A table-anchored) must NOT enter section rows via
        # parent_section, and a differing plan_id must NOT spawn a second doc
        # row — both reqs stay under the single document plan.
        tree = {
            "plan_id": "FOOPLAN", "plan_name": "Foo Plan",
            "requirements": [
                {"req_id": "R-1", "section_number": "5", "title": "Sec5",
                 "parent_section": "", "plan_id": "FOOPLAN"},
                {"req_id": "T-9", "section_number": "", "title": "",
                 "parent_section": "5", "plan_id": "OTHERPLAN"},  # table-anchored, diff plan
            ],
        }
        rows, n_doc, _ = _emit_multi([tree])
        assert n_doc == 1 and "doc:FOOPLAN" in rows
        assert "doc:OTHERPLAN" not in rows               # no plan split
        assert "T-9" in rows["doc:FOOPLAN"]["text"]      # still a doc-row pointer
        assert "T-9" not in rows["section:FOOPLAN:5"]["text"]  # excluded from section rows


# ── D-DRAFT-5: per-req Context baked into corpus rows ────────────────

def _ctx_sections():
    return {"5": ("Bands", "Ch5 intro."), "5.1": ("Frequency", "Freq intro."),
            "5.1.2": ("LTE", "LTE intro.")}


class TestBuildTextContext:
    def test_path_and_content_context_in_row(self):
        from sandbox.adapter.nora_to_beir import _build_text
        tree = {"build_context": "path_and_content", "detection_mode": "leading_id_body", "plan_id": "FOO"}
        req = {"req_id": "ABC-FOO-1", "title": "Band 13", "text": "Device shall support band 13.",
               "section_number": "", "parent_section": "5.1.2", "plan_id": "FOO"}
        out = _build_text(req, tree, None, {}, _ctx_sections())
        assert "[5 Bands]" in out and "[5.1.2 LTE]" in out    # bracketed section headers
        assert "Ch5 intro." in out and "LTE intro." in out    # ancestor bodies
        assert "Device shall support band 13." in out         # req body still present

    def test_path_mode_context_no_body(self):
        from sandbox.adapter.nora_to_beir import _build_text
        tree = {"build_context": "path", "detection_mode": "leading_id_body"}
        req = {"req_id": "ABC-FOO-1", "title": "T", "text": "body", "section_number": "",
               "parent_section": "5.1.2"}
        out = _build_text(req, tree, None, {}, _ctx_sections())
        assert "[Context: 5 Bands > 5.1 Frequency > 5.1.2 LTE]" in out
        assert "LTE intro." not in out                        # body excluded in path mode

    def test_none_mode_no_context_block(self):
        from sandbox.adapter.nora_to_beir import _build_text
        tree = {"build_context": "none"}
        req = {"req_id": "ABC-FOO-1", "title": "T", "text": "body", "parent_section": "5.1.2"}
        out = _build_text(req, tree, None, {}, _ctx_sections())
        assert "[Context:" not in out and "[5 Bands]" not in out


class TestBuildTextPlanStamp:
    """Requirement rows stamp the bare plan_id, never plan_name — enrichment
    resolves the taxonomy block as <plan_id>_features.json from this stamp
    (plan_name-shaped stamps made every lookup miss on one-doc-per-plan
    corpora), and the query service's plan dropdown lists req-row stamps."""

    def test_heading_mode_stamps_plan_id_over_plan_name(self):
        from sandbox.adapter.nora_to_beir import _build_text
        tree = {"plan_id": "FOOPLAN", "plan_name": "Reqs-FOOPLAN"}
        req = {"req_id": "R-1", "title": "T", "text": "body"}
        out = _build_text(req, tree, None, {})
        assert "**plan**: FOOPLAN\n" in out
        assert "Reqs-FOOPLAN" not in out

    def test_heading_mode_falls_back_to_plan_name(self):
        from sandbox.adapter.nora_to_beir import _build_text
        tree = {"plan_name": "Reqs-FOOPLAN"}
        req = {"req_id": "R-1", "title": "T", "text": "body"}
        out = _build_text(req, tree, None, {})
        assert "**plan**: Reqs-FOOPLAN" in out

    def test_leading_id_tree_fallback_prefers_plan_id(self):
        from sandbox.adapter.nora_to_beir import _build_text
        tree = {"detection_mode": "leading_id_body",
                "plan_id": "FOOPLAN", "plan_name": "Reqs-FOOPLAN"}
        req = {"req_id": "R-1", "title": "T", "text": "body", "plan_id": ""}
        out = _build_text(req, tree, None, {})
        assert "**plan**: FOOPLAN\n" in out


class TestBuildTextTables:
    """Tables are inlined into req.text by the parser (faithful order); the
    adapter must pass that text through and NOT re-serialize req.tables (which
    would duplicate the table and lose its document position)."""

    def test_inline_tables_in_text_flow_through_once(self):
        from sandbox.adapter.nora_to_beir import _build_text
        tree = {"build_context": "none", "detection_mode": "leading_id_body"}
        # Faithful parser output: intro → table → note, all inline in `text`.
        body = (
            "Device shall support the following bands.\n"
            "| Band | BW (MHz) |\n| --- | --- |\n| n78 | 100 |\n| n77 | 100 |\n"
            "Note: FR2 bands are specified separately."
        )
        req = {"req_id": "ABC-FOO-1", "title": "NR SA FR1 Bands", "section_number": "86.3",
               "text": body, "plan_id": "FOO",
               "tables": [{"headers": ["Band", "BW (MHz)"],
                           "rows": [["n78", "100"], ["n77", "100"]]}]}
        out = _build_text(req, tree, None, {})
        assert "| n78 | 100 |" in out                       # band data present
        assert out.count("| n78 | 100 |") == 1              # NOT duplicated by the adapter
        # order preserved: intro before table before note
        assert out.index("following bands") < out.index("| n78 | 100 |") < out.index("FR2 bands")

    def test_no_tables_is_unchanged(self):
        from sandbox.adapter.nora_to_beir import _build_text
        tree = {"build_context": "none", "detection_mode": "leading_id_body"}
        req = {"req_id": "ABC-FOO-1", "title": "T", "text": "body", "plan_id": "FOO"}
        out = _build_text(req, tree, None, {})
        assert "|" not in out                          # no spurious table markup


# ── is_requirement filter: sections must not become per-req corpus rows ──

def test_emit_corpus_skips_structural_sections(tmp_path):
    # A requirement (is_requirement True) + a structural section (has a req_id
    # but is_requirement False) → only the requirement becomes a per-req row.
    tree = {
        "mno": "MNOC", "release": "Mar2026", "plan_id": "P", "plan_name": "P plan",
        "requirements": [
            {"req_id": "GP-REQ-1", "is_requirement": True, "title": "Req",
             "text": "body", "section_number": "1.1"},
            {"req_id": "GP-SEC-9", "is_requirement": False, "title": "Section",
             "text": "section body", "section_number": "1"},
        ],
    }
    out = tmp_path / "corpus.jsonl"
    _emit_corpus([tree], out)
    ids = _read_ids(out)
    assert "GP-REQ-1" in ids           # actual requirement kept
    assert "GP-SEC-9" not in ids        # structural section excluded from per-req rows


def test_emit_corpus_backcompat_no_flag_keeps_reqid_rows(tmp_path):
    # Older trees / corpora without the discriminator have no is_requirement key
    # → any node with a req_id is a corpus row, unchanged.
    tree = _tree_with_req("VZW", "Feb2026", "P", "REQ-1")   # no is_requirement key
    out = tmp_path / "corpus.jsonl"
    _emit_corpus([tree], out)
    assert "REQ-1" in _read_ids(out)


# ── --only cell filter ────────────────────────────────────────────

def test_parse_only_splits_mno_release_pairs():
    assert _parse_only("MNOC__Mar2026, MNOA__Feb2026") == [
        ("MNOC", "Mar2026"), ("MNOA", "Feb2026")]


def test_parse_only_rejects_missing_separator():
    with pytest.raises(ValueError, match="__"):
        _parse_only("MNOC/Mar2026")          # slash is not the separator


def test_filter_trees_by_only_keeps_requested_cells():
    trees = [
        _tree("MNOC", "Mar2026", "a"),
        _tree("MNOA", "Feb2026", "b"),
        _tree("MNOB", "Jan2026", "c"),
    ]
    kept = _filter_trees_by_only(trees, [("MNOC", "Mar2026")])
    assert [t["mno"] for t in kept] == ["MNOC"]


def test_filter_trees_by_only_case_insensitive():
    trees = [_tree("MNOC", "Mar2026", "a")]
    assert len(_filter_trees_by_only(trees, [("mnoc", "mar2026")])) == 1


def test_filter_trees_by_only_fail_loud_on_missing_cell():
    trees = [_tree("MNOC", "Mar2026", "a")]
    with pytest.raises(ValueError) as exc:
        _filter_trees_by_only(trees, [("MNOA", "Feb2026")])
    msg = str(exc.value)
    assert "MNOA/Feb2026" in msg            # names the missing cell
    assert "MNOC/Mar2026" in msg           # lists what's available


def test_print_skips_lists_section_and_duplicate_ids(tmp_path, capsys):
    tree1 = {
        "mno": "MNOC", "release": "Mar2026", "plan_id": "P", "plan_name": "P plan",
        "requirements": [
            {"req_id": "GP-REQ-1", "is_requirement": True, "title": "Req",
             "text": "b", "section_number": "1.1"},
            {"req_id": "GP-SEC-9", "is_requirement": False, "title": "Sec",
             "text": "s", "section_number": "1"},
        ],
    }
    tree2 = {  # repeats GP-REQ-1 → duplicate
        "mno": "MNOC", "release": "Mar2026", "plan_id": "P", "plan_name": "P plan",
        "requirements": [
            {"req_id": "GP-REQ-1", "is_requirement": True, "title": "Req dup",
             "text": "b2", "section_number": "1.1"},
        ],
    }
    _emit_corpus([tree1, tree2], tmp_path / "corpus.jsonl", print_skips=True)
    out = capsys.readouterr().out
    assert "is_requirement=False" in out and "GP-SEC-9" in out   # section listed
    assert "duplicate req_ids" in out and "GP-REQ-1" in out       # dup listed


def test_print_skips_off_by_default(tmp_path, capsys):
    tree = {
        "mno": "MNOC", "release": "Mar2026", "plan_id": "P", "plan_name": "P plan",
        "requirements": [{"req_id": "GP-SEC-9", "is_requirement": False,
                          "title": "Sec", "text": "s", "section_number": "1"}],
    }
    _emit_corpus([tree], tmp_path / "corpus.jsonl")   # print_skips defaults False
    assert "is_requirement=False" not in capsys.readouterr().out


def test_print_noid_samples_idless_nodes(tmp_path, capsys):
    reqs = [{"req_id": "", "title": f"noid {i}", "section_number": str(i)}
            for i in range(100)]
    reqs.append({"req_id": "GP-REQ-1", "is_requirement": True, "title": "R",
                 "text": "b", "section_number": "9"})
    tree = {"mno": "MNOC", "release": "Mar2026", "plan_id": "P",
            "plan_name": "P plan", "requirements": reqs}
    _emit_corpus([tree], tmp_path / "corpus.jsonl", print_noid=True)
    out = capsys.readouterr().out
    assert "100 no-id nodes" in out and "strided sample of 40" in out
    assert "noid 0" in out                       # first node is in the strided sample


def test_print_noid_off_by_default(tmp_path, capsys):
    tree = {"mno": "MNOC", "release": "Mar2026", "plan_id": "P", "plan_name": "P plan",
            "requirements": [{"req_id": "", "title": "x", "section_number": "1"}]}
    _emit_corpus([tree], tmp_path / "corpus.jsonl")
    assert "strided sample" not in capsys.readouterr().out


# ── SOURCE.json provenance (docker-distro lane model) ─────────────

def test_multi_cell_emits_source_json(tmp_path):
    from sandbox.adapter.nora_to_beir import _write_source_json
    _write_source_json(tmp_path, tmp_path / "env", ["MNOA__Feb2026"])
    src = json.loads((tmp_path / "SOURCE.json").read_text())
    assert src["cells_last_emitted"] == ["MNOA__Feb2026"]
    assert src["env_dir"].endswith("/env")
    assert "generated_at" in src and "repo_git_sha" in src


# ── emits are atomic (fresh inode per regeneration) ────────────────


def test_emit_corpus_breaks_hardlinks(tmp_path):
    """Regenerating corpus.jsonl lands on a fresh inode (temp + atomic
    rename), so a hardlink-shared snapshot keeps the prior bytes."""
    import os

    tree = _tree_with_req("MNOA", "Rel1", "P", "REQ-1")
    out = tmp_path / "corpus.jsonl"
    _emit_corpus([tree], out)
    snapshot = tmp_path / "label" / "corpus.jsonl"
    snapshot.parent.mkdir()
    os.link(out, snapshot)
    before = snapshot.read_text()

    tree2 = _tree_with_req("MNOA", "Rel1", "P", "REQ-2")
    _emit_corpus([tree, tree2], out)

    assert snapshot.read_text() == before      # snapshot untouched
    assert os.stat(out).st_nlink == 1          # link broken by the rename
    assert {"REQ-1", "REQ-2"} <= _read_ids(out)
    assert not (tmp_path / "corpus.jsonl.tmp").exists()
