from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from lynchpin.analysis.projects.project_velocity_windows import build_project_velocity_windows
from lynchpin.graph.work_correlation import CorrelatedWorkDay


def test_project_velocity_windows_keep_dimensions_separate() -> None:
    commit_payload = {
        "projects": [{"project": "demo", "path": "/tmp/demo", "default_branch": "master", "exists": True}],
        "commits": [
            {
                "project": "demo",
                "sha": "a" * 40,
                "short_sha": "aaaaaaa",
                "timestamp": "2026-05-01T10:00:00+00:00",
                "date": "2026-05-01",
                "subject": "feat(core): land core",
                "author": "Tester",
                "conventional_kind": "feat",
                "conventional_scope": "core",
                "conventional_signature": "feat(core)",
                "files_changed": 2,
                "classified_files_changed": 2,
                "categories": {"src": 2},
                "path_roots": {"core": 2},
                "github_refs": {"prs": [5], "issues": []},
                "paths": ["src/a.py", "src/b.py"],
            }
        ],
    }
    work_payload = {
        "projects": [
            {
                "project": "demo",
                "path": "/tmp/demo",
                "default_branch": "master",
                "packages": [
                    {
                        "work_package_id": "wp:demo:pr:5",
                        "unit_type": "github_thread",
                        "label": "feat(core): land core",
                        "commit_count": 1,
                        "scope_geom": 2.0,
                        "durability_adjusted_scope": 1.5,
                        "top_surfaces": ["src"],
                        "refs": {"prs": [5], "issues": []},
                    }
                ],
            }
        ]
    }
    rows = [
        CorrelatedWorkDay(
            date=date(2026, 5, 1),
            project="demo",
            commit_count=1,
            commit_shas=("a" * 40,),
            commit_subjects=("feat(core): land core",),
            github_refs=("pr#5",),
            github_lifecycles={"executed": 1},
            ai_session_count=2,
            ai_conversation_ids=("conv1", "conv2"),
            raw_log_count=1,
            raw_log_refs=("log:1",),
            focus_minutes=90.0,
            shell_minutes=10.0,
            shell_command_count=4,
            sources=("activitywatch", "git", "github", "polylogue", "raw_log", "terminal"),
        )
    ]

    payload = build_project_velocity_windows(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        commit_payload=commit_payload,
        work_payload=work_payload,
        correlation_rows=rows,
    )
    project = payload["projects"][0]

    assert "velocity_score" not in project
    assert project["micro_effort"]["commit_count"] == 1
    assert project["meso_delivery"]["landed_package_count"] == 1
    assert project["cross_source_support"]["ai_session_count"] == 2
    assert project["cross_source_support"]["focus_hours"] == 1.5
    assert project["interpretation_signals"]["support_level"] == "moderate"
    assert payload["summary"]["moderate_support_projects"] == ["demo"]


def test_project_velocity_windows_builds_correlation_graph_without_analysis_artifacts(monkeypatch) -> None:
    calls = []
    graph = SimpleNamespace(mode="local-fast")

    def fake_build_evidence_graph(**kwargs):
        calls.append(kwargs)
        return graph

    def fake_work_day_correlations(*, start, end, graph):
        assert graph is not None
        return ()

    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.velocity_windows.build_base_evidence_graph",
        fake_build_evidence_graph,
    )
    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.velocity_windows.work_day_correlations",
        fake_work_day_correlations,
    )

    build_project_velocity_windows(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        commit_payload={"projects": [{"project": "demo"}], "commits": []},
        work_payload={"projects": [{"project": "demo", "packages": []}]},
    )

    # The base graph builder doesn't accept (or need) an exclusion list —
    # it never adds analysis nodes by construction.
    assert "exclude_analysis_artifacts" not in calls[0]
    assert calls[0]["mode"] == "local-fast"


# ── package-level cross-source support tests ──


