import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from lynchpin.graph.current_state import (
    ProjectGitHubFrontier,
    ProjectInventoryItem,
    active_project_inventory,
    analysis_claims_markdown,
    analysis_products_markdown,
    current_state_evidence_pack,
    evidence_pack_markdown,
    github_frontier_markdown,
    github_frontier_summary_markdown,
    inventory_markdown,
    project_github_frontier,
    project_inventory,
)
from lynchpin.core.evidence import SourceReadinessReport
from lynchpin.core.evidence_graph import EvidenceGraph
from lynchpin.core.evidence_graph import EvidenceNode
from lynchpin.graph.movement import MovementSummary
from lynchpin.graph.work_correlation import CorrelatedWorkDay, WorkCorrelationSummary
from lynchpin.sources.github import GitHubActor, GitHubItem
from lynchpin.sources.polylogue import PolylogueReadiness


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_project_inventory_discovers_unregistered_git_repo(tmp_path: Path):
    repo = tmp_path / "local-project"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "feat: initial")

    items = project_inventory(roots=(tmp_path,), include_unregistered=True)
    item = next(item for item in items if item.name == "local-project")

    assert item.exists is True
    assert item.is_git_repo is True
    assert item.head is not None
    assert item.dirty is False
    assert item.last_commit_at is not None
    assert isinstance(item.last_commit_at, datetime)
    assert item.last_commit_subject == "feat: initial"

    table = inventory_markdown([item])
    assert "local-project" in table
    assert "feat: initial" in table


def test_active_project_inventory_excludes_inactive_registry_paths(monkeypatch):
    now = datetime(2026, 5, 7, tzinfo=timezone.utc)
    inactive = ProjectInventoryItem(
        name="legacy-project",
        path=Path("/realm/project/_inactive/legacy-project"),
        exists=True,
        is_git_repo=True,
        branch="master",
        default_branch="master",
        head="abc1234",
        dirty=True,
        ahead=None,
        behind=None,
        last_commit_at=now,
        last_commit_subject="WIP: historical import",
        github_slug=None,
        active_registry_entry=False,
    )
    active = ProjectInventoryItem(
        name="sinex",
        path=Path("/realm/project/sinex"),
        exists=True,
        is_git_repo=True,
        branch="master",
        default_branch="master",
        head="def5678",
        dirty=False,
        ahead=0,
        behind=0,
        last_commit_at=now,
        last_commit_subject="feat: current work",
        github_slug="Sinity/sinex",
        active_registry_entry=True,
    )

    monkeypatch.setattr("lynchpin.graph.current_state.project_inventory", lambda: (inactive, active))

    assert tuple(item.name for item in active_project_inventory()) == ("sinex",)


def test_project_github_frontier_classifies_items(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "remote", "add", "origin", "git@github.com:Sinity/project.git")
    item = next(item for item in project_inventory(roots=(tmp_path,), include_unregistered=True) if item.name == "project")
    gh_item = GitHubItem(
        repo="project",
        slug="Sinity/project",
        kind="issue",
        number=7,
        title="tracking: current state",
        state="open",
        url="https://github.com/Sinity/project/issues/7",
        author=GitHubActor("Sinity"),
        labels=(),
        body="Tracking spine.",
        comments=(),
        created_at=None,
        updated_at=None,
        closed_at=None,
    )

    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: SimpleNamespace(status="ready", reason="ready"),
    )
    monkeypatch.setattr(
        "lynchpin.graph.current_state.iter_github_context",
        lambda *, projects=None, **_kwargs: iter((SimpleNamespace(project="project", item=gh_item),)),
    )

    frontier = project_github_frontier([item])

    assert frontier[0].status == "ok"
    assert frontier[0].items[0].lifecycle.lifecycle == "tracking_or_horizon"
    assert "tracking_or_horizon" in github_frontier_markdown(frontier)
    assert "| project | 1 | 0 | 1 |" in github_frontier_summary_markdown(frontier)


def test_project_github_frontier_includes_recently_closed_prs(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "remote", "add", "origin", "git@github.com:Sinity/project.git")
    item = next(item for item in project_inventory(roots=(tmp_path,), include_unregistered=True) if item.name == "project")
    pr = GitHubItem(
        repo="project",
        slug="Sinity/project",
        kind="pr",
        number=9,
        title="fix: land current state",
        state="merged",
        url="https://github.com/Sinity/project/pull/9",
        author=GitHubActor("Sinity"),
        labels=(),
        body="Merged implementation.",
        comments=(),
        created_at=None,
        updated_at=None,
        closed_at=None,
        merged_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: SimpleNamespace(status="ready", reason="ready"),
    )
    monkeypatch.setattr(
        "lynchpin.graph.current_state.iter_github_context",
        lambda *, projects=None, **_kwargs: iter((SimpleNamespace(project="project", item=pr),)),
    )

    frontier = project_github_frontier([item])

    assert frontier[0].items[0].kind == "pr"
    assert frontier[0].items[0].lifecycle.lifecycle == "pr_closed"


