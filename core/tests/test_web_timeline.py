"""Tests for the normalized query timeline (`core.src.web.timeline`).

The contract under test is the pair of honesty rules the timeline
exists to enforce: segments that never over-claim their coverage of
the wall clock, and skipped stages that stay absent instead of
rendering as instantaneous.
"""

from __future__ import annotations

from core.src.web.timeline import build_timeline


class TestEmptyCases:
    def test_none_stages_returns_none(self):
        assert build_timeline(None, 1000, "nora") is None

    def test_empty_stages_returns_none(self):
        assert build_timeline({}, 1000, "nora") is None

    def test_zero_total_returns_none(self):
        assert build_timeline({"analyze": 5}, 0, "nora") is None

    def test_none_total_returns_none(self):
        assert build_timeline({"analyze": 5}, None, "nora") is None

    def test_only_unknown_keys_returns_none(self):
        # A payload of keys from neither vocabulary draws nothing rather
        # than an all-unaccounted bar, which would be noise.
        assert build_timeline({"mystery_ms": 10}, 1000, "nora") is None


class TestNoraLane:
    def _tl(self):
        return build_timeline(
            {
                "analyze": 10,
                "resolve_scope": 5,
                "retrieve": 200,
                "assemble": 15,
                "synthesize": 600,
                "audit": 20,
                "total_ms": 850,
            },
            1000,
            "nora",
        )

    def test_segments_in_pipeline_order(self):
        slugs = [s["slug"] for s in self._tl()["segments"]]
        assert slugs == [
            "analyze", "resolve_scope", "retrieve",
            "assemble", "synthesize", "audit", "unaccounted",
        ]

    def test_total_ms_key_is_not_a_segment(self):
        assert "total_ms" not in {s["slug"] for s in self._tl()["segments"]}

    def test_unaccounted_is_wall_clock_minus_measured(self):
        tl = self._tl()
        assert tl["measured_ms"] == 850
        assert tl["unaccounted_ms"] == 150

    def test_segment_ms_sums_to_total(self):
        tl = self._tl()
        assert sum(s["ms"] for s in tl["segments"]) == tl["total_ms"]

    def test_percentages_are_of_wall_clock(self):
        by_slug = {s["slug"]: s for s in self._tl()["segments"]}
        assert by_slug["synthesize"]["pct"] == 60.0
        assert by_slug["unaccounted"]["pct"] == 15.0

    def test_bypassed_stage_produces_no_segment(self):
        slugs = {s["slug"] for s in self._tl()["segments"]}
        for skipped in ("graph_scope", "rewrite", "threshold", "group"):
            assert skipped not in slugs

    def test_zero_ms_stage_that_ran_is_kept(self):
        # 0 is a real measurement (a sub-millisecond stage); only an
        # ABSENT key means "did not run".
        tl = build_timeline({"analyze": 0, "synthesize": 500}, 1000, "nora")
        assert "analyze" in {s["slug"] for s in tl["segments"]}

    def test_bands_aggregate_stages(self):
        bands = {b["band"]: b["ms"] for b in self._tl()["bands"]}
        assert bands["prep"] == 15          # analyze + resolve_scope
        assert bands["retrieval"] == 200    # retrieve
        assert bands["synthesis"] == 615    # assemble + synthesize
        assert bands["post"] == 20          # audit
        assert bands["unaccounted"] == 150


class TestSiraLane:
    def _tl(self):
        return build_timeline(
            {"expand_ms": 100, "search_ms": 300, "rerank_ms": 400,
             "synth_ms": 1200},
            2500,
            "sira",
        )

    def test_sira_keys_are_mapped(self):
        labels = [s["label"] for s in self._tl()["segments"]]
        assert labels == [
            "Expand", "Search", "Rerank", "Synthesize", "Unaccounted",
        ]

    def test_sira_round_trip_shows_as_unaccounted(self):
        # 2500 wall clock vs 2000 measured — the HTTP round-trip the
        # SIRA numbers do not cover has to be visible, not absorbed.
        assert self._tl()["unaccounted_ms"] == 500

    def test_sira_bands_match_nora_vocabulary(self):
        bands = {b["band"] for b in self._tl()["bands"]}
        assert bands == {"prep", "retrieval", "synthesis", "unaccounted"}

    def test_nora_slugs_ignored_on_sira_lane(self):
        tl = build_timeline(
            {"search_ms": 100, "synthesize": 900}, 1000, "sira",
        )
        assert [s["slug"] for s in tl["segments"]] == ["search_ms", "unaccounted"]


class TestPartialRenders:
    """The partial has to survive Jinja for both lanes.

    A template-only change cannot be caught by the builder tests above,
    and the answer card is the one place this feature is visible.
    """

    def _render(self, timeline, row_id=1):
        from core.src.web.app import templates

        return templates.env.get_template("test/_timeline.html").render(
            timeline=timeline, row_id=row_id,
        )

    def test_nora_lane_renders_ran_stages_only(self):
        html = self._render(build_timeline(
            {"analyze": 10, "graph_scope": 40, "retrieve": 200,
             "synthesize": 600, "audit": 20},
            1000, "nora",
        ))
        assert "Graph scope" in html
        assert "Synthesize" in html
        assert "Unaccounted" in html
        # Stage 3.5 never ran, so its label must not appear anywhere.
        assert "Rewrite" not in html

    def test_sira_lane_renders(self):
        html = self._render(build_timeline(
            {"expand_ms": 100, "search_ms": 300, "rerank_ms": 400,
             "synth_ms": 1200},
            2500, "sira",
        ))
        assert "Rerank" in html
        assert "Unaccounted" in html

    def test_total_rendered_in_seconds(self):
        html = self._render(build_timeline({"synthesize": 900}, 2500, "nora"))
        assert "2.5 s" in html

    def test_sub_second_total_rendered_in_milliseconds(self):
        # A warm cache hit is single-digit ms; "0.0 s" reads as a
        # broken timer rather than a fast query.
        html = self._render(build_timeline({"retrieve": 8}, 9, "nora"))
        assert "9 ms" in html
        assert "0.0 s" not in html

    def test_collapse_id_is_lane_scoped_when_row_id_is_none(self):
        """The merged tab composes BOTH lanes into one DOM, and row_id
        is None whenever record_qa fails. Keyed on row_id alone, the two
        cards would collide and Bootstrap would toggle the wrong table.
        """
        nora = self._render(
            build_timeline({"synthesize": 900}, 1000, "nora"), row_id=None,
        )
        sira = self._render(
            build_timeline({"synth_ms": 900}, 1000, "sira"), row_id=None,
        )
        assert 'id="timeline-detail-nora-None"' in nora
        assert 'id="timeline-detail-sira-None"' in sira
        # The ids the two cards would put in one document must differ.
        assert "timeline-detail-nora-None" not in sira
        assert "timeline-detail-sira-None" not in nora


class TestClamping:
    def test_measured_exceeding_wall_clock_clamps_unaccounted_to_zero(self):
        # Stage timers and the route clock are read separately, so a
        # tiny disagreement is possible. It must never render as a
        # negative segment.
        tl = build_timeline({"synthesize": 1200}, 1000, "nora")
        assert tl["unaccounted_ms"] == 0

    def test_negative_stage_value_clamps_to_zero(self):
        tl = build_timeline({"synthesize": -5}, 1000, "nora")
        by_slug = {s["slug"]: s for s in tl["segments"]}
        assert by_slug["synthesize"]["ms"] == 0
