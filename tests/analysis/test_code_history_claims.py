from __future__ import annotations

from datetime import date, datetime, timezone

from lynchpin.analysis.code_history_claims import CodeHistoryInputs, code_history_claims
from lynchpin.sources.git_models import GitCommitFact, GitFileChangeFact


UTC = timezone.utc


def _commit(
    sha: str,
    subject: str,
    *,
    repo: str = "lynchpin",
    files: int = 1,
    churn: int = 10,
    day: int = 1,
) -> GitCommitFact:
    return GitCommitFact(
        repo=repo,
        commit=sha,
        authored_at=datetime(2026, 5, day, 12, tzinfo=UTC),
        author="tester",
        subject=subject,
        lines_added=churn,
        lines_deleted=0,
        lines_changed=churn,
        files_changed=files,
        paths=tuple(f"src/file_{i}.py" for i in range(files)),
        path_roots=("src",),
    )


def _file_change(
    sha: str,
    path: str,
    *,
    repo: str = "lynchpin",
    churn: int = 100,
    day: int = 1,
) -> GitFileChangeFact:
    return GitFileChangeFact(
        repo=repo,
        commit=sha,
        authored_at=datetime(2026, 5, day, 12, tzinfo=UTC),
        path=path,
        path_root=path.split("/")[0],
        lines_added=churn,
        lines_deleted=0,
        lines_changed=churn,
    )


def test_code_history_claims_emit_hotspot_broad_change_and_rework() -> None:
    commits = (
        _commit("a" * 40, "feat: first"),
        _commit("b" * 40, "fix: repair"),
        _commit("c" * 40, "fixup! repair again"),
        _commit("d" * 40, "refactor: sweep", files=50, churn=6000, day=2),
    )
    file_changes = (
        _file_change("a" * 40, "src/a.py", churn=400),
        _file_change("b" * 40, "src/b.py", churn=400),
        _file_change("c" * 40, "src/c.py", churn=400),
        _file_change("d" * 40, "docs/readme.md", churn=20, day=2),
    )

    rows = code_history_claims(
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
        inputs=CodeHistoryInputs(commits=commits, file_changes=file_changes),
        top_n=10,
    )
    by_type = {row.claim_type: row for row in rows}

    assert by_type["code_hotspot"].payload["path_root"] == "src"
    assert by_type["code_broad_change"].source_ids == ("d" * 40,)
    assert by_type["code_rework_pressure"].payload["rework_count"] == 2
    assert by_type["code_rework_pressure"].support_level == "moderate"
