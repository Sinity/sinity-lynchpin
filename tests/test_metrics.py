"""Tests for lynchpin.metrics: git, health, and productivity metric functions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from lynchpin.metrics.git import GitMetrics, git_summary
from lynchpin.metrics.health import SleepMetrics, sleep_summary
from lynchpin.metrics.productivity import (
    categorise_command,
    chat_token_density,
    command_density,
    commands_by_category,
)


# ---------------------------------------------------------------------------
# git_summary / GitMetrics
# ---------------------------------------------------------------------------

def _commit(repo: str = "sinex", lines_added: int = 10, lines_deleted: int = 5):
    return SimpleNamespace(repo=repo, lines_added=lines_added, lines_deleted=lines_deleted)


class TestGitSummary:
    def test_empty_commits_returns_zero_metrics(self) -> None:
        result = git_summary([])
        assert result.commits == 0
        assert result.lines_added == 0
        assert result.lines_deleted == 0
        assert result.net_loc == 0
        assert result.repos == {}

    def test_commit_count_matches_input(self) -> None:
        commits = [_commit() for _ in range(5)]
        result = git_summary(commits)
        assert result.commits == 5

    def test_lines_added_summed(self) -> None:
        commits = [_commit(lines_added=100), _commit(lines_added=50)]
        result = git_summary(commits)
        assert result.lines_added == 150

    def test_lines_deleted_summed(self) -> None:
        commits = [_commit(lines_deleted=30), _commit(lines_deleted=20)]
        result = git_summary(commits)
        assert result.lines_deleted == 50

    def test_net_loc_is_added_minus_deleted(self) -> None:
        commits = [_commit(lines_added=100, lines_deleted=40)]
        result = git_summary(commits)
        assert result.net_loc == 60

    def test_repos_dict_counts_by_repo(self) -> None:
        commits = [
            _commit(repo="sinex"),
            _commit(repo="sinex"),
            _commit(repo="polylogue"),
        ]
        result = git_summary(commits)
        assert result.repos["sinex"] == 2
        assert result.repos["polylogue"] == 1

    def test_none_lines_treated_as_zero(self) -> None:
        commit = SimpleNamespace(repo="sinex", lines_added=None, lines_deleted=None)
        result = git_summary([commit])
        assert result.lines_added == 0
        assert result.lines_deleted == 0

    def test_churn_property_is_sum_of_add_and_delete(self) -> None:
        m = GitMetrics(commits=1, lines_added=80, lines_deleted=20, net_loc=60, repos={})
        assert m.churn == 100

    def test_empty_repo_name_excluded(self) -> None:
        commits = [SimpleNamespace(repo="", lines_added=0, lines_deleted=0)]
        result = git_summary(commits)
        assert "" not in result.repos


# ---------------------------------------------------------------------------
# sleep_summary / SleepMetrics
# ---------------------------------------------------------------------------

def _sleep_entry(total_minutes: float = 480.0, segments: list = None, avg_score: Optional[float] = 75.0):
    return SimpleNamespace(
        total_minutes=total_minutes,
        segments=segments if segments is not None else [1, 2, 3],
        avg_score=avg_score,
    )


class TestSleepSummary:
    def test_none_entry_returns_none(self) -> None:
        assert sleep_summary(None) is None

    def test_total_hours_converted_from_minutes(self) -> None:
        result = sleep_summary(_sleep_entry(total_minutes=480.0))
        assert result is not None
        assert result.total_hours == pytest.approx(8.0)

    def test_segment_count_from_list_length(self) -> None:
        result = sleep_summary(_sleep_entry(segments=[1, 2, 3, 4]))
        assert result is not None
        assert result.segments == 4

    def test_avg_score_preserved(self) -> None:
        result = sleep_summary(_sleep_entry(avg_score=85.0))
        assert result is not None
        assert result.avg_score == pytest.approx(85.0)

    def test_avg_score_none_preserved(self) -> None:
        result = sleep_summary(_sleep_entry(avg_score=None))
        assert result is not None
        assert result.avg_score is None

    def test_partial_minutes_rounded_to_2dp(self) -> None:
        result = sleep_summary(_sleep_entry(total_minutes=481.0))
        assert result is not None
        assert result.total_hours == pytest.approx(8.02, abs=0.01)


class TestSleepMetricsQualityLabel:
    def test_good_above_80(self) -> None:
        m = SleepMetrics(total_hours=8.0, segments=3, avg_score=85.0)
        assert m.quality_label == "good"

    def test_good_exactly_80(self) -> None:
        m = SleepMetrics(total_hours=8.0, segments=3, avg_score=80.0)
        assert m.quality_label == "good"

    def test_fair_between_60_and_80(self) -> None:
        m = SleepMetrics(total_hours=7.0, segments=3, avg_score=70.0)
        assert m.quality_label == "fair"

    def test_fair_exactly_60(self) -> None:
        m = SleepMetrics(total_hours=7.0, segments=3, avg_score=60.0)
        assert m.quality_label == "fair"

    def test_poor_below_60(self) -> None:
        m = SleepMetrics(total_hours=5.0, segments=2, avg_score=45.0)
        assert m.quality_label == "poor"

    def test_unknown_when_score_is_none(self) -> None:
        m = SleepMetrics(total_hours=8.0, segments=3, avg_score=None)
        assert m.quality_label == "unknown"


# ---------------------------------------------------------------------------
# categorise_command
# ---------------------------------------------------------------------------

class TestCategoriseCommand:
    def test_sinex_project_path(self) -> None:
        assert categorise_command("/realm/project/sinex", "cargo build") == "development:sinex"

    def test_sinex_nested_path(self) -> None:
        assert categorise_command("/realm/project/sinex/crate/nodes", "cargo test") == "development:sinex"

    def test_sinnix_path(self) -> None:
        assert categorise_command("/realm/project/sinnix/modules", "nixos-rebuild") == "infrastructure:sinnix"

    def test_other_realm_project(self) -> None:
        assert categorise_command("/realm/project/polylogue", "python main.py") == "development:other"

    def test_home_directory(self) -> None:
        assert categorise_command("/home/sinity", "ls") == "home"

    def test_realm_home(self) -> None:
        assert categorise_command("/realm/home/sinity/.config", "vim") == "home"

    def test_misc_path(self) -> None:
        assert categorise_command("/tmp", "ls") == "misc"

    def test_none_cwd_returns_misc(self) -> None:
        assert categorise_command(None, "ls") == "misc"

    def test_empty_string_cwd_returns_misc(self) -> None:
        assert categorise_command("", "git status") == "misc"

    def test_case_insensitive_path_matching(self) -> None:
        # sinnix matching is lowercased
        assert categorise_command("/realm/project/SINNIX", "echo") == "infrastructure:sinnix"


# ---------------------------------------------------------------------------
# commands_by_category
# ---------------------------------------------------------------------------

class TestCommandsByCategory:
    def _cmd(self, cwd: str, command: str = "ls"):
        return SimpleNamespace(cwd=cwd, command=command)

    def test_empty_input_returns_empty_dict(self) -> None:
        assert commands_by_category([]) == {}

    def test_groups_by_category(self) -> None:
        cmds = [
            self._cmd("/realm/project/sinex"),
            self._cmd("/realm/project/sinex"),
            self._cmd("/realm/project/polylogue"),
        ]
        result = commands_by_category(cmds)
        assert result["development:sinex"] == 2
        assert result["development:other"] == 1

    def test_result_is_sorted_alphabetically(self) -> None:
        cmds = [
            self._cmd("/tmp"),
            self._cmd("/realm/project/sinex"),
            self._cmd("/home/sinity"),
        ]
        result = commands_by_category(cmds)
        keys = list(result.keys())
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# command_density / chat_token_density
# ---------------------------------------------------------------------------

class TestCommandDensity:
    def test_zero_active_hours_returns_zero(self) -> None:
        assert command_density([1, 2, 3], 0.0) == 0.0

    def test_negative_active_hours_returns_zero(self) -> None:
        assert command_density([1, 2], -1.0) == 0.0

    def test_density_is_count_over_hours(self) -> None:
        result = command_density(list(range(100)), 10.0)
        assert result == pytest.approx(10.0)

    def test_empty_commands_with_nonzero_hours(self) -> None:
        assert command_density([], 5.0) == pytest.approx(0.0)


class TestChatTokenDensity:
    def _transcript(self, tokens: int):
        return SimpleNamespace(tokens=tokens)

    def test_zero_active_hours_returns_zero(self) -> None:
        assert chat_token_density([self._transcript(1000)], 0.0) == 0.0

    def test_tokens_summed_and_divided(self) -> None:
        transcripts = [self._transcript(500), self._transcript(300)]
        result = chat_token_density(transcripts, 2.0)
        assert result == pytest.approx(400.0)

    def test_none_tokens_treated_as_zero(self) -> None:
        t = SimpleNamespace(tokens=None)
        result = chat_token_density([t], 1.0)
        assert result == pytest.approx(0.0)

    def test_empty_transcripts_returns_zero(self) -> None:
        assert chat_token_density([], 8.0) == pytest.approx(0.0)
