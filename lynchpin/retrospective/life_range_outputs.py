from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .life_summary_rendering import render_markdown


def write_life_range_outputs(
    *,
    payload: dict[str, object],
    output: Path,
    markdown_output: Optional[Path] = None,
    markdown_output_dir: Optional[Path] = None,
) -> dict[str, Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    artifact_paths: dict[str, Path] = {"output": output}
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_markdown(payload), encoding="utf-8")
        artifact_paths["markdown_output"] = markdown_output
    if markdown_output_dir is not None:
        _write_markdown_drilldowns(payload=payload, output=output, markdown_output_dir=markdown_output_dir)
        artifact_paths["markdown_output_dir"] = markdown_output_dir
    return artifact_paths


def _write_markdown_drilldowns(
    *,
    payload: dict[str, object],
    output: Path,
    markdown_output_dir: Path,
) -> None:
    markdown_output_dir.mkdir(parents=True, exist_ok=True)
    payload_months = payload.get("months")
    if not isinstance(payload_months, dict):
        return

    years = sorted({month.split("-", 1)[0] for month in payload_months})
    index_lines = [
        f"# Life timeline drilldowns ({payload['range']['start_month']} → {payload['range']['end_month']})",
        "",
        f"Generated: `{payload.get('generated_at')}`",
        f"Backing JSON: `{output}`",
        "",
        "## Years",
        "",
        *[f"- `{year}.md`" for year in years],
        "",
    ]
    (markdown_output_dir / "index.md").write_text("\n".join(index_lines), encoding="utf-8")

    for year in years:
        year_months = {month: payload_months[month] for month in payload_months if month.startswith(f"{year}-")}
        if not year_months:
            continue
        year_payload = {
            "generated_at": payload.get("generated_at"),
            "range": {"start_month": min(year_months.keys()), "end_month": max(year_months.keys())},
            "output_path": str(output),
            "months": year_months,
        }
        (markdown_output_dir / f"{year}.md").write_text(render_markdown(year_payload), encoding="utf-8")