def test_package_support_commit_overlap_strong() -> None:
    """Package matched by commit SHA overlap gets strong support with AI session count."""
    commit_payload = {
        "projects": [{"project": "demo", "path": "/tmp/demo", "default_branch": "master", "exists": True}],
        "commits": [
            {
                "project": "demo",
                "sha": "a" * 40,
                "short_sha": "aaaaaaa",
                "timestamp": "2026-05-01T10:00:00+00:00",
                "date": "2026-05-01",
                "subject": "feat(core): land core",
                "author": "Tester",
                "conventional_kind": "feat",
                "conventional_scope": "core",
                "conventional_signature": "feat(core)",
                "files_changed": 2,
                "classified_files_changed": 2,
                "categories": {"src": 2},
                "path_roots": {"core": 2},
                "github_refs": {"prs": [5], "issues": []},
                "paths": ["src/a.py", "src/b.py"],
            },
            {
                "project": "demo",
                "sha": "c" * 40,
                "short_sha": "ccccccc",
                "timestamp": "2026-05-01T14:00:00+00:00",
                "date": "2026-05-01",
                "subject": "chore: other",
                "author": "Tester",
                "conventional_kind": "chore",
                "conventional_scope": "",
                "conventional_signature": "chore",
                "files_changed": 1,
                "classified_files_changed": 1,
                "categories": {"cfg": 1},
                "path_roots": {"cfg": 1},
                "github_refs": {"prs": [], "issues": []},
                "paths": ["cfg/x.yaml"],
            },
        ],
    }
    work_payload = {
        "projects": [
            {
                "project": "demo",
                "path": "/tmp/demo",
                "default_branch": "master",
                "packages": [
                    {
                        "work_package_id": "wp:demo:pr:5",
                        "unit_type": "github_thread",
                        "label": "feat(core): land core",
                        "commit_count": 1,
                        "commit_shas": ["a" * 40],
                        "first_date": "2026-05-01",
                        "last_date": "2026-05-01",
                        "scope_geom": 2.0,
                        "durability_adjusted_scope": 1.5,
                        "top_surfaces": ["src"],
                        "refs": {"prs": [5], "issues": []},
                    }
                ],
            }
        ],
    }
    rows = [
        CorrelatedWorkDay(
            date=date(2026, 5, 1),
            project="demo",
            commit_count=1,
            commit_shas=("a" * 40,),
            commit_subjects=("feat(core): land core",),
            github_refs=("pr#5",),
            github_lifecycles={"executed": 1},
            ai_session_count=2,
            ai_conversation_ids=("conv1",),
            raw_log_count=0,
            raw_log_refs=(),
            focus_minutes=90.0,
            shell_minutes=10.0,
            shell_command_count=4,
            sources=("activitywatch", "git", "polylogue"),
        ),
        CorrelatedWorkDay(
            date=date(2026, 5, 1),
            project="demo",
            commit_count=1,
            commit_shas=("c" * 40,),
            commit_subjects=("chore: other",),
            github_refs=(),
            github_lifecycles={},
            ai_session_count=0,
            ai_conversation_ids=(),
            raw_log_count=0,
            raw_log_refs=(),
            focus_minutes=30.0,
            shell_minutes=0.0,
            shell_command_count=0,
            sources=("git",),
        ),
    ]

    payload = build_project_velocity_windows(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        commit_payload=commit_payload,
        work_payload=work_payload,
        correlation_rows=rows,
    )
    project = payload["projects"][0]
    packages = project["meso_delivery"]["top_packages"]
    assert len(packages) == 1
    support = packages[0]["cross_source_support"]

    assert support["support_level"] == "strong"
    assert support["strong_match_days"] >= 1
    assert support["match_reasons"]["commit_overlap"] >= 1
    assert support["ai_session_count"] == 2


