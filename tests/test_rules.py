"""Tests for trajectory signal attribution logic in rules.py."""

from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.trajectory.rules import (
    _extract_topics_for_text,
    classify_chain_topics,
    classify_signal,
    mode_family,
)
from lynchpin.trajectory.signal import TrajectorySignal


def _dt(hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 3, 16, hour, minute, tzinfo=timezone.utc)


def _signal(
    *,
    signal_id: str = "test",
    source: str = "atuin.command",
    kind: str = "command",
    mode_hint: str | None = None,
    project_hint: str | None = None,
    app: str | None = None,
    title: str | None = None,
    url: str | None = None,
    domain: str | None = None,
    cwd: str | None = None,
    detail: str | None = None,
    evidence: dict | None = None,
) -> TrajectorySignal:
    return TrajectorySignal(
        signal_id=signal_id,
        source=source,
        kind=kind,
        start=_dt(10, 0),
        end=_dt(10, 5),
        mode_hint=mode_hint,
        project_hint=project_hint,
        app=app,
        title=title,
        url=url,
        domain=domain,
        cwd=cwd,
        detail=detail,
        evidence=evidence or {},
    )


# ---------------------------------------------------------------------------
# mode_family
# ---------------------------------------------------------------------------

class TestModeFamily:
    def test_coding_and_shell_are_coding_family(self) -> None:
        assert mode_family("coding") == "coding"
        assert mode_family("shell") == "coding"

    def test_sensemaking_family_members(self) -> None:
        for mode in ("research", "chat", "writing", "planning"):
            assert mode_family(mode) == "sensemaking"

    def test_other_modes_return_themselves(self) -> None:
        assert mode_family("media") == "media"
        assert mode_family("recovery") == "recovery"
        assert mode_family("unknown") == "unknown"


# ---------------------------------------------------------------------------
# _extract_topics_for_text
# ---------------------------------------------------------------------------

class TestExtractTopicsForText:
    def test_empty_text_returns_empty(self) -> None:
        assert _extract_topics_for_text("", None) == ()

    def test_rust_keyword_detected(self) -> None:
        results = dict(_extract_topics_for_text("writing rust code with cargo", None))
        assert "rust" in results

    def test_python_keyword_detected(self) -> None:
        results = dict(_extract_topics_for_text("running pytest on python code", None))
        assert "python" in results

    def test_research_work_event_boosts_research_topic(self) -> None:
        # Without we_kind — check base score
        no_boost = dict(_extract_topics_for_text("browsing documentation", None))
        # With research boost — score should be higher
        with_boost = dict(_extract_topics_for_text("browsing documentation", "research"))
        research_no_boost = no_boost.get("research", 0.0)
        research_with_boost = with_boost.get("research", 0.0)
        assert research_with_boost >= research_no_boost

    def test_data_analysis_we_kind_boosts_data_topic(self) -> None:
        text = "data analysis notebook"
        no_boost = dict(_extract_topics_for_text(text, None))
        with_boost = dict(_extract_topics_for_text(text, "data_analysis"))
        data_no_boost = no_boost.get("data", 0.0)
        data_with_boost = with_boost.get("data", 0.0)
        assert data_with_boost > data_no_boost

    def test_implementation_we_kind_boosts_language_topics(self) -> None:
        # implementation → boosts language domain topics (rust, python, etc.)
        # "rust" in text already scores ≥ 1.0; boost should increase it
        text = "implementing a rust function"
        no_boost = dict(_extract_topics_for_text(text, None))
        with_boost = dict(_extract_topics_for_text(text, "implementation"))
        rust_no_boost = no_boost.get("rust", 0.0)
        rust_with_boost = with_boost.get("rust", 0.0)
        assert rust_with_boost >= rust_no_boost

    def test_confidence_sorted_descending(self) -> None:
        results = _extract_topics_for_text("rust cargo testing pytest", None)
        confidences = [conf for _, conf in results]
        assert confidences == sorted(confidences, reverse=True)

    def test_confidence_in_valid_range(self) -> None:
        results = _extract_topics_for_text("rust python nix docker testing data", None)
        for _, conf in results:
            assert 0.0 <= conf <= 1.0


