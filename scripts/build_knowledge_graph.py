#!/usr/bin/env python3
"""Build a lightweight knowledge graph snapshot from Markdown knowledgebases.

The script walks one or more source directories, parses Markdown files, and
materialises three artefacts:

* DuckDB database containing `nodes` and `edges` tables.
* Optional Parquet exports mirroring those tables for downstream tools.
* A JSONL manifest describing the snapshot metadata (sources, timestamp).

Nodes capture documents, sections (headings), and actionable list items. Edges
record containment relationships and explicit Markdown links.

The goal is to provide a reproducible scaffold for graph experiments while the
full Sinex knowledge graph is still under construction. The original
knowledgebase content is never modified; only derived artefacts are written.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import duckdb
import pandas as pd


# --------------------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------------------


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


# --------------------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------------------


HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$")
TASK_RE = re.compile(r"^- \[(?P<status>[ xX])\]\s+(?P<body>.+)$")
LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<target>[^)]+)\)")


def normalise_path(path: Path) -> str:
    """Return a POSIX-style relative path for metadata storage."""

    return path.as_posix()


def digest(*parts: str, prefix: str) -> str:
    """Generate a deterministic identifier using SHA1, namespaced by prefix."""

    payload = "::".join(parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha1(payload).hexdigest()}"


def iter_markdown_files(sources: Iterable[Path]) -> Iterator[Path]:
    for source in sources:
        if source.is_file() and source.suffix.lower() in {".md", ".markdown"}:
            yield source
        elif source.is_dir():
            for path in sorted(source.rglob("*.md")):
                if "/.git/" in path.as_posix():
                    continue
                yield path
            for path in sorted(source.rglob("*.markdown")):
                if "/.git/" in path.as_posix():
                    continue
                yield path


def parse_markdown(path: Path) -> Tuple[List[Node], List[Edge]]:
    """Parse a single Markdown file and produce nodes/edges."""

    text = path.read_text(encoding="utf-8")
    rel_path = normalise_path(path)

    document_title = path.stem.replace("_", " ")
    first_heading = HEADING_RE.match(text.splitlines()[0]) if text else None
    if first_heading:
        document_title = first_heading.group("title").strip()

    doc_id = digest(rel_path, prefix="doc")
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

    # Stack of (level, node_id) to maintain containment hierarchy
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

    for line in text.splitlines():
        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush_section()

            level = len(heading_match.group("level"))
            title = heading_match.group("title").strip()
            section_id = digest(rel_path, title, str(len(nodes)), prefix="sec")

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
                    edge_id=digest(section_id, parent_id, prefix="edge"),
                    edge_type="contains",
                    source_id=parent_id,
                    target_id=section_id,
                    metadata={"ordinal": len([e for e in edges if e.source_id == parent_id])},
                )
            )
            continue

        task_match = TASK_RE.match(line.strip())
        if task_match:
            flush_section()
            task_body = task_match.group("body").strip()
            status = "done" if task_match.group("status").lower() == "x" else "todo"
            parent_id = stack[-1][1] if stack else doc_id
            task_id = digest(rel_path, task_body, str(len(nodes)), prefix="task")
            nodes.append(
                Node(
                    node_id=task_id,
                    kind="task",
                    title=task_body,
                    content=line.strip(),
                    source_path=rel_path,
                    parent_id=parent_id,
                    metadata={"status": status},
                )
            )
            edges.append(
                Edge(
                    edge_id=digest(task_id, parent_id, prefix="edge"),
                    edge_type="contains",
                    source_id=parent_id,
                    target_id=task_id,
                    metadata={},
                )
            )
            continue

        if current_section is None:
            # Content before first heading is attached to the document node
            buffer_doc = nodes[0].metadata.setdefault("preamble", [])
            buffer_doc.append(line)
        else:
            buffer.append(line)

    flush_section()

    # Extract link edges for sections and tasks
    for node in nodes:
        for match in LINK_RE.finditer(node.content):
            target = match.group("target")
            label = match.group("text")
            edge_id = digest(node.node_id, target, label, prefix="link")
            edges.append(
                Edge(
                    edge_id=edge_id,
                    edge_type="link",
                    source_id=node.node_id,
                    target_id=target,
                    metadata={"label": label},
                )
            )

    return nodes, edges


# --------------------------------------------------------------------------------------
# Persistence helpers
# --------------------------------------------------------------------------------------


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def nodes_to_dataframe(nodes: List[Node]) -> pd.DataFrame:
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


def edges_to_dataframe(edges: List[Edge]) -> pd.DataFrame:
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
    ensure_parent(db_path)
    with duckdb.connect(db_path.as_posix()) as con:
        con.execute("CREATE OR REPLACE TABLE nodes AS SELECT * FROM nodes_df")
        con.execute("CREATE OR REPLACE TABLE edges AS SELECT * FROM edges_df")


def write_parquet(parquet_dir: Path, nodes_df: pd.DataFrame, edges_df: pd.DataFrame) -> None:
    ensure_parent(parquet_dir / "stub")
    nodes_df.to_parquet(parquet_dir / "nodes.parquet", index=False)
    edges_df.to_parquet(parquet_dir / "edges.parquet", index=False)


def write_manifest(manifest_path: Path, sources: List[Path], db_path: Path, parquet_dir: Optional[Path]) -> None:
    ensure_parent(manifest_path)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [normalise_path(path) for path in sources],
        "duckdb": normalise_path(db_path),
        "parquet": normalise_path(parquet_dir) if parquet_dir else None,
        "tool": "build_knowledge_graph.py",
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a DuckDB knowledge graph snapshot")
    parser.add_argument(
        "sources",
        nargs="*",
        type=Path,
        default=[
            Path("/realm/knowledgebase"),
            Path("docs/reference/kb"),
        ],
        help="Directories or files to include (default: common knowledgebase roots)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/derived/knowledge_graph/knowledge_graph.duckdb"),
        help="DuckDB output path",
    )
    parser.add_argument(
        "--parquet-dir",
        type=Path,
        default=None,
        help="Optional directory for Parquet exports",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/derived/knowledge_graph/manifest.json"),
        help="Manifest JSON path",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    sources = [path.resolve() for path in args.sources]

    markdown_files = list(iter_markdown_files(sources))
    if not markdown_files:
        print("No Markdown files found; aborting", file=sys.stderr)
        sys.exit(1)

    all_nodes: List[Node] = []
    all_edges: List[Edge] = []

    for path in markdown_files:
        nodes, edges = parse_markdown(path)
        all_nodes.extend(nodes)
        all_edges.extend(edges)

    nodes_df = nodes_to_dataframe(all_nodes)
    edges_df = edges_to_dataframe(all_edges)

    write_duckdb(args.output, nodes_df, edges_df)
    if args.parquet_dir:
        write_parquet(args.parquet_dir, nodes_df, edges_df)

    write_manifest(args.manifest, sources, args.output.resolve(), args.parquet_dir.resolve() if args.parquet_dir else None)

    print(f"Processed {len(markdown_files)} markdown files → {len(nodes_df)} nodes, {len(edges_df)} edges")
    print(f"DuckDB database written to {args.output}")


if __name__ == "__main__":
    main()

