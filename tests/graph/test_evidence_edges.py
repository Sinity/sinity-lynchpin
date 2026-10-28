from datetime import date, datetime, timezone


from lynchpin.core.evidence_graph import EvidenceNode
from lynchpin.graph.evidence_edges import (
    mentions_project_edges,
    same_project_day_edges,
    cross_boundary_edges,
    temporal_overlap_edges,
)


def _reload_config(monkeypatch) -> None:
    import lynchpin.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "_CONFIG", None, raising=False)


def test_mentions_project_edges_connects_raw_log_to_commits() -> None:
    """Verify project mentions link raw-log nodes to commits on the same day."""
    day = date(2026, 5, 24)

    # Raw-log entry mentioning "sinex"
    seed = EvidenceNode(
        id="raw-log:entry1",
        kind="raw_log",
        source="raw_log",
        date=day,
        project=None,  # Unattributed
        summary="Working on sinex target vision",
    )

    # A sinex commit on same day
    commit = EvidenceNode(
        id="git:sinex:abc123",
        kind="commit",
        source="git",
        date=day,
        project="sinex",
        summary="feat: implement parser",
    )

    edges = mentions_project_edges([seed, commit])

    # Should emit one edge from the raw-log entry to the sinex commit.
    assert len(edges) == 1
    edge = edges[0]
    assert edge.source_id == "raw-log:entry1"
    assert edge.target_id == "git:sinex:abc123"
    assert edge.relation == "mentions_project"
    assert "sinex" in edge.evidence.lower()
    assert edge.weight == 0.6


def test_mentions_project_edges_skips_no_matches() -> None:
    """Verify no edges are emitted when raw-log text mentions no project."""
    day = date(2026, 5, 24)

    seed = EvidenceNode(
        id="raw-log:entry2",
        kind="raw_log",
        source="raw_log",
        date=day,
        project=None,
        summary="Thinking about web development",
    )

    commit = EvidenceNode(
        id="git:sinex:abc123",
        kind="commit",
        source="git",
        date=day,
        project="sinex",
        summary="feat: implement parser",
    )

    edges = mentions_project_edges([seed, commit])

    # No edge should be emitted (no project mention)
    assert len(edges) == 0


def test_same_project_day_edges_emits_only_cross_source_pairs() -> None:
    """Verify that same_project_day edges are emitted only for distinct source pairs.

    A fixture with 10 git commits + 5 github refs + 2 raw-log entries on one
    (project, date) should produce at most three cross-source edges, not one
    edge per node.
    """
    day = date(2026, 5, 24)
    project = "test-project"

    def node(node_id: str, source: str) -> EvidenceNode:
        return EvidenceNode(
            id=node_id,
            kind="commit" if source == "git" else "github_ref" if source == "github" else "raw_log",
            source=source,
            date=day,
            project=project,
            summary=node_id,
        )

    nodes = (
        [node(f"git-{i}", "git") for i in range(10)]
        + [node(f"github-{i}", "github") for i in range(5)]
        + [node(f"raw-log-{i}", "raw_log") for i in range(2)]
    )

    edges = same_project_day_edges(nodes)

    # There are only three source pairs: git/github, git/raw-log, github/raw-log.
    assert len(edges) <= 3, f"expected ≤3 edges, got {len(edges)}"

    # Verify all edges are cross-source
    edge_pairs = {(e.source_id.split('-')[0], e.target_id.split('-')[0]) for e in edges}
    for source_a, source_b in edge_pairs:
        assert source_a != source_b, f"same-source edge found: {source_a} ↔ {source_b}"


