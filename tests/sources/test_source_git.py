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
import pytest

from lynchpin.core.primitives import logical_date
from lynchpin.sources import git as git_source
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
    repo.mkdir()

    class FakeStdout:
        def __init__(self):
            self.closed = False
            self._chunks = iter(
                [
                    b"COMMIT\x1fa1\x1f2026-01-02T00:00:00+00:00\x1fTester\x1ffeat: first\0\0",
                    b"COMMIT\x1fb2\x1f2026-01-03T00:00:00+00:00\x1fTester\x1ffeat: second\0\0",
                ]
            )

        def read(self, _size=-1):
            return next(self._chunks, b"")

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
    monkeypatch.setattr("lynchpin.sources.git._is_git_repo_root", lambda _repo: True)
    monkeypatch.setattr("lynchpin.sources.git._repo_identity", lambda _repo: "repo")
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


# ── Source-convergence audit reproductions (L52–L55), asserting correct outcomes ──


def _git(repo, *args, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )


def test_linked_worktree_reads_the_same_history_as_its_primary(tmp_path):
    # Anti-vacuity: testing `.git`'s file type rejects a linked worktree
    # (whose `.git` is a file) and yields zero rows.
    primary = tmp_path / "primary"
    _init_repo(primary)
    _commit_at(primary, "a.txt", "feat: base", "2026-01-02T12:00:00+00:00")
    linked = tmp_path / "linked"
    _git(primary, "worktree", "add", "-q", str(linked))
    assert (linked / ".git").is_file()
    window = dict(start=date(2026, 1, 1), end=date(2026, 1, 5))
    primary_rows = [r.commit for r in git_source._iter_repo_commit_records(primary, **window)]
    linked_rows = [r.commit for r in git_source._iter_repo_commit_records(linked, **window)]
    assert primary_rows
    assert linked_rows == primary_rows
    assert [r.repo for r in git_source._iter_repo_commit_records(linked, **window)] == ["primary"]


def test_dangling_declared_default_ref_is_a_typed_failure(tmp_path):
    # Anti-vacuity: returning an empty history when origin/HEAD points at a
    # ref that does not resolve makes a broken repository look inactive.
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_at(repo, "a.txt", "feat: base", "2026-01-02T12:00:00+00:00")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/vanished")
    with pytest.raises(git_source.GitSourceError, match="does not resolve"):
        list(
            git_source._iter_repo_commit_records(
                repo, start=date(2026, 1, 1), end=date(2026, 1, 5)
            )
        )


def test_failed_git_log_is_a_typed_failure_not_empty_history(tmp_path, monkeypatch):
    # Anti-vacuity: discarding git's exit status turns a failed read into
    # "no commits".
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_at(repo, "a.txt", "feat: base", "2026-01-02T12:00:00+00:00")
    monkeypatch.setattr(git_source, "_default_history_ref", lambda _repo: "no-such-ref")
    with pytest.raises(git_source.GitSourceError, match="git log exited"):
        list(
            git_source._iter_repo_commit_records(
                repo, start=date(2026, 1, 1), end=date(2026, 1, 5)
            )
        )


def test_commit_authored_in_window_but_committed_later_is_returned(tmp_path):
    # Anti-vacuity: a committer-time upper bound drops commits whose author
    # time is in the window and whose committer time is after it.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "feat: rebased later",
        env={
            "GIT_AUTHOR_DATE": "2026-01-02T12:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-03-02T12:00:00+00:00",
        },
    )
    day = logical_date(datetime.fromisoformat("2026-01-02T12:00:00+00:00"))
    rows = list(git_source._iter_repo_commit_records(repo, start=day, end=day))
    assert [r.subject for r in rows] == ["feat: rebased later"]


def test_numstat_paths_are_exact_including_unicode_spaces_and_renames(tmp_path):
    # Anti-vacuity: display-formatted numstat quotes and octal-escapes
    # non-ASCII names, strips leading spaces, and renders renames as
    # `{a => b}`; none of those is the file's path.
    repo = tmp_path / "repo"
    _init_repo(repo)
    stamp = "2026-01-02T12:00:00+00:00"
    dates = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    (repo / "żółć.txt").write_text("a\n", encoding="utf-8")
    (repo / " note.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: add", env=dates)
    (repo / "dir name").mkdir()
    _git(repo, "mv", " note.txt", "dir name/renamed.txt")
    _git(repo, "commit", "-q", "-m", "feat: move", env=dates)
    day = logical_date(datetime.fromisoformat(stamp))
    rows = {
        r.subject: r
        for r in git_source._iter_repo_commit_records(repo, start=day, end=day)
    }
    assert {p for p, _, _, _ in rows["feat: add"].path_changes} == {"żółć.txt", " note.txt"}
    assert rows["feat: move"].path_changes == (("dir name/renamed.txt", 0, 0, " note.txt"),)


def test_only_explicit_ai_coauthor_trailers_count_as_ai():
    # Anti-vacuity: treating every Co-authored-by trailer as AI labels a
    # human collaborator as AI contribution.
    human = "Co-authored-by: Audit Collaborator <person@example.com>"
    assert git_source._extract_coauthor(human) is None
    agent = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    assert git_source._extract_coauthor(agent) == "Claude Opus 5.5"
    by_address = "Co-authored-by: Helper <noreply@anthropic.com>"
    assert git_source._extract_coauthor(by_address) == "Helper"


def test_human_coauthor_is_unmarked_not_human_only(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    stamp = "2026-01-02T12:00:00+00:00"
    _commit_at(repo, "a.txt", "feat: base\n\nCo-authored-by: Audit Collaborator <person@example.com>", stamp)
    monkeypatch.setattr(git_source, "active_repo_paths", lambda names=None: [repo])
    monkeypatch.setattr(git_source, "_repo_path", lambda _repo: repo)
    day = logical_date(datetime.fromisoformat(stamp))
    rows = git_source.daily_activity(start=day, end=day)
    assert len(rows) == 1
    assert rows[0].ai_coauthored == 0
    assert rows[0].ai_ratio == 0
    assert rows[0].unmarked == 1
    assert not hasattr(rows[0], "human_only")
