import importlib
from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.core.evidence_graph import EvidenceGraph
from lynchpin.graph.evidence_graph import (
    EvidenceGraphBuildContext,
    build_base_evidence_graph,
    build_evidence_graph,
)
from lynchpin.graph.evidence_views import render_evidence_graph_summary
from lynchpin.graph.work_correlation import work_day_correlations
from lynchpin.sources.github import GitHubActor, GitHubItem
from tests.graph.evidence_graph_fixtures import (  # noqa: F401
    _mock_empty_sources,
    _no_analysis_claims,
    _no_substrate_overlap,
)

UTC = timezone.utc


def test_activitywatch_evidence_uses_core_focus_spans(monkeypatch) -> None:
    """Focus evidence should not pay for prompt-facing gap-healed timelines."""
    from lynchpin.graph import evidence_activitywatch

    calls = {}
    start = datetime(2026, 5, 5, 8, tzinfo=UTC)
    end = datetime(2026, 5, 5, 9, tzinfo=UTC)

    def fake_focus_spans(**kwargs):
        calls.update(kwargs)
        return [
            SimpleNamespace(
                start=start,
                end=end,
                kind="focused",
                app="kitty",
                title="lynchpin",
                mode="coding",
                project="lynchpin",
                duration_s=3600,
                keypress_count=42,
                keylog_state="available",
            )
        ]

    def fail_focus_timeline(**_kwargs):
        raise AssertionError("focus_timeline should not be used for evidence spans")

    monkeypatch.setattr(evidence_activitywatch, "focus_spans", fake_focus_spans)
    monkeypatch.setattr(evidence_activitywatch, "focus_timeline", fail_focus_timeline)
    monkeypatch.setattr(evidence_activitywatch, "deep_work", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "circadian", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "loops", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "fragmentation", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "attention", lambda **kwargs: ())
    monkeypatch.setattr(
        evidence_activitywatch, "project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        evidence_activitywatch, "ensure_activitywatch_derived", lambda **kwargs: None
    )

    nodes = []
    evidence_activitywatch.add_focus(
        nodes,
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        selected=set(),
    )

    assert calls["min_duration_s"] == 60.0
    assert calls["enrich_polylogue"] is False
    assert [node.kind for node in nodes] == ["focus_span"]
    assert nodes[0].payload["span_source"] == "aw_trimmed"
    assert nodes[0].payload["keypress_count"] == 42


def test_activitywatch_materialized_evidence_keeps_detail(monkeypatch) -> None:
    """Materialized graph builds must not drop ActivityWatch detail."""
    from lynchpin.graph import evidence_activitywatch

    start = datetime(2026, 6, 6, 8, tzinfo=UTC)
    end = datetime(2026, 6, 6, 9, tzinfo=UTC)

    monkeypatch.setattr(
        evidence_activitywatch,
        "focus_spans",
        lambda **kwargs: (
            SimpleNamespace(
                start=start,
                end=end,
                kind="focused",
                app="kitty",
                title="lynchpin",
                mode="coding",
                project="lynchpin",
                duration_s=3600,
                keypress_count=12,
                keylog_state="available",
            ),
        ),
    )
    monkeypatch.setattr(evidence_activitywatch, "deep_work", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "circadian", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "loops", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "fragmentation", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "attention", lambda **kwargs: ())
    monkeypatch.setattr(
        evidence_activitywatch, "ensure_activitywatch_derived", lambda **kwargs: None
    )
    monkeypatch.setattr(
        evidence_activitywatch,
        "project_focus_days",
        lambda **kwargs: (
            SimpleNamespace(
                date=date(2026, 6, 6),
                project="lynchpin",
                duration_s=7200.0,
            ),
        ),
    )

    nodes = []
    evidence_activitywatch.add_focus(
        nodes,
        start=date(2026, 6, 6),
        end=date(2026, 6, 6),
        selected=set(),
    )

    assert [node.kind for node in nodes] == ["focus_span", "focus_day"]
    assert nodes[0].source == "activitywatch"
    assert nodes[0].project == "sinity-lynchpin"
    assert nodes[0].payload["duration_s"] == 3600
    assert nodes[0].payload["keypress_count"] == 12
    assert nodes[1].payload["duration_s"] == 7200.0


