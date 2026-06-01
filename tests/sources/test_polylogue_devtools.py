from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def test_polylogue_devtools_reads_xtask_jsonl(tmp_path: Path) -> None:
    from lynchpin.sources.polylogue_devtools import iter_xtask_invocations

    xtask = tmp_path / ".agent/xtask/tasks.jsonl"
    xtask.parent.mkdir(parents=True)
    xtask.write_text(
        json.dumps({
            "timestamp": "2026-06-01T17:15:05.837374+00:00",
            "command": "verify",
            "args": ["--all"],
            "duration_ms": 154457.698,
            "exit_code": 1,
            "cwd": "/realm/project/polylogue",
            "class": "verify",
        }) + "\n",
        encoding="utf-8",
    )

    rows = list(iter_xtask_invocations(path=xtask))

    assert len(rows) == 1
    assert rows[0].source == "polylogue_devtools"
    assert rows[0].work_kind == "polylogue_devtools_invocation"
    assert rows[0].command == ("verify", "--all")
    assert rows[0].status == "failed"
    assert rows[0].duration_s == 154.457698
    assert rows[0].project == "polylogue"


def test_polylogue_devtools_reads_log_meta_metrics(tmp_path: Path) -> None:
    from lynchpin.sources.polylogue_devtools import iter_log_invocations

    logs = tmp_path / ".local/logs"
    logs.mkdir(parents=True)
    meta = logs / "polylogue-run-all-20260412T024220+0200.meta"
    metrics = logs / "polylogue-run-all-20260412T024220+0200.metrics.tsv"
    log = logs / "polylogue-run-all-20260412T024220+0200.log"
    meta.write_text(
        "\n".join([
            "started_at=2026-04-12T02:42:20+02:00",
            "runner=run-all",
            "repo=/realm/project/polylogue",
            "commit=abc123",
            f"metrics={metrics.name}",
            f"log={log.name}",
        ]),
        encoding="utf-8",
    )
    metrics.write_text(
        "\n".join([
            "ts\telapsed_s\tproc_count\trss_kb\tcpu_pct",
            "1\t1.0\t2\t1024\t10.0",
            "2\t3.5\t4\t4096\t30.0",
        ]),
        encoding="utf-8",
    )
    log.write_text("Pipeline complete\n", encoding="utf-8")

    rows = list(iter_log_invocations(
        logs_dir=logs,
        start=datetime(2026, 4, 12, tzinfo=timezone.utc),
    ))

    assert len(rows) == 1
    assert rows[0].work_kind == "polylogue_log_run"
    assert rows[0].started_at.isoformat() == "2026-04-12T02:42:20+02:00"
    assert rows[0].duration_s == 3.5
    assert rows[0].process_cpu_usage_avg == 20.0
    assert rows[0].process_memory_usage_max_mb == 4.0
    assert rows[0].process_count_max == 4
    assert rows[0].resource_sample_count == 2