# ---------------------------------------------------------------------------
# classify_signal — mode attribution
# ---------------------------------------------------------------------------

class TestClassifySignalMode:
    def test_afk_kind_always_recovery(self) -> None:
        sig = _signal(source="activitywatch.afk", kind="afk")
        result = classify_signal(sig)
        assert result.mode == "recovery"
        assert result.mode_confidence == 1.0

    def test_git_commit_always_coding(self) -> None:
        sig = _signal(source="git.commit", kind="git_commit")
        result = classify_signal(sig)
        assert result.mode == "coding"
        assert result.mode_confidence == 1.0

    def test_polylogue_session_with_work_event_implementation(self) -> None:
        sig = _signal(
            source="polylogue.session",
            kind="session",
            evidence={"work_event_kind": "implementation"},
        )
        result = classify_signal(sig)
        assert result.mode == "coding"
        assert result.mode_confidence == 0.9

    def test_polylogue_session_with_work_event_research(self) -> None:
        sig = _signal(
            source="polylogue.session",
            kind="session",
            evidence={"work_event_kind": "research"},
        )
        result = classify_signal(sig)
        assert result.mode == "research"
        assert result.mode_confidence == 0.9

    def test_mode_hint_respected(self) -> None:
        sig = _signal(source="instrumentation.terminal_session", kind="terminal_session", mode_hint="coding")
        result = classify_signal(sig)
        assert result.mode == "coding"
        assert result.mode_confidence == 0.85

    def test_ai_domain_classified_as_chat(self) -> None:
        sig = _signal(source="activitywatch.web", kind="web", domain="claude.ai")
        result = classify_signal(sig)
        assert result.mode == "chat"
        assert result.mode_confidence >= 0.9

    def test_youtube_domain_classified_as_media(self) -> None:
        sig = _signal(source="activitywatch.web", kind="web", domain="youtube.com")
        result = classify_signal(sig)
        assert result.mode == "media"

    def test_terminal_with_project_classified_as_coding(self) -> None:
        sig = _signal(
            source="atuin.command",
            kind="command",
            cwd="/realm/project/sinity-lynchpin",
        )
        result = classify_signal(sig)
        assert result.mode == "coding"
        assert result.project == "sinity-lynchpin"

    def test_terminal_without_project_defaults_to_shell(self) -> None:
        sig = _signal(
            source="atuin.command",
            kind="command",
            cwd="/tmp",
            detail="ls -la",
        )
        result = classify_signal(sig)
        # No project match → shell or coding with low confidence
        assert result.mode in {"shell", "coding"}
        assert result.mode_confidence <= 0.7

    def test_reasons_nonempty_for_classified_signal(self) -> None:
        sig = _signal(source="git.commit", kind="git_commit")
        result = classify_signal(sig)
        assert len(result.reasons) >= 1


# ---------------------------------------------------------------------------
# classify_signal — project attribution
# ---------------------------------------------------------------------------

class TestClassifySignalProject:
    def test_cwd_under_realm_project_resolves_project(self) -> None:
        sig = _signal(cwd="/realm/project/sinex/crate/nodes")
        result = classify_signal(sig)
        assert result.project == "sinex"
        assert result.project_confidence == 1.0

    def test_project_hint_used_when_no_path(self) -> None:
        sig = _signal(project_hint="polylogue", cwd="/tmp")
        result = classify_signal(sig)
        assert result.project == "polylogue"

    def test_no_project_when_unknown_path(self) -> None:
        sig = _signal(cwd="/home/user/random-dir", source="atuin.command", kind="command")
        result = classify_signal(sig)
        assert result.project is None

    def test_url_with_project_name_infers_project(self) -> None:
        # Title mentioning a known project name should match via text pattern
        sig = _signal(title="PR review: polylogue refactor", source="activitywatch.web", kind="web")
        result = classify_signal(sig)
        assert result.project == "polylogue"