def test_package_support_github_ref_overlap_without_commit_overlap() -> None:
    """GitHub ref overlap alone yields strong support when non-git sources exist."""
    commit_payload = {
        "projects": [{"project": "demo", "path": "/tmp/demo", "default_branch": "master", "exists": True}],
        "commits": [
            {
                "project": "demo",
                "sha": "b" * 40,
                "short_sha": "bbbbbbb",
                "timestamp": "2026-05-02T10:00:00+00:00",
                "date": "2026-05-02",
                "subject": "feat(api): add endpoint",
                "author": "Tester",
                "conventional_kind": "feat",
                "conventional_scope": "api",
                "conventional_signature": "feat(api)",
                "files_changed": 3,
                "classified_files_changed": 3,
                "categories": {"src": 3},
                "path_roots": {"api": 3},
                "github_refs": {"prs": [10], "issues": [20]},
                "paths": ["src/api/x.py"],
            }
        ],
    }
    work_payload = {
        "projects": [
            {
                "project": "demo",
                "path": "/tmp/demo",
                "default_branch": "master",
                "packages": [
                    {
                        "work_package_id": "wp:demo:pr:10",
                        "unit_type": "github_thread",
                        "label": "feat(api): add endpoint",
                        "commit_count": 1,
                        "commit_shas": ["b" * 40],
                        "first_date": "2026-05-02",
                        "last_date": "2026-05-02",
                        "scope_geom": 3.0,
                        "durability_adjusted_scope": 2.0,
                        "top_surfaces": ["api"],
                        "refs": {"prs": [10], "issues": [20]},
                    }
                ],
            }
        ],
    }
    rows = [
        CorrelatedWorkDay(
            date=date(2026, 5, 2),
            project="demo",
            commit_count=1,
            commit_shas=("d" * 40,),  # different SHA — no commit overlap
            commit_subjects=("feat(api): add endpoint",),
            github_refs=("pr#10",),  # ref overlap
            github_lifecycles={"executed": 1},
            ai_session_count=1,
            ai_conversation_ids=("conv3",),
            raw_log_count=0,
            raw_log_refs=(),
            focus_minutes=60.0,
            shell_minutes=5.0,
            shell_command_count=2,
            sources=("git", "github"),
        ),
    ]

    payload = build_project_velocity_windows(
        start=date(2026, 5, 2),
        end=date(2026, 5, 3),
        commit_payload=commit_payload,
        work_payload=work_payload,
        correlation_rows=rows,
    )
    support = payload["projects"][0]["meso_delivery"]["top_packages"][0]["cross_source_support"]

    assert support["support_level"] == "strong"
    assert support["match_reasons"]["github_ref_overlap"] >= 1
    assert support["match_reasons"]["commit_overlap"] == 0


def test_package_support_date_only_moderate() -> None:
    """Date-only match with 2+ sources yields moderate support."""
    commit_payload = {
        "projects": [{"project": "demo", "path": "/tmp/demo", "default_branch": "master", "exists": True}],
        "commits": [],
    }
    work_payload = {
        "projects": [
            {
                "project": "demo",
                "path": "/tmp/demo",
                "default_branch": "master",
                "packages": [
                    {
                        "work_package_id": "wp:demo:heuristic:1",
                        "unit_type": "heuristic",
                        "label": "research spike",
                        "commit_count": 0,
                        "commit_shas": [],
                        "first_date": "2026-05-01",
                        "last_date": "2026-05-03",
                        "scope_geom": 0.0,
                        "durability_adjusted_scope": 0.5,
                        "top_surfaces": [],
                        "refs": {},
                    }
                ],
            }
        ],
    }
    rows = [
        CorrelatedWorkDay(
            date=date(2026, 5, 2),
            project="demo",
            commit_count=0,
            commit_shas=(),
            commit_subjects=(),
            github_refs=(),
            github_lifecycles={},
            ai_session_count=3,
            ai_conversation_ids=("conv4", "conv5"),
            raw_log_count=0,
            raw_log_refs=(),
            focus_minutes=120.0,
            shell_minutes=15.0,
            shell_command_count=6,
            sources=("polylogue", "activitywatch", "terminal"),
        ),
    ]

    payload = build_project_velocity_windows(
        start=date(2026, 5, 1),
        end=date(2026, 5, 4),
        commit_payload=commit_payload,
        work_payload=work_payload,
        correlation_rows=rows,
    )
    support = payload["projects"][0]["meso_delivery"]["top_packages"][0]["cross_source_support"]

    assert support["support_level"] == "moderate"
    assert support["match_reasons"]["date_overlap"] >= 1
    assert support["match_reasons"]["commit_overlap"] == 0
    assert support["match_reasons"]["github_ref_overlap"] == 0


