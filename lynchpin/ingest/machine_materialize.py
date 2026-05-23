"""Materialize canonical machine telemetry products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from ..core.config import get_config
from ..sources.machine import (
    canonical_machine_table_path,
    gpu_samples,
    metric_samples,
    network_samples,
    sample_to_json,
    service_states,
)


def materialize_machine_telemetry() -> dict[str, Any]:
    cfg = get_config()
    reports = {
        "metric_sample": _materialize_table(
            "metric_sample",
            lambda: metric_samples(path=cfg.machine_telemetry_db),
        ),
        "gpu_sample": _materialize_table(
            "gpu_sample",
            lambda: gpu_samples(path=cfg.machine_telemetry_db),
        ),
        "network_sample": _materialize_table(
            "network_sample",
            lambda: network_samples(path=cfg.machine_telemetry_db),
        ),
        "service_state": _materialize_table(
            "service_state",
            lambda: service_states(path=cfg.machine_telemetry_db),
        ),
    }
    manifest_path = canonical_machine_table_path("manifest").with_suffix(".json")
    manifest = {
        "dataset": "machine.telemetry",
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "tables": reports,
        "row_count": sum(int(report["row_count"]) for report in reports.values()),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _materialize_table(name: str, rows_fn: Callable[[], Iterable[object]]) -> dict[str, Any]:
    output = canonical_machine_table_path(name)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    first = None
    last = None
    with output.open("w", encoding="utf-8") as handle:
        for sample in rows_fn():
            row = sample_to_json(sample)
            observed_at = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
            if first is None or observed_at < first:
                first = observed_at
            if last is None or observed_at > last:
                last = observed_at
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return {
        "path": str(output),
        "row_count": count,
        "first_date": first.date().isoformat() if first else None,
        "last_date": last.date().isoformat() if last else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical machine telemetry")
    parser.parse_args(argv)
    report = materialize_machine_telemetry()
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
