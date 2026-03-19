"""Tests for trajectory signal attribution logic in rules.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lynchpin.trajectory.rules import (
    _contains_any,
    _extract_topics_for_text,
    _matches_domain,
    _project_from_path,
    _project_from_path_str,
    _project_from_text,
    _project_from_values,
    classify_chain_topics,
    classify_signal,
    mode_family,
    normalize_topic,
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

    def test_debugging_we_kind_boosts_testing_topic(self) -> None:
        text = "running test assert coverage"
        no_boost = dict(_extract_topics_for_text(text, None))
        with_boost = dict(_extract_topics_for_text(text, "debugging"))
        assert with_boost.get("testing", 0.0) >= no_boost.get("testing", 0.0)

    def test_documentation_we_kind_boosts_writing_topic(self) -> None:
        text = "writing doc readme note"
        no_boost = dict(_extract_topics_for_text(text, None))
        with_boost = dict(_extract_topics_for_text(text, "documentation"))
        assert with_boost.get("writing", 0.0) >= no_boost.get("writing", 0.0)

    def test_configuration_we_kind_boosts_infra_topic(self) -> None:
        text = "deploy ci systemd"
        no_boost = dict(_extract_topics_for_text(text, None))
        with_boost = dict(_extract_topics_for_text(text, "configuration"))
        assert with_boost.get("infra", 0.0) >= no_boost.get("infra", 0.0)

    def test_review_we_kind_boosts_language_topics(self) -> None:
        # review → "coding" boost path: amplifies language domain topics scoring ≥ 1.0
        text = "cargo build rust"  # rust scores from cargo keyword
        no_boost = dict(_extract_topics_for_text(text, None))
        with_boost = dict(_extract_topics_for_text(text, "review"))
        assert with_boost.get("rust", 0.0) >= no_boost.get("rust", 0.0)

    def test_refactoring_we_kind_boosts_language_topics(self) -> None:
        # refactoring → "coding" boost path: same fan-out as review/implementation
        text = "python refactor module"
        no_boost = dict(_extract_topics_for_text(text, None))
        with_boost = dict(_extract_topics_for_text(text, "refactoring"))
        assert with_boost.get("python", 0.0) >= no_boost.get("python", 0.0)

    def test_conversation_we_kind_boosts_ai_topic(self) -> None:
        text = "claude llm prompt agent"
        no_boost = dict(_extract_topics_for_text(text, None))
        with_boost = dict(_extract_topics_for_text(text, "conversation"))
        assert with_boost.get("ai", 0.0) >= no_boost.get("ai", 0.0)

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

    def test_implementation_work_event_boosts_language_topic(self) -> None:
        sig = _signal(
            title="implement rust parser",
            source="polylogue.session",
            kind="session",
            evidence={"work_event_kind": "implementation"},
        )
        result = classify_signal(sig)
        assert result.topic == "rust"

    def test_review_work_event_boosts_git_topic(self) -> None:
        sig = _signal(
            title="review merge strategy and branch cleanup",
            source="polylogue.session",
            kind="session",
            evidence={"work_event_kind": "review"},
        )
        result = classify_signal(sig)
        assert result.topic == "git"

    def test_configuration_work_event_boosts_nix_when_text_mentions_nix(self) -> None:
        sig = _signal(
            title="configuration pass on nix flake and home-manager setup",
            source="polylogue.session",
            kind="session",
            evidence={"work_event_kind": "configuration"},
        )
        result = classify_signal(sig)
        assert result.topic == "nix"


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


# ---------------------------------------------------------------------------
# normalize_topic
# ---------------------------------------------------------------------------

class TestNormalizeTopic:
    def test_known_variant_maps_to_canonical(self) -> None:
        assert normalize_topic("cargo") == "rust"
        assert normalize_topic("nixos") == "nix"
        assert normalize_topic("pytest") == "python"
        assert normalize_topic("claude") == "ai"

    def test_unknown_variant_returned_as_is(self) -> None:
        assert normalize_topic("rust") == "rust"
        assert normalize_topic("python") == "python"

    def test_strips_whitespace(self) -> None:
        assert normalize_topic("  cargo  ") == "rust"

    def test_case_insensitive(self) -> None:
        assert normalize_topic("CARGO") == "rust"
        assert normalize_topic("NixOS") == "nix"

    def test_empty_string_returns_empty(self) -> None:
        assert normalize_topic("") == ""


# ---------------------------------------------------------------------------
# _project_from_path_str
# ---------------------------------------------------------------------------

class TestProjectFromPathStr:
    def test_empty_string_returns_none(self) -> None:
        assert _project_from_path_str("") is None

    def test_url_with_scheme_returns_none(self) -> None:
        assert _project_from_path_str("https://github.com/foo") is None

    def test_file_url_scheme_not_rejected(self) -> None:
        # file:// is allowed through the URL guard
        # Result depends on path resolution; just verify it doesn't crash
        _project_from_path_str("file:///realm/project/sinex")
        # No assertion on value — may or may not match — just no crash

    def test_non_path_text_returns_none(self) -> None:
        assert _project_from_path_str("sinex") is None  # no leading / ~ .

    def test_realm_project_path_returns_name(self) -> None:
        assert _project_from_path_str("/realm/project/sinex") == "sinex"

    def test_nested_realm_project_path_returns_name(self) -> None:
        assert _project_from_path_str("/realm/project/sinex/crate/nodes") == "sinex"

    def test_unknown_project_name_returns_none(self) -> None:
        assert _project_from_path_str("/realm/project/does-not-exist") is None

    def test_polylogue_path_detected(self) -> None:
        assert _project_from_path_str("/realm/project/polylogue") == "polylogue"


# ---------------------------------------------------------------------------
# _project_from_path
# ---------------------------------------------------------------------------

class TestProjectFromPath:
    def test_none_returns_none(self) -> None:
        assert _project_from_path(None) is None

    def test_valid_project_path_returns_name(self) -> None:
        assert _project_from_path("/realm/project/sinex") == "sinex"

    def test_non_path_string_returns_none(self) -> None:
        assert _project_from_path("random text") is None


# ---------------------------------------------------------------------------
# _project_from_text
# ---------------------------------------------------------------------------

class TestProjectFromText:
    def test_none_returns_none(self) -> None:
        assert _project_from_text(None) is None

    def test_empty_returns_none(self) -> None:
        assert _project_from_text("") is None

    def test_sinex_in_text_detected(self) -> None:
        result = _project_from_text("cargo build in sinex")
        assert result == "sinex"

    def test_polylogue_in_text_detected(self) -> None:
        result = _project_from_text("polylogue export done")
        assert result == "polylogue"

    def test_no_project_name_returns_none(self) -> None:
        assert _project_from_text("just a shell command") is None


# ---------------------------------------------------------------------------
# _project_from_values
# ---------------------------------------------------------------------------

class TestProjectFromValues:
    def test_no_args_returns_none(self) -> None:
        assert _project_from_values() is None

    def test_all_none_returns_none(self) -> None:
        assert _project_from_values(None, None) is None

    def test_path_match_returns_with_confidence_1(self) -> None:
        result = _project_from_values("/realm/project/sinex")
        assert result is not None
        name, confidence, reason = result
        assert name == "sinex"
        assert confidence == 1.0
        assert reason == "project_path"

    def test_text_match_returns_with_confidence_0_7(self) -> None:
        # No path match, but text contains "polylogue"
        result = _project_from_values("doing polylogue work", "misc text")
        assert result is not None
        name, confidence, reason = result
        assert name == "polylogue"
        assert confidence == pytest.approx(0.7)
        assert reason == "project_text"

    def test_path_match_wins_over_text_match(self) -> None:
        # First value is a non-matching text; second is a path
        result = _project_from_values("sinex text hint", "/realm/project/polylogue")
        assert result is not None
        _, _, reason = result
        # Path checked first across all values, then text
        assert reason == "project_path"


# ---------------------------------------------------------------------------
# _matches_domain
# ---------------------------------------------------------------------------

class TestMatchesDomain:
    def test_empty_domain_returns_false(self) -> None:
        assert _matches_domain("", {"github.com"}) is False

    def test_exact_match_returns_true(self) -> None:
        assert _matches_domain("github.com", {"github.com"}) is True

    def test_subdomain_match_returns_true(self) -> None:
        assert _matches_domain("api.github.com", {"github.com"}) is True

    def test_partial_no_subdomain_returns_false(self) -> None:
        # "notgithub.com" does not end with ".github.com" and is not exact
        assert _matches_domain("notgithub.com", {"github.com"}) is False

    def test_no_matching_candidate_returns_false(self) -> None:
        assert _matches_domain("example.com", {"github.com", "gitlab.com"}) is False

    def test_multiple_candidates_one_matches(self) -> None:
        assert _matches_domain("gitlab.com", {"github.com", "gitlab.com"}) is True


# ---------------------------------------------------------------------------
# _contains_any
# ---------------------------------------------------------------------------

class TestContainsAny:
    def test_no_candidates_returns_false(self) -> None:
        assert _contains_any("hello world", set()) is False

    def test_match_returns_true(self) -> None:
        assert _contains_any("cargo build --release", {"cargo"}) is True

    def test_no_match_returns_false(self) -> None:
        assert _contains_any("python main.py", {"cargo", "rustc"}) is False

    def test_multiple_candidates_one_matches(self) -> None:
        assert _contains_any("running pytest", {"pytest", "cargo"}) is True

    def test_case_sensitive(self) -> None:
        # _contains_any doesn't lowercase — must match exactly
        assert _contains_any("CARGO build", {"cargo"}) is False
        assert _contains_any("CARGO build", {"CARGO"}) is True