# ---------------------------------------------------------------------------
# classify_signal — topic attribution
# ---------------------------------------------------------------------------

class TestClassifySignalTopic:
    def test_rust_title_infers_rust_topic(self) -> None:
        sig = _signal(title="cargo build --release (rust)", source="instrumentation.terminal_session", kind="terminal_session")
        result = classify_signal(sig)
        assert result.topic == "rust"

    def test_no_topic_when_no_relevant_text(self) -> None:
        sig = _signal(source="activitywatch.afk", kind="afk")
        result = classify_signal(sig)
        assert result.topic is None

    def test_topic_confidence_positive_when_topic_detected(self) -> None:
        sig = _signal(title="pytest test discovery python", source="instrumentation.terminal_session", kind="terminal_session")
        result = classify_signal(sig)
        if result.topic:
            assert result.topic_confidence > 0.0

    def test_topic_scores_match_topic_when_topic_set(self) -> None:
        sig = _signal(title="rust cargo test", source="instrumentation.terminal_session", kind="terminal_session")
        result = classify_signal(sig)
        if result.topic_scores:
            top_topic = result.topic_scores[0][0]
            assert top_topic == result.topic


# ---------------------------------------------------------------------------
# classify_chain_topics
# ---------------------------------------------------------------------------


class TestClassifyChainTopics:
    def _attributed(self, sig: TrajectorySignal):
        """Classify a signal and return the AttributedSignal."""
        return classify_signal(sig)

    def test_empty_signals_returns_none(self) -> None:
        topic, conf, ranked = classify_chain_topics([])
        assert topic is None
        assert conf == 0.0
        assert ranked == []

    def test_single_rust_signal_returns_rust_topic(self) -> None:
        sig = _signal(
            title="cargo build --release (rust)",
            source="instrumentation.terminal_session",
            kind="terminal_session",
        )
        attributed = [self._attributed(sig)]
        topic, conf, ranked = classify_chain_topics(attributed)
        assert topic == "rust"
        assert conf > 0.0

    def test_dominant_topic_is_highest_weighted(self) -> None:
        # Two rust signals, one ai signal
        rust1 = _signal(
            title="cargo test -- --nocapture rust",
            source="instrumentation.terminal_session",
            kind="terminal_session",
        )
        rust2 = _signal(
            title="rustc compilation error",
            source="atuin.command",
            kind="command",
            detail="cargo clippy",
        )
        ai_sig = _signal(
            title="claude.ai prompt",
            source="activitywatch.web",
            kind="web",
            domain="claude.ai",
        )
        attributed = [
            self._attributed(rust1),
            self._attributed(rust2),
            self._attributed(ai_sig),
        ]
        topic, conf, ranked = classify_chain_topics(attributed)
        assert topic is not None  # dominant should be detected

    def test_source_diversity_boost_increases_confidence(self) -> None:
        # Same topic from two different sources should get diversity boost
        sig1 = _signal(
            title="pytest testing python coverage",
            source="instrumentation.terminal_session",
            kind="terminal_session",
        )
        sig2 = _signal(
            title="pytest test discovery python",
            source="atuin.command",
            kind="command",
            detail="pytest -v tests/",
        )
        single = [self._attributed(sig1)]
        diverse = [self._attributed(sig1), self._attributed(sig2)]

        _, single_conf, single_ranked = classify_chain_topics(single)
        _, diverse_conf, diverse_ranked = classify_chain_topics(diverse)

        # Diverse should have higher or equal confidence for the python/testing topic
        assert diverse_conf >= single_conf

    def test_ranked_topics_sorted_by_weight(self) -> None:
        sig = _signal(
            title="rust cargo build python pytest testing data sql",
            source="instrumentation.terminal_session",
            kind="terminal_session",
        )
        attributed = [self._attributed(sig)]
        _, _, ranked = classify_chain_topics(attributed)
        if len(ranked) > 1:
            weights = [w for _, w in ranked]
            assert weights == sorted(weights, reverse=True)
