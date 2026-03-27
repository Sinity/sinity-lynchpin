"""CLI for knowledge graph materialization."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from .knowledge_graph_markdown import build_markdown_snapshot
from .knowledge_graph_temporal import build_temporal_snapshot

app = typer.Typer(help="Build Markdown knowledge graph snapshots", pretty_exceptions_show_locals=False)


@app.command()
def build(
    sources: List[Path] = typer.Argument(None, help="Markdown sources to scan (defaults to knowledgebase + docs)"),
    output: Path = typer.Option(Path("artefacts/knowledge/graph/knowledge_graph.duckdb"), "--output", help="DuckDB output path"),
    manifest: Path = typer.Option(Path("artefacts/knowledge/graph/manifest.json"), "--manifest", help="Manifest JSON path"),
    parquet_dir: Optional[Path] = typer.Option(None, "--parquet-dir", help="Optional directory for Parquet exports"),
) -> None:
    try:
        summary = build_markdown_snapshot(
            sources=sources,
            output=output,
            manifest=manifest,
            parquet_dir=parquet_dir,
        )
    except FileNotFoundError:
        typer.echo("No Markdown files found in the provided sources.", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"Processed {summary['markdown_file_count']} Markdown files → "
        f"{summary['node_count']} nodes, {summary['edge_count']} edges"
    )
    typer.echo(f"DuckDB written to {output}")


@app.command("build-temporal")
def build_temporal(
    days: Optional[int] = typer.Option(None, "--days", help="Lookback days (default: all)"),
    output_dir: Path = typer.Option(
        Path("artefacts/knowledge/graph"),
        "--output-dir",
        help="Directory for temporal-nodes.json and temporal-edges.json",
    ),
) -> None:
    summary = build_temporal_snapshot(days=days, output_dir=output_dir)
    typer.echo(
        f"Temporal KG: {summary['node_count']} nodes "
        f"({summary['episode_count']} episodes, {summary['theme_count']} themes), "
        f"{summary['edge_count']} edges"
    )
    typer.echo(f"  → {summary['nodes_path']}")
    typer.echo(f"  → {summary['edges_path']}")


if __name__ == "__main__":
    app()