def test_analysis_products_markdown_lists_graph_artifacts(tmp_path: Path):
    graph = EvidenceGraph(
        start=datetime(2026, 5, 1).date(),
        end=datetime(2026, 5, 5).date(),
        generated_at=datetime(2026, 5, 5),
        mode="materialized",
        nodes=(
            EvidenceNode(
                id="analysis:sinex_structure_metrics.json",
                kind="analysis_artifact",
                source="analysis",
                date=datetime(2026, 5, 5).date(),
                project="sinex",
                summary="sinex_structure_metrics.json",
                payload={
                    "name": "sinex_structure_metrics.json",
                    "kind": "json",
                    "generated_at": "2026-05-05T12:00:00+00:00",
                    "brief": "sinex summary",
                    "top_level_keys": ("generated_at_utc", "totals"),
                },
            ),
        ),
        edges=(),
        caveats=(),
    )

    rendered = analysis_products_markdown(graph)

    assert "sinex_structure_metrics.json" in rendered
    assert "sinex summary" in rendered
    assert "generated_at_utc, totals" in rendered


def test_analysis_claims_markdown_lists_graph_claims():
    graph = EvidenceGraph(
        start=datetime(2026, 5, 1).date(),
        end=datetime(2026, 5, 5).date(),
        generated_at=datetime(2026, 5, 5),
        mode="materialized",
        nodes=(
            EvidenceNode(
                id="analysis-claim:active-project-snapshot:sinex",
                kind="analysis_claim",
                source="analysis",
                date=datetime(2026, 5, 5).date(),
                project="sinex",
                summary="sinex: 3 first-parent commits",
                payload={"claim_type": "project_snapshot", "confidence": 0.82},
            ),
        ),
        edges=(),
        caveats=(),
    )

    rendered = analysis_claims_markdown(graph)

    assert "project_snapshot" in rendered
    assert "0.82" in rendered
    assert "sinex: 3 first-parent commits" in rendered


def test_current_state_evidence_pack_combines_readiness_inventory_and_correlations(monkeypatch, tmp_path: Path):
    start = datetime(2026, 5, 1)
    end = datetime(2026, 5, 5)
    inventory_item = project_inventory(roots=(tmp_path,), include_unregistered=False)[0]
    readiness = PolylogueReadiness(
        db_path=tmp_path / "polylogue.db",
        status="degraded",
        reason="session-profile products are stale",
        conversation_count=1,
        message_count=None,
        conversation_stats_count=1,
        session_profile_count=0,
        day_summary_count=0,
        work_event_count=0,
        provider_event_count=None,
        derives_profiles_from_base_tables=True,
        derives_day_summaries_from_profiles=True,
    )
    row = CorrelatedWorkDay(
        date=start.date(),
        project="sinity-lynchpin",
        commit_count=1,
        commit_shas=("abc1234",),
        commit_subjects=("feat: correlate",),
        github_refs=(),
        github_lifecycles={},
        ai_session_count=1,
        ai_conversation_ids=("conv-1",),
        raw_log_count=0,
        raw_log_refs=(),
        focus_minutes=30,
        shell_minutes=5,
        shell_command_count=2,
        sources=("git", "polylogue"),
    )
    summary = WorkCorrelationSummary(
        row_count=1,
        cross_source_row_count=1,
        projects=("sinity-lynchpin",),
        source_counts={"git": 1, "polylogue": 1},
        source_pair_counts={"git+polylogue": 1},
        git_without_ai_or_focus=0,
        ai_without_git=0,
        focus_without_git=0,
        terminal_without_git=0,
    )

    monkeypatch.setattr("lynchpin.graph.current_state.active_project_inventory", lambda: (inventory_item,))
    monkeypatch.setattr("lynchpin.graph.current_state.archive_readiness", lambda: readiness)
    graph_calls = {}

    def fake_build_evidence_graph(*, start, end, projects=None, include_github_frontier=False):
        graph_calls["include_github_frontier"] = include_github_frontier
        return EvidenceGraph(start=start, end=end, generated_at=datetime(2026, 5, 1), mode="materialized", nodes=(), edges=(), caveats=())

    monkeypatch.setattr(
        "lynchpin.graph.current_state.build_evidence_graph",
        fake_build_evidence_graph,
    )
    readiness_calls = {}
    monkeypatch.setattr(
        "lynchpin.graph.current_state.source_readiness",
        lambda *, start, end, include_polylogue_product_counts=False, include_github_frontier=False, include_analysis_inventory=True: readiness_calls.update(
            include_polylogue_product_counts=include_polylogue_product_counts,
            include_github_frontier=include_github_frontier,
            include_analysis_inventory=include_analysis_inventory,
        )
        or SourceReadinessReport(start=start, end=end, generated_at=datetime(2026, 5, 1), sources=()),
    )
    monkeypatch.setattr(
        "lynchpin.graph.current_state.work_day_correlations",
        lambda *, start, end, include_github_context=False, graph=None: (row,),
    )
    monkeypatch.setattr("lynchpin.graph.current_state.summarize_work_correlations", lambda rows: summary)
    monkeypatch.setattr(
        "lynchpin.graph.current_state.movement_summary",
        lambda *, start, end, rows, include_github_context=False: MovementSummary(start=start, end=end, projects=(), caveats=()),
    )
    monkeypatch.setattr("lynchpin.graph.current_state.project_github_frontier", lambda inventory, **kwargs: ())

    pack = current_state_evidence_pack(start=start, end=end)
    rendered = evidence_pack_markdown(pack)

    assert graph_calls["include_github_frontier"] is False
    assert readiness_calls == {
        "include_polylogue_product_counts": True,
        "include_github_frontier": False,
        "include_analysis_inventory": True,
    }
    assert pack.polylogue_readiness.status == "degraded"
    assert pack.correlation_summary.cross_source_row_count == 1
    assert "Polylogue: `degraded`" in rendered
    assert "Source Readiness" in rendered
    assert "Movement Summary" in rendered
    assert "git+polylogue" in rendered
    assert "Strongest Correlated Rows" in rendered
    assert "sinity-lynchpin" in rendered
    assert pack.github_frontiers == ()


