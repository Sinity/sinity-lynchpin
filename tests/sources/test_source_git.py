"""Tests for sources/git.py — commit parsing, daily activity, burst detection."""

import os
import subprocess
from datetime import date, datetime, timedelta

from lynchpin.sources.git import (
    _iter_repo_commit_records,
    _parse_prefix,
    _count_bursts,
    _path_root,
    _parse_date,
    _parse_git_shortstat,
    GitCommitFact,
    commit_facts,
    github_context_for_commits,
)
from lynchpin.core.primitives import logical_date
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
        ts = [
            base + timedelta(seconds=i * 30) for i in range(5)
        ]  # 0s, 30s, 60s, 90s, 120s apart
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
        result = _parse_git_shortstat(
            " 3 files changed, 42 insertions(+), 10 deletions(-)"
        )
        assert result["files_changed"] == 3
        assert result["lines_added"] == 42
        assert result["lines_deleted"] == 10

    def test_insertions_only(self):
        result = _parse_git_shortstat(" 1 file changed, 5 insertions(+)")
        assert result["files_changed"] == 1
        assert result["lines_added"] == 5
        assert result["lines_deleted"] == 0


def test_github_context_for_commits_reads_materialized_context(monkeypatch):
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

    calls = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window))
        or type("Result", (), {"status": "ready", "reason": "ready"})(),
    )
    monkeypatch.setattr(
        "lynchpin.sources.github_context.iter_github_context",
        lambda *, projects=None, **_kwargs: iter(
            (type("Row", (), {"project": "polylogue", "item": item})(),)
        ),
    )

    result = github_context_for_commits([fact])

    assert calls == [("github_context", (date(2026, 5, 5), date(2026, 5, 6)))]
    assert result["status"] == "ok"
    assert result["materialization_status"] == "ready"
    assert result["items"][0]["number"] == 846
    assert result["items"][0]["state"] == "merged"


def test_github_context_for_commits_reports_missing_product(monkeypatch):
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
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: type(
            "Result", (), {"status": "failed", "reason": "network_down"}
        )(),
    )
    monkeypatch.setattr(
        "lynchpin.sources.github_context.iter_github_context",
        lambda *, projects=None, **_kwargs: (_ for _ in ()).throw(
            FileNotFoundError("missing context")
        ),
    )

    result = github_context_for_commits([fact])

    assert result["status"] == "unavailable"
    assert result["materialization_status"] == "missing"
    assert result["items"][0]["status"] == "unavailable"


def test_commit_facts_defaults_to_current_history_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: base"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "switch", "-c", "side"], cwd=repo, check=True, capture_output=True
    )
    (repo / "side.txt").write_text("side\n", encoding="utf-8")
    subprocess.run(["git", "add", "side.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: side"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "switch", "master"], cwd=repo, check=True, capture_output=True
    )

    default_rows = tuple(
        commit_facts(start=date(2026, 1, 1), end=date(2027, 1, 1), repo_paths=(repo,))
    )
    all_rows = tuple(
        commit_facts(
            start=date(2026, 1, 1),
            end=date(2027, 1, 1),
            repo_paths=(repo,),
            all_refs=True,
        )
    )

    assert [row.subject for row in default_rows] == ["feat: base"]
    assert {row.subject for row in all_rows} == {"feat: base", "feat: side"}


def _init_repo(repo):
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)


def _commit_at(repo, name, msg, author_date):
    (repo / name).write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=repo, check=True)
    env = {
        "GIT_AUTHOR_DATE": author_date,
        "GIT_COMMITTER_DATE": author_date,
    }
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **env},
    )


def test_commits_bucket_by_logical_day_not_author_tz(tmp_path):
    """A commit at 23:30-08:00 is the next local logical day, not the author-tz date.

    Author-tz ``.date()`` would file 2026-03-15T23:30-08:00 on Mar 15; the local
    logical day (Europe/Warsaw, after the 6 AM boundary) is Mar 16. This pins the
    fix that aligns git day attribution with AW/terminal logical days.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    author_date = "2026-03-15T23:30:00-08:00"
    _commit_at(repo, "a.txt", "feat: edge commit", author_date)

    aware = datetime.fromisoformat(author_date)
    expected_day = logical_date(aware)
    assert expected_day != aware.date()  # guards against author-tz regression

    # commit_facts is the per-repo entry point; the range filter inside
    # _iter_repo_commit_records now buckets by logical_date.
    facts = list(commit_facts(start=expected_day, end=expected_day, repo_paths=(repo,)))
    assert [f.subject for f in facts] == ["feat: edge commit"]
    assert logical_date(facts[0].authored_at) == expected_day


def test_commit_facts_can_skip_numstat_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: base"], cwd=repo, check=True, capture_output=True
    )

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


def test_iter_repo_commit_records_closes_git_process_when_consumer_stops(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    class FakeStdout:
        def __init__(self):
            self.closed = False
            self._lines = iter(
                [
                    "COMMIT|a1|2026-01-02T00:00:00+00:00|Tester|feat: first\n",
                    "COMMIT|b2|2026-01-03T00:00:00+00:00|Tester|feat: second\n",
                ]
            )

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._lines)

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()
            self.terminated = False
            self.killed = False
            self.waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waited = True
            return 0

    fake = FakeProcess()
    monkeypatch.setattr(
        "lynchpin.sources.git._default_history_ref", lambda _repo: "master"
    )
    monkeypatch.setattr(
        "lynchpin.sources.git.subprocess.Popen", lambda *_args, **_kwargs: fake
    )

    records = _iter_repo_commit_records(
        repo, start=date(2026, 1, 1), end=date(2026, 1, 4), include_paths=False
    )
    first = next(records)
    records.close()

    assert first.subject == "feat: first"
    assert fake.stdout.closed is True
    assert fake.terminated is True
    assert fake.waited is True
    assert fake.killed is False
