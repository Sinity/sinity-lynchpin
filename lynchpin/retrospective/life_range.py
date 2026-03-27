from __future__ import annotations

from pathlib import Path
from typing import Optional

from .life_paths import current_month_key
from .life_periods import iter_months
from .life_range_models import LifeRangeInputs, LifeRangeResult
from .life_range_outputs import write_life_range_outputs
from .life_range_payload import build_life_range_payload
from .life_range_sources import collect_life_range_evidence


def build_life_range(
    *,
    start_month: str,
    end_month: str | None = None,
    output: Path,
    inputs: LifeRangeInputs | None = None,
    markdown_output: Optional[Path] = None,
    markdown_output_dir: Optional[Path] = None,
) -> LifeRangeResult:
    resolved_end_month = end_month or current_month_key()
    resolved_inputs = inputs or LifeRangeInputs()
    if markdown_output is not None and markdown_output_dir is not None:
        raise ValueError("Pass at most one of markdown_output or markdown_output_dir.")

    months = list(iter_months(start_month, resolved_end_month))
    evidence = collect_life_range_evidence(
        start_month=start_month,
        end_month=resolved_end_month,
        months=months,
        inputs=resolved_inputs,
    )
    payload = build_life_range_payload(
        start_month=start_month,
        end_month=resolved_end_month,
        output=output,
        months=months,
        evidence=evidence,
        inputs=resolved_inputs,
    )
    artifact_paths = write_life_range_outputs(
        payload=payload,
        output=output,
        markdown_output=markdown_output,
        markdown_output_dir=markdown_output_dir,
    )
    artifact_paths["youtube_oembed_cache"] = resolved_inputs.youtube_oembed_cache

    return LifeRangeResult(
        output=output,
        start_month=start_month,
        end_month=resolved_end_month,
        month_count=len(payload.get("months", {})),
        artifact_paths=artifact_paths,
    )
