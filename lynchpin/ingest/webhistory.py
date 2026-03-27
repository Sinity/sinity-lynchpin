"""CLI for canonical webhistory maintenance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from ..core.config import get_config
from ..core.io import write_text_if_changed
from .webhistory_audit import audit_webhistory
from .webhistory_compare import compare_webhistory
from .webhistory_dedup import build_full_history, dedup_webhistory

app = typer.Typer(help="Webhistory derived artefacts")


@app.command()
def dedup(
    raw_root: Optional[Path] = typer.Option(
        None,
        "--raw-root",
        help="Raw webhistory export directory (defaults to lynchpin config).",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        help="Directory to write deduped segments (defaults to the canonical webhistory dir).",
    ),
    tolerance_seconds: int = typer.Option(
        30,
        "--tolerance-seconds",
        help="Timestamp tolerance (seconds) when identifying duplicates. Default 30s catches Chrome's habit of recording the same page view multiple times within seconds.",
    ),
    files: list[str] = typer.Option(
        [],
        "--file",
        help="Specific raw filenames to process (repeatable, relative to --raw-root).",
    ),
    report: Optional[Path] = typer.Option(
        None,
        "--report",
        help="Optional JSON report path (defaults to the canonical webhistory derived directory).",
    ),
    manifest: Optional[Path] = typer.Option(
        None,
        "--manifest",
        help="Optional manifest path to record input signatures (defaults to the canonical webhistory derived directory).",
    ),
    force: bool = typer.Option(False, "--force", help="Rebuild even if manifest matches."),
) -> None:
    """Sequentially deduplicate raw exports into canonical segments."""
    cfg = get_config()
    resolved_raw = raw_root or cfg.webhistory_raw_dir
    resolved_output = output_dir or cfg.webhistory_dir
    derived_dir = resolved_output.parent / "derived"
    report_path = report or (derived_dir / "dedup_report.json")
    manifest_path = manifest or (derived_dir / "dedup_manifest.json")

    summary = dedup_webhistory(
        raw_root=resolved_raw,
        output_dir=resolved_output,
        tolerance_seconds=tolerance_seconds,
        files=files or None,
        report_path=report_path,
        manifest_path=manifest_path,
        force=force,
    )

    if summary["missing_inputs"]:
        typer.secho(f"No raw webhistory files found in {resolved_raw}", fg=typer.colors.YELLOW)
        return
    if summary["skipped"]:
        typer.secho("✓ Dedup inputs unchanged; skipping.", fg=typer.colors.GREEN)
        return
    for row in summary["report_rows"]:
        path = Path(str(row["file"]))
        kept_path = row["kept_path"]
        duplicates = int(row["duplicates"])
        unique = int(row["unique"])
        if kept_path:
            out_path = Path(str(kept_path))
            typer.secho(
                f"[keep] {path.name} → {out_path.name} ({unique} unique, {duplicates} dup)",
                fg=typer.colors.GREEN,
            )
            continue
        typer.secho(f"[drop] {path.name} (all duplicates)", fg=typer.colors.YELLOW)


@app.command()
def compare(
    canonical: Optional[Path] = typer.Option(
        None,
        "--canonical",
        help="Canonical gestalt directory or NDJSON file (defaults to lynchpin config).",
    ),
    candidate: Optional[Path] = typer.Option(
        None,
        "--candidate",
        help="Candidate dataset (gestalt dir or NDJSON file) to compare against canonical.",
    ),
    tolerance_seconds: int = typer.Option(
        5,
        "--tolerance-seconds",
        help="Timestamp tolerance when matching events.",
    ),
    sample: int = typer.Option(20, "--sample", help="Max samples to include for missing/extra lists."),
    output: Optional[Path] = typer.Option(
        Path("artefacts/webhistory/gestalt_compare.json"),
        "--output",
        help="Optional JSON output path.",
    ),
) -> None:
    """Compare canonical vs candidate datasets with URL-normalized matching."""
    cfg = get_config()
    resolved_canonical = canonical or cfg.webhistory_dir
    resolved_candidate = candidate or cfg.webhistory_ndjson or cfg.webhistory_dir
    report = compare_webhistory(resolved_canonical, resolved_candidate, tolerance_seconds, sample)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        write_text_if_changed(output, payload)
    typer.echo(payload)


@app.command()
def full_history(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Canonical gestalt segments directory (defaults to lynchpin config).",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Output NDJSON path (defaults to the canonical webhistory derived directory).",
    ),
    tolerance_seconds: int = typer.Option(
        30,
        "--tolerance-seconds",
        help="Cross-file dedup tolerance in seconds (URL + timestamp window). Default 30s catches cross-export duplicates where the same visit was recorded with slightly different timestamps.",
    ),
) -> None:
    """Write merged, deduplicated, chronologically-sorted NDJSON from canonical segments."""
    cfg = get_config()
    resolved_root = root or cfg.webhistory_dir
    derived_dir = cfg.webhistory_dir.parent / "derived"
    resolved_output = output or (derived_dir / "full_history.ndjson")
    summary = build_full_history(root=resolved_root, output=resolved_output, tolerance_seconds=tolerance_seconds)
    typer.secho(
        f"✓ Wrote {summary['row_count']} webhistory rows → {resolved_output} ({summary['duplicate_count']} cross-file duplicates removed)",
        fg=typer.colors.GREEN,
    )


@app.command()
def audit(
    raw_root: Optional[Path] = typer.Option(
        None,
        "--raw-root",
        help="Raw webhistory export directory (defaults to lynchpin config).",
    ),
    canonical: Optional[Path] = typer.Option(
        None,
        "--canonical",
        help="Canonical gestalt directory (defaults to lynchpin config).",
    ),
    merged: Optional[Path] = typer.Option(
        None,
        "--merged",
        help="Merged NDJSON file (defaults to the canonical derived full-history path).",
    ),
    tolerance_seconds: int = typer.Option(
        5,
        "--tolerance-seconds",
        help="Timestamp tolerance used by the dedup simulation.",
    ),
    sample: int = typer.Option(20, "--sample", help="Max samples to include for mismatch lists."),
    output: Optional[Path] = typer.Option(
        Path("artefacts/webhistory/gestalt_audit.json"),
        "--output",
        help="Optional JSON output path.",
    ),
) -> None:
    """Audit canonical outputs against raw inputs and merged NDJSON."""
    cfg = get_config()
    resolved_raw = raw_root or cfg.webhistory_raw_dir
    resolved_canonical = canonical or cfg.webhistory_dir
    resolved_merged = merged or cfg.webhistory_ndjson or (cfg.webhistory_dir.parent / "derived" / "full_history.ndjson")
    report = audit_webhistory(
        raw_root=resolved_raw,
        canonical=resolved_canonical,
        merged=resolved_merged,
        tolerance=tolerance_seconds,
        sample=sample,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        write_text_if_changed(output, payload)
    typer.echo(payload)


if __name__ == "__main__":
    app()