def test_package_support_no_matching_rows_weak() -> None:
    """Package with no matching rows gets weak support and a caveat."""
    commit_payload = {
        "projects": [{"project": "demo", "path": "/tmp/demo", "default_branch": "master", "exists": True}],
        "commits": [
            {
                "project": "demo",
                "sha": "e" * 40,
                "short_sha": "eeeeeee",
                "timestamp": "2026-05-01T10:00:00+00:00",
                "date": "2026-05-01",
                "subject": "fix: something",
                "author": "Tester",
                "conventional_kind": "fix",
                "conventional_scope": "",
                "conventional_signature": "fix",
                "files_changed": 1,
                "classified_files_changed": 1,
                "categories": {"src": 1},
                "path_roots": {"src": 1},
                "github_refs": {"prs": [99], "issues": []},
                "paths": ["src/z.py"],
            }
        ],
    }
    work_payload = {
        "projects": [
            {
                "project": "demo",
                "path": "/tmp/demo",
                "default_branch": "master",
                "packages": [
                    {
                        "work_package_id": "wp:demo:pr:99",
                        "unit_type": "github_thread",
                        "label": "fix: something",
                        "commit_count": 1,
                        "commit_shas": ["e" * 40],
                        "first_date": "2026-05-01",
                        "last_date": "2026-05-01",
                        "scope_geom": 1.0,
                        "durability_adjusted_scope": 0.5,
                        "top_surfaces": ["src"],
                        "refs": {"prs": [99], "issues": []},
                    }
                ],
            }
        ],
    }
    rows = [
        CorrelatedWorkDay(
            date=date(2026, 5, 1),
            project="other-project",  # different project
            commit_count=1,
            commit_shas=("e" * 40,),
            commit_subjects=("fix: something",),
            github_refs=("pr#99",),
            github_lifecycles={"executed": 1},
            ai_session_count=0,
            ai_conversation_ids=(),
            raw_log_count=0,
            raw_log_refs=(),
            focus_minutes=30.0,
            shell_minutes=0.0,
            shell_command_count=0,
            sources=("git",),
        ),
    ]

    payload = build_project_velocity_windows(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        commit_payload=commit_payload,
        work_payload=work_payload,
        correlation_rows=rows,
    )
    support = payload["projects"][0]["meso_delivery"]["top_packages"][0]["cross_source_support"]

    assert support["support_level"] == "weak"
    assert any("no correlated" in c for c in support["caveats"])
    assert support["support_days"] == 0