def test_current_state_evidence_pack_can_include_github_frontier(monkeypatch, tmp_path: Path):
    start = datetime(2026, 5, 1)
    end = datetime(2026, 5, 5)
    inventory_item = project_inventory(roots=(tmp_path,), include_unregistered=False)[0]
    readiness = PolylogueReadiness(
        db_path=tmp_path / "polylogue.db",
        status="ok",
        reason="ready",
        conversation_count=1,
        message_count=None,
        conversation_stats_count=1,
        session_profile_count=1,
        day_summary_count=1,
        work_event_count=1,
        provider_event_count=None,
        derives_profiles_from_base_tables=False,
        derives_day_summaries_from_profiles=False,
    )
    summary = WorkCorrelationSummary(
        row_count=0,
        cross_source_row_count=0,
        projects=(),
        source_counts={},
        source_pair_counts={},
        git_without_ai_or_focus=0,
        ai_without_git=0,
        focus_without_git=0,
        terminal_without_git=0,
    )
    frontier = (ProjectGitHubFrontier(project="polylogue", slug="Sinity/polylogue", status="ok", reason=None, items=()),)

    monkeypatch.setattr("lynchpin.graph.current_state.active_project_inventory", lambda: (inventory_item,))
    monkeypatch.setattr("lynchpin.graph.current_state.archive_readiness", lambda: readiness)
    monkeypatch.setattr(
        "lynchpin.graph.current_state.build_evidence_graph",
        lambda *, start, end, projects=None, include_github_frontier=False: EvidenceGraph(
            start=start,
            end=end,
            generated_at=datetime(2026, 5, 1),
            mode="network" if include_github_frontier else "materialized",
            nodes=(),
            edges=(),
            caveats=(),
        ),
    )
    readiness_calls = {}
    monkeypatch.setattr(
        "lynchpin.graph.current_state.source_readiness",
        lambda *, start, end, include_polylogue_product_counts=False, include_github_frontier=False, include_analysis_inventory=True: readiness_calls.update(
            include_polylogue_product_counts=include_polylogue_product_counts,
            include_github_frontier=include_github_frontier,
            include_analysis_inventory=include_analysis_inventory,
        )
        or SourceReadinessReport(start=start, end=end, generated_at=datetime(2026, 5, 1), sources=()),
    )
    calls = {}

    def fake_work_day_correlations(*, start, end, include_github_context=False, graph=None):
        calls["include_github_context"] = include_github_context
        return ()

    monkeypatch.setattr("lynchpin.graph.current_state.work_day_correlations", fake_work_day_correlations)
    monkeypatch.setattr("lynchpin.graph.current_state.summarize_work_correlations", lambda rows: summary)
    monkeypatch.setattr(
        "lynchpin.graph.current_state.movement_summary",
        lambda *, start, end, rows, include_github_context=False: MovementSummary(start=start, end=end, projects=(), caveats=()),
    )
    frontier_calls = {}
    monkeypatch.setattr(
        "lynchpin.graph.current_state.project_github_frontier",
        lambda inventory, **kwargs: frontier_calls.update(kwargs) or frontier,
    )

    pack = current_state_evidence_pack(start=start, end=end, include_github_frontier=True)
    rendered = evidence_pack_markdown(pack)

    assert pack.github_frontiers == frontier
    assert frontier_calls == {"start": start.date(), "end": end.date() + timedelta(days=1)}
    assert calls["include_github_context"] is True
    assert readiness_calls == {
        "include_polylogue_product_counts": True,
        "include_github_frontier": True,
        "include_analysis_inventory": True,
    }
    assert "GitHub Frontier" in rendered
    assert "Frontier Items" in rendered
    assert "| polylogue |  | ok |  | 0 | 0 | no items |" in rendered