def test_activitywatch_graph_evidence_ensures_requested_window(monkeypatch) -> None:
    from lynchpin.graph import evidence_activitywatch

    calls = []
    monkeypatch.setattr(evidence_activitywatch, "focus_spans", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "deep_work", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "circadian", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "loops", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "fragmentation", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "attention", lambda **kwargs: ())
    monkeypatch.setattr(
        evidence_activitywatch, "project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        evidence_activitywatch,
        "ensure_activitywatch_derived",
        lambda **kwargs: calls.append(kwargs),
    )

    evidence_activitywatch.add_focus(
        [],
        start=date(2026, 6, 5),
        end=date(2026, 6, 6),
        selected=set(),
    )

    assert calls == [{"start": date(2026, 6, 5), "end": date(2026, 6, 6)}]

    calls.clear()
    evidence_activitywatch.add_focus(
        [],
        start=date(2026, 6, 5),
        end=date(2026, 6, 6),
        selected=set(),
    )

    assert calls == [{"start": date(2026, 6, 5), "end": date(2026, 6, 6)}]


def test_activitywatch_ensure_wrapper_uses_exclusive_end(monkeypatch) -> None:
    import lynchpin.graph.evidence_activitywatch as evidence_activitywatch

    evidence_activitywatch = importlib.reload(evidence_activitywatch)

    calls = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window, budget="inline": calls.append((name, window, budget)),
    )

    evidence_activitywatch.ensure_activitywatch_derived(
        start=date(2026, 6, 5),
        end=date(2026, 6, 6),
    )

    assert calls == [
        ("activitywatch_derived", (date(2026, 6, 5), date(2026, 6, 7)), "manual")
    ]


def test_activitywatch_graph_wrappers_prefer_derived_products(monkeypatch) -> None:
    import lynchpin.graph.evidence_activitywatch as evidence_activitywatch

    evidence_activitywatch = importlib.reload(evidence_activitywatch)

    start = datetime(2026, 6, 6, 8, tzinfo=UTC)
    end = datetime(2026, 6, 6, 9, tzinfo=UTC)
    product_span = SimpleNamespace(
        start=start,
        end=end,
        kind="focused",
        app="kitty",
        title="derived",
        mode="coding",
        project="lynchpin",
        duration_s=3600.0,
        keypress_count=1,
        keylog_state="available",
    )

    monkeypatch.setattr(
        "lynchpin.sources.activitywatch_derived.iter_derived_focus_spans",
        lambda **kwargs: (product_span,),
    )
    monkeypatch.setattr(
        "lynchpin.sources.activitywatch.focus_spans",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("source fallback should not run")
        ),
    )

    assert evidence_activitywatch.focus_spans(start=start, end=end) == (product_span,)


def test_activitywatch_graph_wrappers_do_not_fallback_to_raw(monkeypatch) -> None:
    import lynchpin.graph.evidence_activitywatch as evidence_activitywatch

    evidence_activitywatch = importlib.reload(evidence_activitywatch)

    start = datetime(2026, 6, 6, 8, tzinfo=UTC)
    end = datetime(2026, 6, 6, 9, tzinfo=UTC)
    fallback_calls = []

    def missing_product(**_kwargs):
        raise FileNotFoundError("missing derived product")

    monkeypatch.setattr(
        "lynchpin.sources.activitywatch_derived.iter_derived_focus_spans",
        missing_product,
    )
    monkeypatch.setattr(
        "lynchpin.sources.activitywatch.focus_spans",
        lambda **kwargs: fallback_calls.append(kwargs) or (),
    )

    try:
        evidence_activitywatch.focus_spans(start=start, end=end)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError(
            "missing derived product should surface to the graph caveat path"
        )

    assert fallback_calls == []


