"""Tests for _highlights helper in lynchpin/context/signal_rollups.py."""

from __future__ import annotations


from lynchpin.context.signal_rollups import _highlights


def _base(**overrides) -> dict:
    base = {
        "dominant_mode": "coding",
        "dominant_project": "sinex",
        "top_modes": (("coding", 7200.0),),
        "top_projects": (("sinex", 3600.0),),
        "command_count": 10,
        "transcript_count": 2,
        "commit_count": 3,
    }
    base.update(overrides)
    return base


class TestHighlights:
    def test_returns_tuple(self) -> None:
        result = _highlights(**_base())
        assert isinstance(result, list)

    def test_mode_included(self) -> None:
        result = _highlights(**_base())
        assert any("mode:" in s for s in result)

    def test_project_included(self) -> None:
        result = _highlights(**_base())
        assert any("project:" in s for s in result)

    def test_commands_included(self) -> None:
        result = _highlights(**_base(command_count=42))
        assert any("commands:42" in s for s in result)

    def test_commits_included(self) -> None:
        result = _highlights(**_base(commit_count=7))
        assert any("commits:7" in s for s in result)

    def test_transcripts_included(self) -> None:
        result = _highlights(**_base(transcript_count=5))
        assert any("transcripts:5" in s for s in result)

    def test_no_mode_when_dominant_mode_none(self) -> None:
        result = _highlights(**_base(dominant_mode=None, top_modes=()))
        assert not any("mode:" in s for s in result)

    def test_no_project_when_dominant_project_none(self) -> None:
        result = _highlights(**_base(dominant_project=None, top_projects=()))
        assert not any("project:" in s for s in result)

    def test_no_commands_when_zero(self) -> None:
        result = _highlights(**_base(command_count=0))
        assert not any("commands:" in s for s in result)

    def test_no_commits_when_zero(self) -> None:
        result = _highlights(**_base(commit_count=0))
        assert not any("commits:" in s for s in result)

    def test_no_transcripts_when_zero(self) -> None:
        result = _highlights(**_base(transcript_count=0))
        assert not any("transcripts:" in s for s in result)

    def test_empty_when_all_zero_and_none(self) -> None:
        result = _highlights(
            dominant_mode=None,
            dominant_project=None,
            top_modes=(),
            top_projects=(),
            command_count=0,
            transcript_count=0,
            commit_count=0,
        )
        assert result == []

    def test_max_5_highlights(self) -> None:
        result = _highlights(
            dominant_mode="coding",
            dominant_project="sinex",
            top_modes=(("coding", 7200.0),),
            top_projects=(("sinex", 3600.0),),
            command_count=100,
            transcript_count=10,
            commit_count=5,
        )
        assert len(result) <= 5

    def test_hours_formatted_in_mode_entry(self) -> None:
        result = _highlights(**_base(top_modes=(("coding", 3600.0),)))
        mode_entry = next(s for s in result if s.startswith("mode:"))
        assert "1.0h" in mode_entry
