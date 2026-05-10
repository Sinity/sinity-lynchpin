"""Tests for the Lynchpin work-event re-classifier overlay (Arc K)."""

from __future__ import annotations

from lynchpin.graph.work_event_kind import overlay_label, tier_weight


def test_overlay_agrees_with_polylogue_when_features_align():
    label = overlay_label(
        polylogue_kind="implementation",
        polylogue_confidence=0.9,
        file_paths=("lynchpin/foo.py", "lynchpin/bar.py"),
        tools_used=("Edit", "Write"),
        duration_ms=45 * 60_000,
    )
    assert label.kind == "implementation"
    assert label.source == "agreement"
    assert label.tier == "high"
    assert label.polylogue_kind == "implementation"
    assert label.overlay_kind == "implementation"
    # Combined confidence is bounded at 0.95.
    assert 0.8 <= label.confidence <= 0.95


def test_overlay_disagrees_when_features_point_elsewhere():
    """Polylogue says 'implementation' but file_paths is all tests/ + Bash retry."""
    label = overlay_label(
        polylogue_kind="implementation",
        polylogue_confidence=0.55,
        file_paths=("tests/test_foo.py", "tests/test_bar.py", "tests/conftest.py"),
        tools_used=("Bash", "Bash", "Bash"),
        duration_ms=15 * 60_000,
    )
    # Overlay should win because tests/ + Bash-heavy is a strong testing/debugging signal.
    assert label.source == "disagreement"
    assert label.kind in ("testing", "debugging")
    # Both labels visible for the rendering caveat.
    assert label.polylogue_kind == "implementation"
    assert label.overlay_kind in ("testing", "debugging")


def test_low_tier_when_only_single_feature():
    """Single feature (one weak file path, no tools, mid-length duration that
    doesn't score) → overlay confidence capped at 0.45, falls back to Polylogue."""
    label = overlay_label(
        polylogue_kind="planning",
        polylogue_confidence=0.7,
        file_paths=("docs/notes.md",),
        tools_used=(),
        duration_ms=5 * 60_000,  # 5 min: too short for impl/research, too long for conversation
    )
    # Single feature dimension → overlay caps at 0.45 → Polylogue wins on confidence.
    assert label.kind == "planning"
    assert label.source == "polylogue"
    # Polylogue 0.7 puts us in medium; tier needs feature_count >= 2 OR agreement for high.
    assert label.tier == "medium"


def test_unknown_when_no_signal():
    """All-empty input edge case. Duration=0 triggers conversation as a tiny
    fallback signal (0-length event ≈ message), so we get conversation/low,
    not literal `unknown`. That's the right tier; just check it's degraded."""
    label = overlay_label(
        polylogue_kind=None,
        polylogue_confidence=0.0,
        file_paths=(),
        tools_used=(),
        duration_ms=0,
    )
    assert label.tier == "low"
    assert label.confidence < 0.5


def test_overlay_label_when_polylogue_missing():
    """No Polylogue label, strong overlay features → lynchpin_overlay source."""
    label = overlay_label(
        polylogue_kind=None,
        polylogue_confidence=0.0,
        file_paths=("Cargo.toml", "Cargo.lock"),
        tools_used=("Edit", "Write"),
        duration_ms=10 * 60_000,
    )
    assert label.source == "lynchpin_overlay"
    assert label.kind == "dependency_management"
    assert label.polylogue_kind is None


def test_low_tier_for_zero_duration_zero_features():
    """Empty everything except duration=0 (which scores 'conversation' as a
    last-resort signal). This is the degraded-mode shape — caller must see low
    tier so downstream weighting reflects the lack of corroboration."""
    label = overlay_label(
        polylogue_kind=None,
        polylogue_confidence=0.0,
        file_paths=(),
        tools_used=(),
        duration_ms=0,
    )
    assert label.tier == "low"
    assert label.confidence < 0.5
    assert label.kind == "conversation"  # weakest fallback


def test_tier_weight_arc_k3_propagation():
    assert tier_weight("high") == 1.0
    assert tier_weight("medium") == 0.6
    assert tier_weight("low") == 0.25