def test_package_support_fields_in_top_packages() -> None:
    """Every package in top_packages carries a cross_source_support key with required fields."""
    commit_payload = {
        "projects": [
            {"project": "demo", "path": "/tmp/demo", "default_branch": "master", "exists": True},
            {"project": "other", "path": "/tmp/other", "default_branch": "main", "exists": True},
        ],
        "commits": [
            {
                "project": "demo",
                "sha": "a" * 40,
                "short_sha": "aaaaaaa",
                "timestamp": "2026-05-01T10:00:00+00:00",
                "date": "2026-05-01",
                "subject": "feat(core): land core",
                "author": "Sinity",
                "conventional_kind": "feat",
                "conventional_scope": "core",
                "conventional_signature": "feat(core)",
                "files_changed": 2,
                "classified_files_changed": 2,
                "categories": {"src": 2},
                "path_roots": {"core": 2},
                "github_refs": {"prs": [5], "issues": []},
                "paths": ["src/a.py", "src/b.py"],
            },
            {
                "project": "other",
                "sha": "f" * 40,
                "short_sha": "fffffff",
                "timestamp": "2026-05-02T10:00:00+00:00",
                "date": "2026-05-02",
                "subject": "fix(other): patch",
                "author": "Sinity",
                "conventional_kind": "fix",
                "conventional_scope": "other",
                "conventional_signature": "fix(other)",
                "files_changed": 1,
                "classified_files_changed": 1,
                "categories": {"src": 1},
                "path_roots": {"other": 1},
                "github_refs": {"prs": [7], "issues": []},
                "paths": ["src/o.py"],
            },
        ],
    }
    work_payload = {
        "projects": [
            {
                "project": "demo",
                "path": "/tmp/demo",
                "default_branch": "master",
                "packages": [
                    {
                        "work_package_id": "wp:demo:pr:5",
                        "unit_type": "github_thread",
                        "label": "feat(core): land core",
                        "commit_count": 1,
                        "commit_shas": ["a" * 40],
                        "first_date": "2026-05-01",
                        "last_date": "2026-05-01",
                        "scope_geom": 2.0,
                        "durability_adjusted_scope": 1.5,
                        "top_surfaces": ["src"],
                        "refs": {"prs": [5], "issues": []},
                    },
                    {
                        "work_package_id": "wp:demo:heuristic:2",
                        "unit_type": "heuristic",
                        "label": "misc changes",
                        "commit_count": 0,
                        "commit_shas": [],
                        "first_date": "2026-05-01",
                        "last_date": "2026-05-01",
                        "scope_geom": 0.0,
                        "durability_adjusted_scope": 0.3,
                        "top_surfaces": [],
                        "refs": {},
                    },
                ],
            },
            {
                "project": "other",
                "path": "/tmp/other",
                "default_branch": "main",
                "packages": [
                    {
                        "work_package_id": "wp:other:pr:7",
                        "unit_type": "github_thread",
                        "label": "fix(other): patch",
                        "commit_count": 1,
                        "commit_shas": ["f" * 40],
                        "first_date": "2026-05-02",
                        "last_date": "2026-05-02",
                        "scope_geom": 1.0,
                        "durability_adjusted_scope": 0.5,
                        "top_surfaces": ["other"],
                        "refs": {"prs": [7], "issues": []},
                    }
                ],
            },
        ],
    }
    rows = [
        CorrelatedWorkDay(
            date=date(2026, 5, 1),
            project="demo",
            commit_count=1,
            commit_shas=("a" * 40,),
            commit_subjects=("feat(core): land core",),
            github_refs=("pr#5",),
            github_lifecycles={"executed": 1},
            ai_session_count=2,
            ai_conversation_ids=("conv1",),
            raw_log_count=0,
            raw_log_refs=(),
            focus_minutes=90.0,
            shell_minutes=10.0,
            shell_command_count=4,
            sources=("activitywatch", "git", "polylogue"),
        ),
        CorrelatedWorkDay(
            date=date(2026, 5, 2),
            project="other",
            commit_count=1,
            commit_shas=("f" * 40,),
            commit_subjects=("fix(other): patch",),
            github_refs=("pr#7",),
            github_lifecycles={"merged": 1},
            ai_session_count=1,
            ai_conversation_ids=("conv-other",),
            raw_log_count=1,
            raw_log_refs=("log:1",),
            focus_minutes=45.0,
            shell_minutes=5.0,
            shell_command_count=2,
            sources=("activitywatch", "git", "github", "raw_log", "terminal"),
        ),
    ]

    payload = build_project_velocity_windows(
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
        commit_payload=commit_payload,
        work_payload=work_payload,
        correlation_rows=rows,
    )

    required_fields = (
        "support_days",
        "strong_match_days",
        "sources",
        "source_counts",
        "source_pair_counts",
        "github_lifecycles",
        "ai_session_count",
        "focus_hours",
        "shell_command_count",
        "raw_log_count",
        "github_ref_count",
        "match_reasons",
        "support_level",
        "caveats",
    )

    for project in payload["projects"]:
        for pkg in project["meso_delivery"]["top_packages"]:
            assert "cross_source_support" in pkg, f"{pkg.get('work_package_id')} missing cross_source_support"
            support = pkg["cross_source_support"]
            for field in required_fields:
                assert field in support, f"missing field {field} in {pkg.get('work_package_id')}"
            assert support["support_level"] in ("strong", "moderate", "weak")
            assert isinstance(support["caveats"], list)
            assert isinstance(support["sources"], list)
            assert isinstance(support["match_reasons"], dict)
            for reason_key in ("commit_overlap", "github_ref_overlap", "date_overlap"):
                assert reason_key in support["match_reasons"]


