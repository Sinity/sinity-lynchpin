"""Tests for pure helper functions in sources/captures/instrumentation.py."""

from __future__ import annotations


from lynchpin.sources.captures.instrumentation import (
    _assess_session_quality,
    _schema_generation,
)


# ---------------------------------------------------------------------------
# _schema_generation
# ---------------------------------------------------------------------------

class TestSchemaGeneration:
    def test_manifest_schema_generation_key(self) -> None:
        manifest = {"schema_generation": "terminal-session-v2"}
        result = _schema_generation(manifest, None)
        assert result == "terminal-session-v2"

    def test_manifest_schema_key_fallback(self) -> None:
        manifest = {"schema": "custom-schema"}
        result = _schema_generation(manifest, None)
        assert result == "custom-schema"

    def test_manifest_fallback_to_default(self) -> None:
        # manifest must be truthy (non-empty) to hit the "terminal-session-v1" default
        manifest = {"other_key": "value"}
        result = _schema_generation(manifest, None)
        assert result == "terminal-session-v1"

    def test_empty_manifest_treated_as_absent(self) -> None:
        # Empty dict is falsy → falls through to cast-header
        result = _schema_generation({}, None)
        assert result == "cast-header"

    def test_no_manifest_uses_header_version(self) -> None:
        header = {"version": 2}
        result = _schema_generation(None, header)
        assert result == "asciicast-v2"

    def test_no_manifest_no_header_version_returns_cast_header(self) -> None:
        result = _schema_generation(None, {})
        assert result == "cast-header"

    def test_none_inputs_returns_cast_header(self) -> None:
        result = _schema_generation(None, None)
        assert result == "cast-header"

    def test_manifest_takes_precedence_over_header(self) -> None:
        manifest = {"schema_generation": "terminal-session-v3"}
        header = {"version": 2}
        result = _schema_generation(manifest, header)
        assert result == "terminal-session-v3"


# ---------------------------------------------------------------------------
# _assess_session_quality
# ---------------------------------------------------------------------------

def _perfect_kwargs(**overrides) -> dict:
    """Return a fully-populated valid quality assessment kwargs dict."""
    base = {
        "manifest_exists": True,
        "has_events": True,
        "schema_generation": "terminal-session-v1",
        "created_at": "2026-03-17T10:00:00+00:00",
        "finished_at": "2026-03-17T11:00:00+00:00",
        "duration_seconds": 3600.0,
        "active_seconds": 1800.0,
        "command": "nvim",
        "timing_source": "events",
    }
    base.update(overrides)
    return base


class TestAssessSessionQuality:
    def test_perfect_session_returns_ok_no_flags(self) -> None:
        status, flags = _assess_session_quality(**_perfect_kwargs())
        assert status == "ok"
        assert flags == []

    def test_missing_manifest_adds_flag(self) -> None:
        _, flags = _assess_session_quality(**_perfect_kwargs(manifest_exists=False))
        assert "missing_manifest" in flags

    def test_missing_events_adds_flag(self) -> None:
        _, flags = _assess_session_quality(**_perfect_kwargs(has_events=False))
        assert "missing_events" in flags

    def test_missing_created_at_causes_degraded(self) -> None:
        status, flags = _assess_session_quality(**_perfect_kwargs(created_at=None))
        assert status == "degraded"
        assert "missing_created_at" in flags

    def test_missing_finished_at_causes_degraded(self) -> None:
        status, flags = _assess_session_quality(**_perfect_kwargs(finished_at=None))
        assert status == "degraded"

    def test_missing_duration_causes_degraded(self) -> None:
        status, flags = _assess_session_quality(**_perfect_kwargs(duration_seconds=None))
        assert status == "degraded"

    def test_missing_active_seconds_causes_degraded(self) -> None:
        status, flags = _assess_session_quality(**_perfect_kwargs(active_seconds=None))
        assert status == "degraded"

    def test_timing_unavailable_causes_degraded(self) -> None:
        status, flags = _assess_session_quality(**_perfect_kwargs(timing_source=None))
        assert status == "degraded"
        assert "timing_unavailable" in flags

    def test_timing_tail_adds_timing_estimated_flag(self) -> None:
        _, flags = _assess_session_quality(**_perfect_kwargs(timing_source="tail"))
        assert "timing_estimated" in flags

    def test_timing_full_fallback_adds_timing_estimated_flag(self) -> None:
        _, flags = _assess_session_quality(**_perfect_kwargs(timing_source="full-fallback"))
        assert "timing_estimated" in flags

    def test_manifest_exists_without_events_is_broken(self) -> None:
        status, flags = _assess_session_quality(**_perfect_kwargs(has_events=False))
        assert status == "damaged"
        assert "broken_new_model" in flags

    def test_header_only_when_no_manifest_no_events_asciicast(self) -> None:
        status, flags = _assess_session_quality(
            **_perfect_kwargs(
                manifest_exists=False,
                has_events=False,
                schema_generation="asciicast-v2",
            )
        )
        assert status == "header-only"
        assert "header_only" in flags

    def test_missing_command_adds_flag_but_not_degraded(self) -> None:
        status, flags = _assess_session_quality(**_perfect_kwargs(command=None))
        assert "missing_command" in flags
        assert status == "ok"  # command missing doesn't trigger degraded

    def test_multiple_flags_accumulated(self) -> None:
        _, flags = _assess_session_quality(
            **_perfect_kwargs(
                manifest_exists=False,
                created_at=None,
                command=None,
            )
        )
        assert len(flags) >= 3