def test_materialized_web_evidence_uses_webhistory_source(monkeypatch) -> None:
    from lynchpin.graph import evidence_web_media

    ensure_calls = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window, budget="inline": ensure_calls.append(
            (name, window, budget)
        ),
    )
    monkeypatch.setattr(
        evidence_web_media,
        "daily_browsing",
        lambda **kwargs: (
            SimpleNamespace(
                date=date(2026, 6, 3),
                visit_count=42,
                unique_domains=7,
                top_domains=(("github.com", 21.0),),
                top_titles=("Pull request",),
            ),
        ),
    )

    nodes = []
    evidence_web_media.add_web(
        nodes,
        start=date(2026, 6, 1),
        end=date(2026, 6, 6),
        selected=set(),
    )

    assert len(nodes) == 1
    assert nodes[0].kind == "web_domain_day"
    assert nodes[0].date == date(2026, 6, 3)
    assert nodes[0].summary == "42 visits, 7 domains, top: github.com"
    assert nodes[0].payload["visit_count"] == 42
    assert nodes[0].payload["unique_domains"] == 7
    assert nodes[0].payload["top_domains"] == [("github.com", 21.0)]
    assert nodes[0].payload["top_titles"] == ["Pull request"]
    assert nodes[0].provenance.cost == "materialized"
    assert ensure_calls == [
        ("webhistory", (date(2026, 6, 1), date(2026, 6, 7)), "manual")
    ]


def test_activitywatch_evidence_keeps_focus_detail(monkeypatch) -> None:
    """Local ActivityWatch graph evidence is product-backed but still detailed."""
    from lynchpin.graph import evidence_activitywatch

    calls = []
    start = datetime(2026, 5, 5, 8, tzinfo=UTC)
    end = datetime(2026, 5, 5, 9, tzinfo=UTC)

    def fake_focus_spans(**kwargs):
        calls.append(kwargs)
        return [
            SimpleNamespace(
                start=start,
                end=end,
                kind="focused",
                app="kitty",
                title="lynchpin",
                mode="coding",
                project="lynchpin",
                duration_s=3600,
                keypress_count=1,
                keylog_state="available",
            )
        ]

    monkeypatch.setattr(evidence_activitywatch, "focus_spans", fake_focus_spans)
    monkeypatch.setattr(evidence_activitywatch, "deep_work", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "circadian", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "loops", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "fragmentation", lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "attention", lambda **kwargs: ())
    monkeypatch.setattr(
        evidence_activitywatch, "project_focus_days", lambda **kwargs: ()
    )

    nodes = []
    evidence_activitywatch.add_focus(
        nodes,
        start=date(2026, 6, 6),
        end=date(2026, 6, 6),
        selected=set(),
    )

    assert calls
    assert [node.kind for node in nodes] == ["focus_span"]


def test_materialized_graph_uses_only_converged_builder_surfaces(
    monkeypatch,
) -> None:
    """Default materialized graph builds should call only product-backed surfaces."""
    machine_calls = []
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.add_machine_analysis_nodes",
        lambda nodes, edges, **kwargs: machine_calls.append(kwargs),
    )
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.focus_spans",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.terminal_patterns.detect_patterns",
        lambda **kwargs: (),
    )
    personal_calls = []
    monkeypatch.setattr(
        "lynchpin.graph.evidence_personal_products.add_personal_daily_signals",
        lambda nodes, **kwargs: personal_calls.append(("daily", kwargs)),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_personal_products.add_personal_products",
        lambda nodes, **kwargs: personal_calls.append(("products", kwargs)),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.health_bridge.build_sleep_evidence",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.health_bridge.build_health_evidence",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.health_bridge.build_sleep_productivity_links",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.sleep_productivity.iter_sleep_productivity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: ()
    )
    readiness_calls = []
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: readiness_calls.append(kwargs) or SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(start=date(2026, 6, 1), end=date(2026, 6, 6))

    assert graph.nodes == ()
    assert readiness_calls == [
        {
            "start": date(2026, 6, 1),
            "end": date(2026, 6, 6),
            "include_polylogue_product_counts": True,
            "include_github_frontier": False,
            "include_analysis_inventory": True,
            "repair_materializations": False,
        }
    ]
    assert machine_calls == [
        {
            "start": date(2026, 6, 1),
            "end": date(2026, 6, 6),
            "selected": set(),
            "exclude_names": frozenset(),
        }
    ]
    assert personal_calls == [
        ("daily", {"start": date(2026, 6, 1), "end": date(2026, 6, 6)}),
        ("products", {"start": date(2026, 6, 1), "end": date(2026, 6, 6)}),
    ]


