from __future__ import annotations

from pathlib import Path

from lynchpin.analysis.change.work_packages import build_active_work_packages


def _commit(
    *,
    project: str = "demo",
    sha: str,
    day: str,
    subject: str,
    signature: str,
    scope: str | None,
    paths: list[str],
    categories: dict[str, int],
    prs: list[int] | None = None,
    issues: list[int] | None = None,
) -> dict[str, object]:
    return {
        "project": project,
        "sha": sha,
        "short_sha": sha[:7],
        "timestamp": f"{day}T10:00:00+00:00",
        "date": day,
        "subject": subject,
        "author": "Tester",
        "conventional_kind": signature.split("(", 1)[0],
        "conventional_scope": scope,
        "conventional_signature": signature,
        "paths": paths,
        "categories": categories,
        "path_roots": {path.split("/", 1)[0]: 1 for path in paths},
        "github_refs": {"prs": prs or [], "issues": issues or []},
    }


def test_active_work_packages_group_refs_before_conventional_scope(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("", encoding="utf-8")
    payload = {
        "window": {"start": "2026-05-01", "end": "2026-05-05"},
        "projects": [{"project": "demo", "path": str(tmp_path), "default_branch": "master", "status": "available"}],
        "commits": [
            _commit(
                sha="a" * 40,
                day="2026-05-01",
                subject="feat(core): land package (#7)",
                signature="feat(core)",
                scope="core",
                paths=["src/a.py"],
                categories={"src": 1},
                prs=[7],
            ),
            _commit(
                sha="b" * 40,
                day="2026-05-01",
                subject="feat(core): follow package (#7)",
                signature="feat(core)",
                scope="core",
                paths=["src/b.py"],
                categories={"src": 1},
                prs=[7],
            ),
            _commit(
                sha="c" * 40,
                day="2026-05-02",
                subject="feat(core): local continuation",
                signature="feat(core)",
                scope="core",
                paths=["src/c.py"],
                categories={"src": 1},
            ),
        ],
    }

    result = build_active_work_packages(
        start=None,
        end=None,
        commit_payload=payload,
    )
    packages = result["projects"][0]["packages"]

    assert [pkg["unit_type"] for pkg in packages] == ["github_thread", "single_commit"]
    assert packages[0]["unit_key"] == "pr#7"
    assert packages[0]["commit_shas"] == ["a" * 40, "b" * 40]
    assert packages[1]["commit_shas"] == ["c" * 40]
    assert sorted(sha for pkg in packages for sha in pkg["commit_shas"]) == ["a" * 40, "b" * 40, "c" * 40]


def test_active_work_packages_split_scoped_bursts_by_gap(tmp_path: Path) -> None:
    payload = {
        "window": {"start": "2026-05-01", "end": "2026-05-10"},
        "projects": [{"project": "demo", "path": str(tmp_path), "default_branch": "master", "status": "available"}],
        "commits": [
            _commit(
                sha="a" * 40,
                day="2026-05-01",
                subject="refactor(parser): split parser entry",
                signature="refactor(parser)",
                scope="parser",
                paths=["parser/a.py"],
                categories={"analysis": 1},
            ),
            _commit(
                sha="b" * 40,
                day="2026-05-02",
                subject="refactor(parser): move parser helper",
                signature="refactor(parser)",
                scope="parser",
                paths=["parser/b.py"],
                categories={"analysis": 1},
            ),
            _commit(
                sha="c" * 40,
                day="2026-05-08",
                subject="refactor(parser): revisit parser",
                signature="refactor(parser)",
                scope="parser",
                paths=["parser/c.py"],
                categories={"analysis": 1},
            ),
        ],
    }

    result = build_active_work_packages(commit_payload=payload)
    packages = result["projects"][0]["packages"]

    assert packages[0]["unit_type"] == "conventional_scope_burst"
    assert packages[0]["commit_count"] == 2
    assert packages[1]["unit_type"] == "single_commit"
    assert result["summary"]["package_count"] == 2
