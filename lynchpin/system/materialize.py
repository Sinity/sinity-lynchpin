from __future__ import annotations

import subprocess
import sys
from typing import Optional

import typer

from ..core.config import get_config
from ..ingest import webhistory as webhistory_ingest
from ..views import ledgers, warehouse, velocity

app = typer.Typer(help="Materialize derived artefacts from lazy sources.")


@app.command()
def run(
    webhistory: bool = typer.Option(
        True,
        "--webhistory/--no-webhistory",
        help="Build webhistory dedup outputs + merged NDJSON.",
    ),
    webhistory_dedup: bool = typer.Option(
        True,
        "--webhistory-dedup/--no-webhistory-dedup",
        help="Run raw->dedup step before merging full history.",
    ),
    webhistory_compare: bool = typer.Option(
        False,
        "--webhistory-compare/--no-webhistory-compare",
        help="Compare dedup outputs vs canonical segments.",
    ),
    ledgers_enabled: bool = typer.Option(
        True,
        "--ledgers/--no-ledgers",
        help="Refresh optional ledger exports.",
    ),
    warehouse_enabled: bool = typer.Option(
        True,
        "--warehouse/--no-warehouse",
        help="Rebuild the DuckDB warehouse.",
    ),
    warehouse_limit: Optional[int] = typer.Option(
        None,
        "--warehouse-limit",
        help="Optional row cap for warehouse loads.",
    ),
    warehouse_format: str = typer.Option(
        "parquet",
        "--warehouse-format",
        help="Per-source output format for the warehouse (duckdb or parquet).",
    ),
    warehouse_sources: Optional[str] = typer.Option(
        None,
        "--warehouse-sources",
        help="Comma-separated source list to materialize (default: all).",
    ),
    velocity_enabled: bool = typer.Option(
        False,
        "--velocity/--no-velocity",
        help="Rebuild the velocity dashboard.",
    ),
    baseline_enabled: bool = typer.Option(
        False,
        "--baseline/--no-baseline",
        help="Run the baseline pipeline (heavy).",
    ),
) -> None:
    cfg = get_config()

    if webhistory:
        if webhistory_dedup:
            typer.secho("→ Webhistory dedup", fg=typer.colors.CYAN)
            webhistory_ingest.dedup(raw_root=cfg.webhistory_raw_dir)
        typer.secho("→ Webhistory full-history", fg=typer.colors.CYAN)
        webhistory_ingest.full_history(
            root=cfg.webhistory_dir,
            output=cfg.webhistory_dir.parent / "derived/full_history.ndjson",
        )
        if webhistory_compare:
            typer.secho("→ Webhistory compare", fg=typer.colors.CYAN)
            webhistory_ingest.compare(
                canonical=cfg.webhistory_dir,
                candidate=cfg.webhistory_ndjson,
            )

    if ledgers_enabled:
        typer.secho("→ Ledger export (artefacts)", fg=typer.colors.CYAN)
        ledgers.artefact(
            catalog=cfg.repo_root / "docs/reference/ledgers/artefact_catalog.json",
            output=cfg.repo_root / "artefacts/knowledge/ledgers/artefact_index.csv",
        )

    if warehouse_enabled:
        typer.secho("→ Warehouse", fg=typer.colors.CYAN)
        sources = warehouse_sources.split(",") if warehouse_sources else None
        warehouse.refresh(
            output=cfg.warehouse_db,
            root=cfg.warehouse_root,
            output_format=warehouse_format,
            sources=sources,
            limit=warehouse_limit,
        )

    if velocity_enabled:
        typer.secho("→ Velocity", fg=typer.colors.CYAN)
        velocity.build(output=velocity.DEFAULT_OUTPUT, project=None)

    if baseline_enabled:
        typer.secho("→ Baseline pipeline", fg=typer.colors.CYAN)
        _run_baseline(cfg)


def _run_baseline(cfg) -> None:
    script = cfg.repo_root / "pipelines/core/baseline/build_baseline.py"
    cmd = [
        sys.executable,
        str(script),
        "--session-root",
        "/realm/data/sinity-lynchpin/baseline-inputs/latest",
        "--health-root",
        str(cfg.sleep_jsonl.parent),
        "--output-dir",
        str(cfg.repo_root / "artefacts/core/baseline/latest"),
        "--mode",
        "auto",
        "--full",
    ]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    app()