def test_materialized_graph_includes_ensured_personal_daily_signals(
    monkeypatch,
) -> None:
    from lynchpin.graph import evidence_personal_products

    calls: list[tuple[str, tuple[date, date], str]] = []

    monkeypatch.setattr(
        "lynchpin.graph.evidence_personal_products.ensure_materialized",
        lambda name, *, window, budget="inline": calls.append((name, window, budget)),
        raising=False,
    )
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_personal_daily_signals",
        lambda *, start, end, ensure=True: (
            SimpleNamespace(
                source="keylog",
                date=date(2026, 6, 6),
                metric="keypress_count",
                value=42.0,
                dimensions={"kind": "changed"},
            ),
        ),
    )

    nodes = []
    evidence_personal_products.add_personal_daily_signals(
        nodes,
        start=date(2026, 6, 6),
        end=date(2026, 6, 6),
    )

    assert calls == [
        ("personal_daily_signals", (date(2026, 6, 6), date(2026, 6, 7)), "manual")
    ]
    assert [node.kind for node in nodes] == ["personal_daily_signal"]
    assert nodes[0].source == "keylog"
    assert nodes[0].payload["metric"] == "keypress_count"


def test_polylogue_work_events_degrade_locally_when_products_fail(
    monkeypatch,
) -> None:
    from lynchpin.graph.evidence_polylogue import add_polylogue_work_events

    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )

    def broken_work_events(**kwargs):
        from lynchpin.sources.polylogue import PolylogueMaterializationError

        raise PolylogueMaterializationError("session work-event rows are incomplete")

    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", broken_work_events
    )

    nodes = []
    add_polylogue_work_events(
        nodes,
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        selected=set(),
    )

    assert nodes == []


def test_build_evidence_graph_links_commit_refs_and_project_day(monkeypatch):
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.add_machine_analysis_nodes",
        lambda nodes, edges, **kwargs: None,
    )
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_git.commit_facts",
        lambda **kwargs: (
            SimpleNamespace(
                repo="sinity-lynchpin",
                commit="abc123",
                authored_at=when,
                author="Sinity",
                subject="fix: graph closes #17",
                lines_added=3,
                lines_deleted=1,
                lines_changed=4,
                files_changed=1,
                paths=(),
            ),
        ),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.focus_spans", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.focus_timeline", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(start=date(2026, 5, 5), end=date(2026, 5, 5))

    assert {node.kind for node in graph.nodes} == {"commit", "github_ref"}
    assert any(
        node.kind == "github_ref" and node.source == "github_ref"
        for node in graph.nodes
    )
    assert any(edge.relation == "references" for edge in graph.edges)
    assert "commit=1" in render_evidence_graph_summary(graph)


def test_build_evidence_graph_passes_mode_to_source_readiness(monkeypatch):
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.add_machine_analysis_nodes",
        lambda nodes, edges, **kwargs: None,
    )
    calls = {}
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: ()
    )

    def fake_source_readiness(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(caveats=())

    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness", fake_source_readiness
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5), end=date(2026, 5, 5), include_github_frontier=True
    )

    assert graph.mode == "network"
    assert calls["include_polylogue_product_counts"] is True
    assert calls["include_github_frontier"] is True
    assert calls["repair_materializations"] is False


def test_network_evidence_graph_uses_temporal_focus_spans(monkeypatch):
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.add_machine_analysis_nodes",
        lambda nodes, edges, **kwargs: None,
    )
    start = datetime(2026, 5, 5, 12, tzinfo=UTC)
    end = datetime(2026, 5, 5, 12, 45, tzinfo=UTC)
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.focus_spans",
        lambda **kwargs: (
            SimpleNamespace(
                start=start,
                end=end,
                date=start.date(),
                kind="focused",
                app="kitty",
                title="nvim /realm/project/sinity-lynchpin",
                mode="coding",
                project="sinity-lynchpin",
                source="aw_trimmed",
                duration_s=2700,
                keypress_count=120,
                keylog_state="covered",
            ),
        ),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(
        start=start.date(),
        end=start.date(),
        include_github_frontier=True,
    )
    rows = work_day_correlations(start=start.date(), end=start.date(), graph=graph)

    assert [node.kind for node in graph.nodes] == ["focus_span"]
    assert graph.nodes[0].start == start
    assert graph.nodes[0].payload["keypress_count"] == 120
    assert rows[0].focus_minutes == 45


