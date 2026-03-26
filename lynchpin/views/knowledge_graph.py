"""Knowledge graph snapshot builder for Markdown sources."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import typer

from ..core.io import write_text_if_changed

DEFAULT_SOURCES = [
    Path("/realm/project/knowledgebase"),
    Path("docs"),
]

app = typer.Typer(help="Build Markdown knowledge graph snapshots", pretty_exceptions_show_locals=False)


@dataclasses.dataclass(slots=True)
class Node:
    node_id: str
    kind: str
    title: str
    content: str
    source_path: str
    parent_id: Optional[str]
    metadata: Dict[str, object]


@dataclasses.dataclass(slots=True)
class Edge:
    edge_id: str
    edge_type: str
    source_id: str
    target_id: str
    metadata: Dict[str, object]


HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$")
TASK_RE = re.compile(r"^- \[(?P<status>[ xX])\]\s+(?P<body>.+)$")
LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<target>[^)]+)\)")


def _normalise_path(path: Path) -> str:
    return path.as_posix()


def _digest(prefix: str, *parts: str) -> str:
    payload = "::".join(parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha1(payload).hexdigest()}"


def iter_markdown_files(sources: Iterable[Path]) -> Iterator[Path]:
    for source in sources:
        if source.is_file() and source.suffix.lower() in {".md", ".markdown"}:
            yield source
            continue
        if not source.is_dir():
            continue
        for suffix in ("*.md", "*.markdown"):
            for candidate in sorted(source.rglob(suffix)):
                if "/.git/" in candidate.as_posix():
                    continue
                yield candidate


def parse_markdown(path: Path) -> Tuple[List[Node], List[Edge]]:
    text = path.read_text(encoding="utf-8")
    rel_path = _normalise_path(path)
    lines = text.splitlines()
    document_title = path.stem.replace("_", " ")
    if lines:
        first = HEADING_RE.match(lines[0])
        if first:
            document_title = first.group("title").strip()

    doc_id = _digest("doc", rel_path)
    nodes: List[Node] = [
        Node(
            node_id=doc_id,
            kind="document",
            title=document_title,
            content=text,
            source_path=rel_path,
            parent_id=None,
            metadata={"size_bytes": len(text.encode("utf-8"))},
        )
    ]
    edges: List[Edge] = []
    stack: List[Tuple[int, str]] = [(0, doc_id)]
    current_section: Optional[Node] = None
    buffer: List[str] = []

    def flush_section() -> None:
        nonlocal current_section, buffer
        if current_section is None:
            return
        current_section.content = "\n".join(buffer).strip()
        nodes.append(current_section)
        buffer = []
        current_section = None

    for line in lines:
        heading = HEADING_RE.match(line)
        if heading:
            flush_section()
            level = len(heading.group("level"))
            title = heading.group("title").strip()
            section_id = _digest("sec", rel_path, title, str(len(nodes)))
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent_id = stack[-1][1] if stack else doc_id
            stack.append((level, section_id))

            current_section = Node(
                node_id=section_id,
                kind="section",
                title=title,
                content="",
                source_path=rel_path,
                parent_id=parent_id,
                metadata={"level": level},
            )
            edges.append(
                Edge(
                    edge_id=_digest("edge", section_id, parent_id, str(len(edges))),
                    edge_type="contains",
                    source_id=parent_id,
                    target_id=section_id,
                    metadata={},
                )
            )
            continue

        task = TASK_RE.match(line.strip())
        if task:
            flush_section()
            status = "done" if task.group("status").lower() == "x" else "todo"
            body = task.group("body").strip()
            parent_id = stack[-1][1] if stack else doc_id
            task_id = _digest("task", rel_path, body, str(len(nodes)))
            nodes.append(
                Node(
                    node_id=task_id,
                    kind="task",
                    title=body,
                    content=line.strip(),
                    source_path=rel_path,
                    parent_id=parent_id,
                    metadata={"status": status},
                )
            )
            edges.append(
                Edge(
                    edge_id=_digest("edge", task_id, parent_id, str(len(edges))),
                    edge_type="contains",
                    source_id=parent_id,
                    target_id=task_id,
                    metadata={},
                )
            )
            continue

        if current_section is None:
            nodes[0].metadata.setdefault("preamble", []).append(line)
        else:
            buffer.append(line)

    flush_section()

    for node in nodes:
        content = node.content or ""
        for match in LINK_RE.finditer(content):
            target = match.group("target")
            label = match.group("text")
            edges.append(
                Edge(
                    edge_id=_digest("link", node.node_id, target, label),
                    edge_type="link",
                    source_id=node.node_id,
                    target_id=target,
                    metadata={"label": label},
                )
            )

    return nodes, edges


def nodes_to_df(nodes: List[Node]) -> Any:
    import pandas as pd
    return pd.DataFrame(
        [
            {
                "node_id": node.node_id,
                "kind": node.kind,
                "title": node.title,
                "content": node.content,
                "source_path": node.source_path,
                "parent_id": node.parent_id,
                "metadata": json.dumps(node.metadata, ensure_ascii=False),
            }
            for node in nodes
        ]
    )


def edges_to_df(edges: List[Edge]) -> Any:
    import pandas as pd
    return pd.DataFrame(
        [
            {
                "edge_id": edge.edge_id,
                "edge_type": edge.edge_type,
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "metadata": json.dumps(edge.metadata, ensure_ascii=False),
            }
            for edge in edges
        ]
    )


def write_duckdb(db_path: Path, nodes_df: Any, edges_df: Any) -> None:
    import duckdb
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(db_path.as_posix()) as con:
        con.execute("CREATE OR REPLACE TABLE nodes AS SELECT * FROM nodes_df")
        con.execute("CREATE OR REPLACE TABLE edges AS SELECT * FROM edges_df")


def write_parquet(parquet_dir: Path, nodes_df: Any, edges_df: Any) -> None:
    parquet_dir.mkdir(parents=True, exist_ok=True)
    nodes_df.to_parquet(parquet_dir / "nodes.parquet", index=False)
    edges_df.to_parquet(parquet_dir / "edges.parquet", index=False)


def write_manifest(manifest: Path, sources: List[Path], db_path: Path, parquet_dir: Optional[Path]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [_normalise_path(src.resolve()) for src in sources],
        "duckdb": _normalise_path(db_path.resolve()),
        "parquet": _normalise_path(parquet_dir.resolve()) if parquet_dir else None,
        "tool": "lynchpin.views.knowledge_graph",
    }
    write_text_if_changed(manifest, json.dumps(payload, indent=2))


@app.command()
def build(
    sources: List[Path] = typer.Argument(None, help="Markdown sources to scan (defaults to knowledgebase + docs)"),
    output: Path = typer.Option(Path("artefacts/knowledge/graph/knowledge_graph.duckdb"), "--output", help="DuckDB output path"),
    manifest: Path = typer.Option(Path("artefacts/knowledge/graph/manifest.json"), "--manifest", help="Manifest JSON path"),
    parquet_dir: Optional[Path] = typer.Option(None, "--parquet-dir", help="Optional directory for Parquet exports"),
) -> None:
    resolved_sources = sources or DEFAULT_SOURCES
    markdown_files = list(iter_markdown_files(resolved_sources))
    if not markdown_files:
        typer.echo("No Markdown files found in the provided sources.", err=True)
        raise typer.Exit(1)

    all_nodes: List[Node] = []
    all_edges: List[Edge] = []
    for path in markdown_files:
        nodes, edges = parse_markdown(path)
        all_nodes.extend(nodes)
        all_edges.extend(edges)

    nodes_df = nodes_to_df(all_nodes)
    edges_df = edges_to_df(all_edges)

    write_duckdb(output, nodes_df, edges_df)
    if parquet_dir:
        write_parquet(parquet_dir, nodes_df, edges_df)
    write_manifest(manifest, resolved_sources, output, parquet_dir)

    typer.echo(f"Processed {len(markdown_files)} Markdown files → {len(nodes_df)} nodes, {len(edges_df)} edges")
    typer.echo(f"DuckDB written to {output}")


def _month_to_date_str(month_key: str) -> Optional[str]:
    """Convert 'YYYY-MM' to ISO date string of the first day of that month."""
    try:
        year, month = month_key.split("-")
        from datetime import date as _date
        return _date(int(year), int(month), 1).isoformat()
    except (ValueError, AttributeError):
        return None


def _date_ranges_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start <= b_end and b_start <= a_end


def build_episode_nodes_temporal(days: Optional[int] = None) -> List[Dict]:
    """Export derived episode rollups as temporally-grounded KG nodes."""
    from ..trajectory.day import summarize_days
    from ..context.patterns import detect_episodes

    day_list = summarize_days(days=days if days is not None else 90)
    episodes = detect_episodes(day_list)
    now = datetime.now(timezone.utc).isoformat()

    nodes = []
    for ep in episodes:
        nodes.append({
            "id": f"episode:{ep.episode_id}",
            "kind": "episode",
            "label": ep.label,
            "valid_from": ep.start_date.isoformat(),
            "valid_until": ep.end_date.isoformat(),
            "confidence": ep.confidence,
            "dominant_project": ep.dominant_project,
            "dominant_topic": ep.dominant_topic,
            "source_timestamp": now,
            "evidence_count": ep.day_count_with_dominant,
            "properties": {
                "dominant_mode": ep.dominant_mode,
                "trigger": ep.trigger,
                "day_count": (ep.end_date - ep.start_date).days + 1,
            },
        })
    return nodes


def build_theme_nodes_temporal(days: Optional[int] = None) -> List[Dict]:
    """Export Theme objects as KG nodes with temporal validity spans."""
    from ..trajectory.day import summarize_days
    from ..trajectory.month import summarize_months as _summarize_months
    from ..trajectory.week import summarize_weeks
    from ..context.themes import detect_themes

    day_list = summarize_days(days=days if days is not None else 90)
    months = _summarize_months(day_list)
    weeks = summarize_weeks(day_list)
    themes = detect_themes(months, weeks)
    now = datetime.now(timezone.utc).isoformat()

    nodes = []
    for theme in themes:
        nodes.append({
            "id": f"theme:{theme.kind}:{theme.name}",
            "kind": "theme",
            "label": theme.name,
            "valid_from": _month_to_date_str(theme.first_seen),
            "valid_until": _month_to_date_str(theme.last_seen),
            "confidence": min(0.5 + theme.month_count * 0.1, 0.95),
            "dominant_project": theme.name if theme.kind == "project" else None,
            "dominant_topic": theme.name if theme.kind == "topic" else None,
            "source_timestamp": now,
            "evidence_count": theme.month_count,
            "properties": {
                "kind": theme.kind,
                "total_hours": theme.total_hours,
                "trend": theme.trend,
                "months_active": theme.month_count,
            },
        })
    return nodes


def build_temporal_edges(
    episode_nodes: List[Dict],
    theme_nodes: List[Dict],
) -> List[Dict]:
    """Build temporal KG edges: precedes, overlaps, contains."""
    edges = []
    sorted_eps = sorted(episode_nodes, key=lambda n: n.get("valid_from") or "")

    # precedes: consecutive episodes
    for i in range(len(sorted_eps) - 1):
        curr, nxt = sorted_eps[i], sorted_eps[i + 1]
        edges.append({
            "source": curr["id"],
            "target": nxt["id"],
            "kind": "precedes",
            "confidence": round((curr["confidence"] + nxt["confidence"]) / 2, 3),
            "evidence": f"{curr['valid_until']} → {nxt['valid_from']}",
        })

    # overlaps / contains: episodes × themes
    for ep in episode_nodes:
        ep_s, ep_e = ep.get("valid_from"), ep.get("valid_until")
        if not ep_s or not ep_e:
            continue
        for theme in theme_nodes:
            th_s, th_e = theme.get("valid_from"), theme.get("valid_until")
            if not th_s or not th_e:
                continue
            if not _date_ranges_overlap(ep_s, ep_e, th_s, th_e):
                continue
            edge_kind = "contains" if ep_s <= th_s and ep_e >= th_e else "overlaps"
            edges.append({
                "source": ep["id"],
                "target": theme["id"],
                "kind": edge_kind,
                "confidence": round(ep["confidence"] * theme["confidence"], 3),
                "evidence": f"ep {ep_s}–{ep_e} {edge_kind} theme {th_s}–{th_e}",
            })

    return edges


@app.command("build-temporal")
def build_temporal(
    days: Optional[int] = typer.Option(None, "--days", help="Lookback days (default: all)"),
    output_dir: Path = typer.Option(
        Path("artefacts/knowledge/graph"),
        "--output-dir",
        help="Directory for temporal-nodes.json and temporal-edges.json",
    ),
) -> None:
    """Export derived episode and theme rollups as temporally-grounded KG nodes."""
    episode_nodes = build_episode_nodes_temporal(days=days)
    theme_nodes = build_theme_nodes_temporal(days=days)
    all_nodes = episode_nodes + theme_nodes
    edges = build_temporal_edges(episode_nodes, theme_nodes)

    output_dir.mkdir(parents=True, exist_ok=True)
    nodes_path = output_dir / "temporal-nodes.json"
    edges_path = output_dir / "temporal-edges.json"

    nodes_path.write_text(
        json.dumps(all_nodes, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    edges_path.write_text(
        json.dumps(edges, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    typer.echo(
        f"Temporal KG: {len(all_nodes)} nodes "
        f"({len(episode_nodes)} episodes, {len(theme_nodes)} themes), "
        f"{len(edges)} edges"
    )
    typer.echo(f"  → {nodes_path}")
    typer.echo(f"  → {edges_path}")


if __name__ == "__main__":
    app()
