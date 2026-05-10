"""Tests for sources/git.py — commit parsing, daily activity, burst detection."""

import subprocess
from datetime import date, datetime, timedelta

from lynchpin.sources.git import (
    _parse_prefix, _count_bursts, _path_root, _parse_date, _parse_git_shortstat,
    GitCommitFact, commit_facts, github_context_for_commits,
)
from lynchpin.sources.github import GitHubActor, GitHubItem


class TestParsePrefix:
    def test_feat(self):
        assert _parse_prefix("feat: add new thing") == "feat"

    def test_fix_parens(self):
        assert _parse_prefix("fix(core): handle null") == "fix"

    def test_unknown(self):
        assert _parse_prefix("random commit message") == "other"

    def test_refactor(self):
        assert _parse_prefix("refactor: split module") == "refactor"


class TestCountBursts:
    def test_no_burst(self):
        ts = [datetime(2026, 3, 15, 10, i * 10) for i in range(3)]
        assert _count_bursts(ts) == 0  # 10min apart

    def test_burst(self):
        base = datetime(2026, 3, 15, 10, 0, 0)
        ts = [base + timedelta(seconds=i * 30) for i in range(5)]  # 0s, 30s, 60s, 90s, 120s apart
        assert _count_bursts(ts) >= 1

    def test_too_few(self):
        assert _count_bursts([datetime(2026, 3, 15, 10)]) == 0


class TestPathRoot:
    def test_src_module(self):
        assert _path_root("src/networking/mod.rs") == "networking"

    def test_top_level(self):
        assert _path_root("Cargo.toml") == "Cargo.toml"

    def test_tests(self):
        assert _path_root("tests/integration/test_api.rs") == "integration"


class TestParseDate:
    def test_iso_date(self):
        assert _parse_date("2026-03-15") == date(2026, 3, 15)

    def test_iso_datetime(self):
        assert _parse_date("2026-03-15T10:00:00+01:00") == date(2026, 3, 15)

    def test_none(self):
        assert _parse_date(None) is None
        assert _parse_date("") is None


class TestParseShortstat:
    def test_full(self):
        result = _parse_git_shortstat(" 3 files changed, 42 insertions(+), 10 deletions(-)")
        assert result["files_changed"] == 3
        assert result["lines_added"] == 42
        assert result["lines_deleted"] == 10

    def test_insertions_only(self):
        result = _parse_git_shortstat(" 1 file changed, 5 insertions(+)")
        assert result["files_changed"] == 1
        assert result["lines_added"] == 5
        assert result["lines_deleted"] == 0


def test_github_context_for_commits_uses_typed_github_source(monkeypatch):
    fact = GitCommitFact(
        repo="polylogue",
        commit="abc123",
        authored_at=datetime(2026, 5, 6, 1, 0),
        author="Sinity",
        subject="fix(cli): handle dispatch (#846)",
        lines_added=1,
        lines_deleted=0,
        lines_changed=1,
        files_changed=1,
        paths=("polylogue/cli.py",),
        path_roots=("polylogue",),
    )
    item = GitHubItem(
        repo="polylogue",
        slug="Sinity/polylogue",
        kind="pr",
        number=846,
        title="fix(cli): handle dispatch",
        state="merged",
        url="https://github.com/Sinity/polylogue/pull/846",
        author=GitHubActor("Sinity"),
        labels=(),
        body="",
        comments=(),
        created_at=None,
        updated_at=None,
        closed_at=None,
        merged_at=datetime(2026, 5, 6, 2, 0),
        merge_commit="deadbeef",
    )

    monkeypatch.setattr("lynchpin.sources.git.shutil.which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr("lynchpin.sources.git.repo_slug", lambda path: "Sinity/polylogue")
    monkeypatch.setattr("lynchpin.sources.git.fetch_pr", lambda path, number: item)

    result = github_context_for_commits([fact])

    assert result["status"] == "ok"
    assert result["items"][0]["number"] == 846
    assert result["items"][0]["state"] == "merged"


def test_commit_facts_defaults_to_current_history_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: base"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "side"], cwd=repo, check=True, capture_output=True)
    (repo / "side.txt").write_text("side\n", encoding="utf-8")
    subprocess.run(["git", "add", "side.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: side"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "switch", "master"], cwd=repo, check=True, capture_output=True)

    default_rows = tuple(commit_facts(start=date(2026, 1, 1), end=date(2027, 1, 1), repo_paths=(repo,)))
    all_rows = tuple(commit_facts(start=date(2026, 1, 1), end=date(2027, 1, 1), repo_paths=(repo,), all_refs=True))

    assert [row.subject for row in default_rows] == ["feat: base"]
    assert {row.subject for row in all_rows} == {"feat: base", "feat: side"}


def test_commit_facts_can_skip_numstat_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: base"], cwd=repo, check=True, capture_output=True)

    rows = tuple(
        commit_facts(
            start=date(2026, 1, 1),
            end=date(2027, 1, 1),
            repo_paths=(repo,),
            include_paths=False,
        )
    )

    assert len(rows) == 1
    assert rows[0].subject == "feat: base"
    assert rows[0].paths == ()
    assert rows[0].lines_changed == 0