def test_network_evidence_graph_enriches_only_selected_project_commits(monkeypatch):
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    captured = {}
    facts = (
        SimpleNamespace(
            repo="sinity-lynchpin",
            commit="abc123",
            authored_at=when,
            author="Sinity",
            subject="fix: selected (#1)",
            lines_added=1,
            lines_deleted=0,
            lines_changed=1,
            files_changed=1,
            paths=(),
        ),
        SimpleNamespace(
            repo="sinex",
            commit="def456",
            authored_at=when,
            author="Sinity",
            subject="fix: other (#2)",
            lines_added=1,
            lines_deleted=0,
            lines_changed=1,
            files_changed=1,
            paths=(),
        ),
    )

    monkeypatch.setattr(
        "lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: facts
    )

    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window, budget="inline": SimpleNamespace(
            status="ready", reason="ready"
        ),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_git.iter_github_context",
        lambda *, projects=None, **_kwargs: captured.setdefault(
            "projects", tuple(sorted(projects or ()))
        )
        and iter(()),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.add_machine_analysis_nodes",
        lambda *args, **kwargs: None,
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        projects=("sinity-lynchpin",),
        include_github_frontier=True,
    )

    assert captured["projects"] == ("sinity-lynchpin",)
    assert {node.project for node in graph.nodes} == {"sinity-lynchpin"}


def test_materialized_git_evidence_uses_ttl_backed_github_context(monkeypatch) -> None:
    from lynchpin.graph import evidence_git

    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    fact = SimpleNamespace(
        repo="sinity-lynchpin",
        commit="abc123",
        authored_at=when,
        author="Sinity",
        subject="fix: selected (#1)",
        lines_added=1,
        lines_deleted=0,
        lines_changed=1,
        files_changed=1,
        paths=(),
    )
    calls = []
    item = GitHubItem(
        repo="sinity-lynchpin",
        slug="Sinity/sinity-lynchpin",
        kind="pr",
        number=1,
        title="fix: selected",
        state="merged",
        url="https://github.com/Sinity/sinity-lynchpin/pull/1",
        author=GitHubActor("Sinity"),
        labels=(),
        body="",
        comments=(),
        created_at=None,
        updated_at=None,
        closed_at=datetime(2026, 5, 5, 13, tzinfo=UTC),
        merged_at=datetime(2026, 5, 5, 13, tzinfo=UTC),
    )

    monkeypatch.setattr(evidence_git, "commit_facts", lambda **kwargs: (fact,))
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window, budget="inline": calls.append((name, window, budget))
        or SimpleNamespace(status="ready", reason="ready"),
    )
    monkeypatch.setattr(
        evidence_git,
        "iter_github_context",
        lambda *, projects=None, **_kwargs: iter(
            (SimpleNamespace(project="sinity-lynchpin", item=item),)
        ),
    )

    nodes = []
    edges = []
    evidence_git.add_git(
        nodes,
        edges,
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        selected=set(),
        mode="materialized",
    )

    assert calls == [("github_context", (date(2026, 5, 5), date(2026, 5, 6)), "manual")]
    assert any(node.kind == "github_pr" for node in nodes)
    github = next(node for node in nodes if node.kind == "github_pr")
    assert github.provenance.cost == "materialized"
    assert github.payload["lifecycle"] == "pr_closed"


def test_materialized_git_evidence_ignores_github_cache_misses(monkeypatch) -> None:
    from lynchpin.graph import evidence_git

    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    fact = SimpleNamespace(
        repo="sinity-lynchpin",
        commit="abc123",
        authored_at=when,
        author="Sinity",
        subject="fix: selected (#1)",
        lines_added=1,
        lines_deleted=0,
        lines_changed=1,
        files_changed=1,
        paths=(),
    )

    monkeypatch.setattr(evidence_git, "commit_facts", lambda **kwargs: (fact,))
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window, budget="inline": SimpleNamespace(
            status="ready", reason="ready"
        ),
    )
    monkeypatch.setattr(
        evidence_git,
        "iter_github_context",
        lambda *, projects=None, **_kwargs: iter(()),
    )

    nodes = []
    edges = []
    evidence_git.add_git(
        nodes,
        edges,
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        selected=set(),
        mode="materialized",
    )

    assert {node.kind for node in nodes} == {"commit", "github_ref"}