def test_cross_boundary_edges_preserves_boundary_edges_without_tail_tail_work(monkeypatch) -> None:
    from lynchpin.graph import evidence_edges
    from lynchpin.core.evidence_graph import EvidenceEdge

    day = date(2026, 5, 24)
    boundary = EvidenceNode(id="boundary", kind="commit", source="git", date=day, project="p", summary="boundary")
    tail_a = EvidenceNode(id="tail-a", kind="github_ref", source="github", date=day, project="p", summary="tail a")
    tail_b = EvidenceNode(id="tail-b", kind="raw_log", source="raw_log", date=day, project="p", summary="tail b")
    calls: list[tuple[str, int]] = []

    def same(nodes):
        calls.append(("same_project_day", len(nodes)))
        return (
            EvidenceEdge("boundary", "tail-a", "same_project_day", "boundary", 0.4),
            EvidenceEdge("boundary", "tail-b", "same_project_day", "boundary", 0.4),
            EvidenceEdge("tail-a", "tail-b", "same_project_day", "tail only", 0.4),
        )

    monkeypatch.setattr(evidence_edges, "same_project_day_edges", same)
    for name in (
        "temporal_overlap_edges",
        "temporal_proximity_edges",
        "mentions_project_edges",
    ):
        monkeypatch.setattr(evidence_edges, name, lambda _nodes: ())

    edges = cross_boundary_edges((boundary,), (tail_a, tail_b))

    assert {(edge.source_id, edge.target_id) for edge in edges} == {
        ("boundary", "tail-a"),
        ("boundary", "tail-b"),
    }
    assert calls == [("same_project_day", 3)]


def test_temporal_overlap_edges_group_by_project_and_stop_at_interval_end() -> None:
    day = date(2026, 5, 24)

    def node(
        node_id: str,
        source: str,
        project: str,
        start_minute: int,
        end_minute: int,
    ) -> EvidenceNode:
        return EvidenceNode(
            id=node_id,
            kind={
                "git": "commit",
                "activitywatch": "focus_span",
                "terminal": "terminal_session",
            }[source],
            source=source,
            date=day,
            project=project,
            summary=node_id,
            start=datetime(2026, 5, 24, 10, start_minute, tzinfo=timezone.utc),
            end=datetime(2026, 5, 24, 10, end_minute, tzinfo=timezone.utc),
        )

    edges = temporal_overlap_edges(
        [
            node("git-a", "git", "lynchpin", 0, 20),
            node("focus-a", "activitywatch", "lynchpin", 10, 25),
            node("term-a", "terminal", "lynchpin", 25, 30),
            node("git-b", "git", "polylogue", 10, 15),
            node("focus-b", "activitywatch", "polylogue", 16, 20),
        ]
    )

    assert [(edge.source_id, edge.target_id, edge.relation) for edge in edges] == [
        ("git-a", "focus-a", "temporal_overlap")
    ]


def test_temporal_overlap_admits_point_event_inside_interval() -> None:
    """Commits have start == end (point events). A point inside an interval
    is a legitimate overlap — previously dropped by the `end > start` filter,
    making every commit invisible to this layer."""
    day = date(2026, 5, 24)
    commit_at = datetime(2026, 5, 24, 10, 15, tzinfo=timezone.utc)
    session_start = datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc)
    session_end = datetime(2026, 5, 24, 10, 30, tzinfo=timezone.utc)

    nodes = [
        EvidenceNode(
            id="commit-x", kind="commit", source="git", date=day,
            project="lynchpin", summary="x",
            start=commit_at, end=commit_at,  # point event
        ),
        EvidenceNode(
            id="term-x", kind="terminal_session", source="terminal", date=day,
            project="lynchpin", summary="x",
            start=session_start, end=session_end,
        ),
    ]
    edges = temporal_overlap_edges(nodes)
    rels = {(e.source_id, e.target_id, e.relation) for e in edges}
    # Either direction is fine — the edge proves the commit happened inside
    # the terminal session.
    assert ("commit-x", "term-x", "temporal_overlap") in rels or \
           ("term-x", "commit-x", "temporal_overlap") in rels


def test_temporal_overlap_excludes_point_outside_interval() -> None:
    """Negative case: commit BEFORE a terminal session starts must NOT overlap."""
    day = date(2026, 5, 24)
    commit_at = datetime(2026, 5, 24, 9, 50, tzinfo=timezone.utc)  # before
    session_start = datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc)
    session_end = datetime(2026, 5, 24, 10, 30, tzinfo=timezone.utc)

    nodes = [
        EvidenceNode(
            id="commit-y", kind="commit", source="git", date=day,
            project="lynchpin", summary="y",
            start=commit_at, end=commit_at,
        ),
        EvidenceNode(
            id="term-y", kind="terminal_session", source="terminal", date=day,
            project="lynchpin", summary="y",
            start=session_start, end=session_end,
        ),
    ]
    edges = temporal_overlap_edges(nodes)
    assert edges == ()