def test_current_state_evidence_pack_filters_inventory_for_selected_projects(monkeypatch, tmp_path: Path):
    start = datetime(2026, 5, 1)
    end = datetime(2026, 5, 5)
    lynchpin_item = project_inventory(roots=(tmp_path,), include_unregistered=False)[0]
    polylogue_item = ProjectGitHubFrontier(project="polylogue", slug="Sinity/polylogue", status="ok", reason=None, items=())
    readiness = PolylogueReadiness(
        db_path=tmp_path / "polylogue.db",
        status="ok",
        reason="ready",
        conversation_count=1,
        message_count=None,
        conversation_stats_count=1,
        session_profile_count=1,
        day_summary_count=1,
        work_event_count=1,
        provider_event_count=None,
        derives_profiles_from_base_tables=False,
        derives_day_summaries_from_profiles=False,
    )
    summary = WorkCorrelationSummary(
        row_count=0,
        cross_source_row_count=0,
        projects=(),
        source_counts={},
        source_pair_counts={},
        git_without_ai_or_focus=0,
        ai_without_git=0,
        focus_without_git=0,
        terminal_without_git=0,
    )
    seen = {}

    monkeypatch.setattr(
        "lynchpin.graph.current_state.active_project_inventory",
        lambda: (
            lynchpin_item,
            lynchpin_item.__class__(
                name="polylogue",
                path=tmp_path / "polylogue",
                exists=True,
                is_git_repo=True,
                branch="master",
                default_branch="master",
                head="abc",
                dirty=False,
                ahead=0,
                behind=0,
                last_commit_at=None,
                last_commit_subject=None,
                github_slug="Sinity/polylogue",
                active_registry_entry=True,
            ),
        ),
    )
    monkeypatch.setattr("lynchpin.graph.current_state.archive_readiness", lambda: readiness)

    def fake_build_evidence_graph(*, start, end, projects=None, include_github_frontier=False):
        seen["projects"] = projects
        return EvidenceGraph(start=start, end=end, generated_at=datetime(2026, 5, 1), mode="network" if include_github_frontier else "materialized", nodes=(), edges=(), caveats=())

    def fake_project_github_frontier(inventory, **kwargs):
        seen["frontier_inventory"] = tuple(item.name for item in inventory)
        seen["frontier_window"] = kwargs
        return (polylogue_item,)

    monkeypatch.setattr("lynchpin.graph.current_state.build_evidence_graph", fake_build_evidence_graph)
    monkeypatch.setattr(
        "lynchpin.graph.current_state.source_readiness",
        lambda *, start, end, include_polylogue_product_counts=False, include_github_frontier=False, include_analysis_inventory=True: SourceReadinessReport(start=start, end=end, generated_at=datetime(2026, 5, 1), sources=()),
    )
    monkeypatch.setattr("lynchpin.graph.current_state.work_day_correlations", lambda *, start, end, include_github_context=False, graph=None: ())
    monkeypatch.setattr("lynchpin.graph.current_state.summarize_work_correlations", lambda rows: summary)
    monkeypatch.setattr(
        "lynchpin.graph.current_state.movement_summary",
        lambda *, start, end, rows, include_github_context=False: MovementSummary(start=start, end=end, projects=(), caveats=()),
    )
    monkeypatch.setattr("lynchpin.graph.current_state.project_github_frontier", fake_project_github_frontier)

    pack = current_state_evidence_pack(
        start=start,
        end=end,
        projects=("polylogue",),
        include_github_frontier=True,
    )

    assert seen["projects"] == ("polylogue",)
    assert [item.name for item in pack.inventory] == ["polylogue"]
    assert seen["frontier_inventory"] == ("polylogue",)
    assert seen["frontier_window"] == {"start": start.date(), "end": end.date() + timedelta(days=1)}
