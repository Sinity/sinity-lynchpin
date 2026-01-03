"""Knowledge graph snapshot builder for Markdown sources."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import duckdb
import pandas as pd
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


def nodes_to_df(nodes: List[Node]) -> pd.DataFrame:
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


def edges_to_df(edges: List[Edge]) -> pd.DataFrame:
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


def write_duckdb(db_path: Path, nodes_df: pd.DataFrame, edges_df: pd.DataFrame) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(db_path.as_posix()) as con:
        con.execute("CREATE OR REPLACE TABLE nodes AS SELECT * FROM nodes_df")
        con.execute("CREATE OR REPLACE TABLE edges AS SELECT * FROM edges_df")


def write_parquet(parquet_dir: Path, nodes_df: pd.DataFrame, edges_df: pd.DataFrame) -> None:
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


if __name__ == "__main__":
    app()