def test_build_base_evidence_graph_excludes_analysis_nodes(monkeypatch):
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )
    # Even if latest_artifacts returns something, base should ignore it.
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts",
        lambda **kwargs: (
            SimpleNamespace(
                name="active_work_packages.json",
                projects=("sinity-lynchpin",),
                modified_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
                generated_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
                size_bytes=100,
                top_level_keys=(),
                brief="",
                references=(),
                status="available",
                reason=None,
            ),
        ),
    )

    base = build_base_evidence_graph(start=date(2026, 5, 5), end=date(2026, 5, 5))
    kinds = {n.kind for n in base.nodes}
    assert "analysis_artifact" not in kinds
    assert "analysis_claim" not in kinds


def test_materialized_graph_runs_product_readiness_without_frontier(monkeypatch):
    calls = {}
    source_calls = []

    def empty_source(*args, **kwargs):
        source_calls.append(kwargs)
        return ()

    def empty_adder(*args, **kwargs):
        source_calls.append(kwargs)

    def fake_source_readiness(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(caveats=())

    monkeypatch.setattr("lynchpin.graph.evidence_polylogue.work_events", empty_source)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", empty_source
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.analysis_claims", empty_source
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals", empty_adder
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness", empty_adder
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness", fake_source_readiness
    )

    graph = build_evidence_graph(start=date(2026, 5, 5), end=date(2026, 5, 5))
    base = build_base_evidence_graph(start=date(2026, 5, 5), end=date(2026, 5, 5))

    assert graph.mode == "materialized"
    assert base.mode == "materialized"
    assert source_calls
    assert calls == {
        "start": date(2026, 5, 5),
        "end": date(2026, 5, 5),
        "include_polylogue_product_counts": True,
        "include_github_frontier": False,
        "include_analysis_inventory": True,
        "repair_materializations": False,
    }


def test_spotify_evidence_converges_daily_product(monkeypatch):
    from lynchpin.graph import evidence_web_media

    ensure_calls = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window, budget="inline": ensure_calls.append(
            (name, window, budget)
        ),
    )
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_spotify_daily_signals",
        lambda *, start, end, ensure=True: (
            SimpleNamespace(
                date=date(2026, 1, 1),
                track_count=12,
                minutes_played=42.5,
                unique_artists=3,
                unique_tracks=11,
                top_artists=("A", "B", "C"),
                top_tracks=("T1", "T2"),
            ),
        ),
    )

    nodes = []
    evidence_web_media.add_spotify(
        nodes, start=date(2026, 1, 1), end=date(2026, 1, 2), selected=set()
    )

    assert ensure_calls == [
        ("spotify_daily", (date(2026, 1, 1), date(2026, 1, 3)), "manual")
    ]
    assert len(nodes) == 1
    assert nodes[0].id == "spotify:listening:2026-01-01"
    assert nodes[0].kind == "listening_session"
    assert nodes[0].summary == "12 tracks, 42min - top: A, B, C"
    assert nodes[0].payload == {
        "track_count": 12,
        "minutes": 42.5,
        "unique_artists": 3,
        "unique_tracks": 11,
        "top_artists": ["A", "B", "C"],
        "top_tracks": ["T1", "T2"],
    }


def test_spotify_evidence_uses_product_reader_exclusive_end(monkeypatch):
    from lynchpin.graph import evidence_web_media

    reader_calls = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_spotify_daily_signals",
        lambda *, start, end, ensure=True: reader_calls.append((start, end, ensure))
        or (),
    )

    nodes = []
    evidence_web_media.add_spotify(
        nodes, start=date(2026, 1, 1), end=date(2026, 1, 2), selected=set()
    )

    assert nodes == []
    assert reader_calls == [(date(2026, 1, 1), date(2026, 1, 3), False)]


def _patch_polylogue_work_events(
    monkeypatch,
    events,
    *,
    sessions=(),
):
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: sessions,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events",
        lambda **kwargs: events,
    )


def _patch_evidence_graph_minimal(monkeypatch):
    """Stub all sources except polylogue/git/terminal so a focused test only
    exercises the new substrate. Callers should still set the polylogue and
    optionally git/terminal mocks they need."""
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.focus_timeline", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )
    monkeypatch.setattr(
        "lynchpin.graph.terminal_patterns.detect_patterns", lambda **kwargs: ()
    )


