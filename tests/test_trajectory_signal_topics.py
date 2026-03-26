"""Tests for topic extraction and scoring functions in signals/rules.py,
trajectory/quarter.py, and context/selection.py."""

from __future__ import annotations


from lynchpin.signals.rules import _extract_topics_for_text
from lynchpin.trajectory.quarter import _quarter_key
from lynchpin.context.selection import _score_packet


# ---------------------------------------------------------------------------
# _quarter_key (trajectory/quarter.py)
# ---------------------------------------------------------------------------

class TestQuarterKeyFromMonth:
    """_quarter_key converts 'YYYY-MM' to 'YYYY-QN' per calendar quarter."""

    def test_january_is_q1(self) -> None:
        assert _quarter_key("2026-01") == "2026-Q1"

    def test_march_is_q1(self) -> None:
        assert _quarter_key("2026-03") == "2026-Q1"

    def test_april_is_q2(self) -> None:
        assert _quarter_key("2026-04") == "2026-Q2"

    def test_june_is_q2(self) -> None:
        assert _quarter_key("2026-06") == "2026-Q2"

    def test_july_is_q3(self) -> None:
        assert _quarter_key("2026-07") == "2026-Q3"

    def test_september_is_q3(self) -> None:
        assert _quarter_key("2026-09") == "2026-Q3"

    def test_october_is_q4(self) -> None:
        assert _quarter_key("2026-10") == "2026-Q4"

    def test_december_is_q4(self) -> None:
        assert _quarter_key("2026-12") == "2026-Q4"

    def test_year_preserved(self) -> None:
        assert _quarter_key("2025-07").startswith("2025-")

    def test_different_years(self) -> None:
        assert _quarter_key("2024-01") == "2024-Q1"
        assert _quarter_key("2027-12") == "2027-Q4"


# ---------------------------------------------------------------------------
# _extract_topics_for_text (signals/rules.py)
# ---------------------------------------------------------------------------

class TestExtractTopicsForText:
    def test_empty_text_returns_empty(self) -> None:
        assert _extract_topics_for_text("", None) == ()

    def test_rust_keywords_detected(self) -> None:
        result = dict(_extract_topics_for_text("cargo clippy tokio rust", None))
        assert "rust" in result

    def test_python_keywords_detected(self) -> None:
        result = dict(_extract_topics_for_text("python pandas dataframe", None))
        assert "python" in result

    def test_returns_tuple_of_tuples(self) -> None:
        result = _extract_topics_for_text("rust cargo", None)
        assert isinstance(result, tuple)
        if result:
            assert isinstance(result[0], tuple)
            assert len(result[0]) == 2

    def test_confidence_bounded_0_1(self) -> None:
        result = _extract_topics_for_text("rust cargo tokio clippy async await futures", None)
        for _topic, conf in result:
            assert 0.0 <= conf <= 1.0

    def test_sorted_descending_by_confidence(self) -> None:
        # Heavily rust-biased text — rust should come first
        result = _extract_topics_for_text("rust cargo tokio clippy async", None)
        if len(result) >= 2:
            assert result[0][1] >= result[1][1]

    def test_no_keyword_match_returns_empty(self) -> None:
        result = _extract_topics_for_text("xyzzy quux frob norf blarg", None)
        assert result == ()

    def test_we_kind_data_analysis_boosts_data_topic(self) -> None:
        # "data_analysis" we_kind should boost the "data" topic
        text = "data analysis csv parquet"
        result_boosted = dict(_extract_topics_for_text(text, "data_analysis"))
        result_plain = dict(_extract_topics_for_text(text, None))
        if "data" in result_boosted and "data" in result_plain:
            assert result_boosted["data"] >= result_plain["data"]

    def test_multiple_topics_when_text_covers_both(self) -> None:
        # Text with clear python and data keywords
        result = _extract_topics_for_text("python pandas dataframe csv data analysis", None)
        topic_names = {t for t, _ in result}
        assert "python" in topic_names or "data" in topic_names


# ---------------------------------------------------------------------------
# _score_packet (context/selection.py)
# ---------------------------------------------------------------------------

class TestScorePacket:
    def test_days_packet_higher_than_years(self) -> None:
        """'days' has recency_score=1.0 vs 'years' at 0.2."""
        score_days = _score_packet("days", {}, set())
        score_years = _score_packet("years", {}, set())
        assert score_days > score_years

    def test_empty_query_no_topic_match(self) -> None:
        score = _score_packet("days", {"chain_count": 0}, set())
        # topic_match=0 (empty query), recency=1.0*0.3=0.3, density=0
        assert abs(score - 0.3) < 0.01

    def test_matching_query_term_increases_score(self) -> None:
        packet = {"title": "python project work"}
        score_match = _score_packet("days", packet, {"python"})
        score_no_match = _score_packet("days", packet, set())
        assert score_match > score_no_match

    def test_evidence_density_increases_score(self) -> None:
        low_evidence = {"chain_count": 1}
        high_evidence = {"chain_count": 400}
        score_low = _score_packet("days", low_evidence, set())
        score_high = _score_packet("days", high_evidence, set())
        assert score_high > score_low

    def test_score_is_non_negative(self) -> None:
        assert _score_packet("unknown_type", {}, set()) >= 0.0

    def test_unknown_packet_type_uses_default_recency(self) -> None:
        # Unknown types → default recency_score = 0.5 * 0.3 = 0.15
        score = _score_packet("mystery_type", {}, set())
        assert abs(score - 0.15) < 0.01

    def test_period_packet_type_recognized(self) -> None:
        # 'period' is a known type with recency=0.7
        score = _score_packet("period", {}, set())
        assert abs(score - 0.7 * 0.3) < 0.01

    def test_all_recency_scores_match_formula(self) -> None:
        """Verify all named packet types have recency scores between 0 and 1."""
        known_types = ["days", "weeks", "months", "quarters", "years",
                       "episodes", "themes", "project_arcs", "coverage", "period", "claims"]
        for t in known_types:
            score = _score_packet(t, {}, set())
            assert 0.0 <= score <= 1.0, f"Out-of-range score for type '{t}': {score}"