def test_no_velocity_score_with_package_support() -> None:
    """No velocity_score appears anywhere in the payload, recursively."""
    commit_payload = {
        "projects": [{"project": "demo", "path": "/tmp/demo", "default_branch": "master", "exists": True}],
        "commits": [
            {
                "project": "demo",
                "sha": "a" * 40,
                "short_sha": "aaaaaaa",
                "timestamp": "2026-05-01T10:00:00+00:00",
                "date": "2026-05-01",
                "subject": "feat(core): land core",
                "author": "Tester",
                "conventional_kind": "feat",
                "conventional_scope": "core",
                "conventional_signature": "feat(core)",
                "files_changed": 2,
                "classified_files_changed": 2,
                "categories": {"src": 2},
                "path_roots": {"core": 2},
                "github_refs": {"prs": [5], "issues": []},
                "paths": ["src/a.py", "src/b.py"],
            }
        ],
    }
    work_payload = {
        "projects": [
            {
                "project": "demo",
                "path": "/tmp/demo",
                "default_branch": "master",
                "packages": [
                    {
                        "work_package_id": "wp:demo:pr:5",
                        "unit_type": "github_thread",
                        "label": "feat(core): land core",
                        "commit_count": 1,
                        "commit_shas": ["a" * 40],
                        "first_date": "2026-05-01",
                        "last_date": "2026-05-01",
                        "scope_geom": 2.0,
                        "durability_adjusted_scope": 1.5,
                        "top_surfaces": ["src"],
                        "refs": {"prs": [5], "issues": []},
                    }
                ],
            }
        ],
    }
    rows = [
        CorrelatedWorkDay(
            date=date(2026, 5, 1),
            project="demo",
            commit_count=1,
            commit_shas=("a" * 40,),
            commit_subjects=("feat(core): land core",),
            github_refs=("pr#5",),
            github_lifecycles={"executed": 1},
            ai_session_count=2,
            ai_conversation_ids=("conv1",),
            raw_log_count=0,
            raw_log_refs=(),
            focus_minutes=90.0,
            shell_minutes=10.0,
            shell_command_count=4,
            sources=("activitywatch", "git", "polylogue"),
        ),
    ]

    payload = build_project_velocity_windows(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        commit_payload=commit_payload,
        work_payload=work_payload,
        correlation_rows=rows,
    )

    def _find_key(obj: object, key: str, path: str = "") -> str | None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == key:
                    return f"{path}.{k}"
                found = _find_key(v, key, f"{path}.{k}")
                if found:
                    return found
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                found = _find_key(v, key, f"{path}[{i}]")
                if found:
                    return found
        return None

    found = _find_key(payload, "velocity_score")
    assert found is None, f"velocity_score found at {found}"