def test_polylogue_work_events_appear_in_local_heavy_and_skip_local_fast(
    monkeypatch,
):
    when = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    events = (
        SimpleNamespace(
            event_id="we-high",
            conversation_id="c1",
            provider="claude-code",
            kind="implementation",
            confidence=0.9,
            start=when,
            end=when.replace(hour=14, minute=30),
            duration_ms=30 * 60_000,
            file_paths=("/realm/project/sinity-lynchpin/lynchpin/foo.py",),
            tools_used=("Edit",),
            summary="Implementing foo",
            workflow_shape="agentic_loop",
            workflow_shape_confidence=0.86,
            terminal_state="tool_left",
            terminal_state_confidence=0.72,
        ),
        SimpleNamespace(
            event_id="we-low",
            conversation_id="c1",
            provider="claude-code",
            kind="research",
            confidence=0.4,
            start=when.replace(hour=15),
            end=when.replace(hour=15, minute=10),
            duration_ms=10 * 60_000,
            file_paths=("/realm/project/sinity-lynchpin/docs/notes.md",),
            tools_used=("Read",),
            summary="Reading notes",
        ),
    )
    _patch_evidence_graph_minimal(monkeypatch)
    _patch_polylogue_work_events(monkeypatch, events)

    graph = build_evidence_graph(start=date(2026, 5, 7), end=date(2026, 5, 7))
    we_kinds = sorted(
        n.payload["event_id"] for n in graph.nodes if n.kind == "ai_work_event"
    )
    assert we_kinds == ["we-high", "we-low"]

    high = next(
        n
        for n in graph.nodes
        if n.kind == "ai_work_event" and n.payload["event_id"] == "we-high"
    )
    assert high.project == "sinity-lynchpin"
    assert high.payload["kind"] == "implementation"
    # Arc K overlay: file_paths (.py), Edit tool, 30min duration all support
    # implementation, so Polylogue + overlay AGREE — kind_source becomes
    # "agreement" and tier is high (combined confidence ≥ 0.8 with ≥2 features).
    assert high.payload["kind_source"] == "agreement"
    assert high.payload["kind_tier"] == "high"
    assert high.payload["source_kind"] == "implementation"
    assert high.payload["overlay_kind"] == "implementation"
    assert high.payload["workflow_shape"] == "agentic_loop"
    assert high.payload["workflow_shape_confidence"] == 0.86
    assert high.payload["terminal_state"] == "tool_left"
    assert high.payload["terminal_state_confidence"] == 0.72
    assert any(c.message.startswith("Work-event boundaries") for c in high.caveats)


def test_polylogue_work_events_reuse_session_project_before_path_resolution(
    monkeypatch,
) -> None:
    from lynchpin.graph import evidence_polylogue

    when = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    session = SimpleNamespace(
        conversation_id="c1",
        work_event_projects=("sinity-lynchpin",),
        title="Lynchpin work",
    )
    event = SimpleNamespace(
        event_id="we-session-project",
        conversation_id="c1",
        provider="codex",
        kind="implementation",
        confidence=0.8,
        start=when,
        end=when,
        duration_ms=1_000,
        file_paths=("/tmp/generated/path/outside/registry.py",),
        tools_used=("Edit",),
        summary="implementation",
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (session,),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events",
        lambda **kwargs: (event,),
    )
    monkeypatch.setattr(
        "lynchpin.core.classify.resolve_project",
        lambda path: (_ for _ in ()).throw(AssertionError("path resolver called")),
    )

    nodes = []
    evidence_polylogue.add_polylogue_work_events(
        nodes,
        start=date(2026, 5, 7),
        end=date(2026, 5, 7),
        selected=set(),
    )

    assert [node.project for node in nodes] == ["sinity-lynchpin"]


def test_build_context_caches_base_graph(monkeypatch):
    calls = []

    def fake_base(*, start, end, projects=None, include_github_frontier=False):
        calls.append((start, end))
        return EvidenceGraph(
            start=start,
            end=end,
            generated_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
            mode="network" if include_github_frontier else "materialized",
            nodes=(),
            edges=(),
            caveats=(),
        )

    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.build_base_evidence_graph", fake_base
    )

    ctx = EvidenceGraphBuildContext()
    g1 = ctx.base_graph(start=date(2026, 5, 1), end=date(2026, 5, 7))
    g2 = ctx.base_graph(start=date(2026, 5, 1), end=date(2026, 5, 7))
    assert g1 is g2
    assert len(calls) == 1
