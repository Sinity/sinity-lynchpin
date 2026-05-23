"""Freeze a deterministic fixture output for work_correlation.

Build a synthetic but realistic mix of git/github/ai/raw_log/focus/shell evidence
across multiple projects and dates, plus an EvidenceGraph for the graph-driven
code paths, then materialize every public output via JSON. Use this to diff
before/after each polars migration step. Output is written to stdout.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from lynchpin.core.evidence_graph import (
    EvidenceEdge,
    EvidenceGraph,
    EvidenceNode,
)
from lynchpin.graph.work_correlation import (
    correlate_work_days,
    dataset_correlations,
    render_dataset_correlations,
    render_supported_work_claims,
    render_work_correlation_summary,
    render_work_day_correlations,
    strongest_work_correlations,
    summarize_work_correlations,
    supported_work_claims,
    work_day_correlations,
)
from lynchpin.sources.github import GitHubActor, GitHubItem


UTC = timezone.utc


def _dt(day_offset: int, hour: int = 12) -> datetime:
    return datetime(2026, 5, 1, hour, tzinfo=UTC) + timedelta(days=day_offset)


def _make_inputs():
    days = [_dt(i) for i in range(5)]
    projects = ["sinity-lynchpin", "polylogue", "sinex"]

    git_facts = []
    # 8 commits across (project, day)
    spec = [
        (0, "sinity-lynchpin", "a1", "feat: correlation closes #17"),
        (0, "sinity-lynchpin", "a2", "fix: subjects (#18)"),
        (1, "sinity-lynchpin", "b1", "chore: cleanup"),
        (1, "polylogue", "c1", "feat: archive scan"),
        (2, "polylogue", "c2", "fix: retry"),
        (2, "sinex", "d1", "feat: ingest"),
        (3, "sinex", "d2", "perf: lock contention"),
        (4, "sinity-lynchpin", "e1", "fix: regression"),
    ]
    for off, proj, sha, subj in spec:
        git_facts.append(SimpleNamespace(
            repo=proj, commit=sha, authored_at=_dt(off), subject=subj
        ))

    github_items = [
        GitHubItem(
            repo="sinity-lynchpin",
            slug="Sinity/sinity-lynchpin",
            kind="issue",
            number=17,
            title="Fix correlation",
            state="closed",
            url=None,
            author=GitHubActor("Sinity"),
            labels=(),
            body="",
            comments=(),
            created_at=days[0],
            updated_at=days[0],
            closed_at=days[0],
        ),
        GitHubItem(
            repo="sinity-lynchpin",
            slug="Sinity/sinity-lynchpin",
            kind="pr",
            number=18,
            title="Subjects PR",
            state="open",
            url=None,
            author=GitHubActor("Sinity"),
            labels=(),
            body="",
            comments=(),
            created_at=days[0],
            updated_at=days[0],
            closed_at=None,
        ),
    ]

    ai_sessions = [
        SimpleNamespace(
            conversation_id=f"conv-{i}",
            first_message_at=_dt(i % 5, hour=10),
            work_event_projects=(projects[i % 3],),
            title="",
        )
        for i in range(6)
    ]

    raw_log_entries = [
        SimpleNamespace(
            timestamp=_dt(i % 5, hour=15),
            text=f"work on {projects[i % 3]} today",
            source_path=f"/realm/data/knowledgebase/log{i}.md",
            line_no=i + 1,
        )
        for i in range(5)
    ]

    focus_spans = [
        SimpleNamespace(
            project=projects[i % 3],
            start=_dt(i % 5, hour=9),
            duration_s=1800 + 600 * i,
        )
        for i in range(7)
    ]

    shell_sessions = [
        SimpleNamespace(
            project=projects[i % 3],
            start=_dt(i % 5, hour=14),
            duration_s=300 + 60 * i,
            command_count=3 + i,
        )
        for i in range(6)
    ]

    return dict(
        git_facts=git_facts,
        github_items=github_items,
        ai_sessions=ai_sessions,
        raw_log_entries=raw_log_entries,
        focus_spans=focus_spans,
        shell_sessions=shell_sessions,
    )


def _make_graph():
    nodes: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    projects = ["sinity-lynchpin", "polylogue", "sinex"]
    for i in range(5):
        day = date(2026, 5, 1) + timedelta(days=i)
        proj = projects[i % 3]
        nodes.append(EvidenceNode(
            id=f"commit:{proj}:{i}",
            kind="commit",
            source="git",
            date=day,
            project=proj,
            summary=f"commit {i}",
            payload={"commit": f"sha{i}", "github_refs": {"prs": [i + 100], "issues": []}},
        ))
        nodes.append(EvidenceNode(
            id=f"ai-ev:{proj}:{i}",
            kind="ai_work_event",
            source="polylogue",
            date=day,
            project=proj,
            summary="ai event",
            payload={
                "conversation_id": f"conv-{i}",
                "kind": "implementation" if i % 2 == 0 else "discussion",
                "kind_tier": "high" if i % 2 == 0 else "medium",
            },
        ))
        nodes.append(EvidenceNode(
            id=f"focus:{proj}:{i}",
            kind="focus_day",
            source="activitywatch",
            date=day,
            project=proj,
            summary="focus",
            payload={"duration_s": 3600 + i * 600},
        ))
        nodes.append(EvidenceNode(
            id=f"term:{proj}:{i}",
            kind="terminal_session",
            source="terminal",
            date=day,
            project=proj,
            summary="term",
            payload={"duration_s": 600 + i * 60, "command_count": 5 + i},
        ))
        nodes.append(EvidenceNode(
            id=f"ghref:{proj}:{i}",
            kind="github_pr",
            source="github",
            date=day,
            project=proj,
            summary="pr",
            payload={"kind": "pr", "number": i + 200, "lifecycle": "executed"},
        ))
        nodes.append(EvidenceNode(
            id=f"rawlog:{proj}:{i}",
            kind="raw_log",
            source="raw_log",
            date=day,
            project=proj,
            summary="rl",
            payload={"source_path": f"/log/{i}.md", "line_no": str(i)},
        ))
        # add some edges (relations) for dataset_correlations
        edges.append(EvidenceEdge(
            source_id=f"commit:{proj}:{i}",
            target_id=f"ai-ev:{proj}:{i}",
            relation="references",
            evidence=f"commit-ai overlap {i}",
            weight=1.0 + i * 0.3,
        ))
        edges.append(EvidenceEdge(
            source_id=f"focus:{proj}:{i}",
            target_id=f"term:{proj}:{i}",
            relation="temporal_overlap",
            evidence=f"focus-term {i}",
            weight=0.5 + i * 0.2,
        ))
        edges.append(EvidenceEdge(
            source_id=f"ghref:{proj}:{i}",
            target_id=f"commit:{proj}:{i}",
            relation="same_project_day",
            evidence=f"github-commit {i}",
            weight=0.7,
        ))
    return EvidenceGraph(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        generated_at=datetime(2026, 5, 6, tzinfo=UTC),
        mode="local-fast",
        nodes=tuple(nodes),
        edges=tuple(edges),
        caveats=(),
    )


def _row_to_dict(row):
    d = asdict(row)
    # asdict on frozen dataclass works; date/tuple stay native
    return d


def _convert(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_convert(v) for v in value]
    if isinstance(value, list):
        return [_convert(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _convert(v) for k, v in value.items()}
    if isinstance(value, set):
        return sorted(_convert(v) for v in value)
    return value


def build_payload() -> dict[str, object]:
    inputs = _make_inputs()
    rows = correlate_work_days(**inputs)
    summary = summarize_work_correlations(rows)
    strong = strongest_work_correlations(rows, limit=5)

    graph = _make_graph()
    graph_rows = work_day_correlations(
        start=graph.start, end=graph.end, graph=graph,
    )
    claims = supported_work_claims(graph_rows, graph=graph, limit=8)
    ds_corr = dataset_correlations(graph, limit=8, include_analysis=False)

    return {
        "correlate_rows": [_convert(_row_to_dict(r)) for r in rows],
        "summary": _convert(_row_to_dict(summary)),
        "strongest": [_convert(_row_to_dict(r)) for r in strong],
        "graph_rows": [_convert(_row_to_dict(r)) for r in graph_rows],
        "claims": [_convert(_row_to_dict(c)) for c in claims],
        "dataset_correlations": [_convert(_row_to_dict(d)) for d in ds_corr],
        "render_work_day": render_work_day_correlations(rows),
        "render_summary": render_work_correlation_summary(summary),
        "render_claims": render_supported_work_claims(claims),
        "render_dataset_correlations": render_dataset_correlations(ds_corr),
    }


def main() -> None:
    print(json.dumps(build_payload(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
