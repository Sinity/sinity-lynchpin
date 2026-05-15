from __future__ import annotations

from datetime import date, datetime, timezone

from lynchpin.analysis.machine.telemetry import analyze_machine_telemetry
from lynchpin.substrate.connection import apply_schema, connect


def test_machine_telemetry_analysis_keeps_general_signals(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for idx, day in enumerate(range(1, 9), start=1):
            conn.execute(
                """
                INSERT INTO machine_metric_sample (
                    observed_at, host, boot_id, source, source_schema_version,
                    gpu_power_w, gpu_temp_c, gpu_util_pct, gpu_pcie_gen,
                    gpu_pcie_width, load_1m, mem_avail_mb,
                    io_psi_some_avg10, io_psi_full_avg10,
                    gap_codes, refresh_id
                ) VALUES (?, 'host', 'boot', 'machine.telemetry', 2,
                    ?, 40, 5, ?, 16, ?, ?, ?, ?, [], 'r1')
                """,
                [
                    datetime(2026, 5, day, 12, tzinfo=timezone.utc),
                    20.0 + idx,
                    4 if day >= 5 else 2,
                    float(idx),
                    32000 - (idx * 100),
                    0.1 * idx,
                    0.05 * idx,
                ],
            )

    analysis = analyze_machine_telemetry(path=db)

    assert analysis.coverage.sample_count == 8
    assert len(analysis.daily) == 8
    assert {signal.metric for signal in analysis.signals} >= {"p95_load_1m", "min_mem_avail_mb"}
    assert analysis.hardware_regimes[0].sample_count == 4
    assert any(regime.gpu_pcie_gen == 4 for regime in analysis.hardware_regimes)
    assert analysis.correlations


def test_machine_telemetry_analysis_respects_window(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for day in (1, 2):
            conn.execute(
                """
                INSERT INTO machine_metric_sample (
                    observed_at, host, source, source_schema_version,
                    load_1m, mem_avail_mb, gap_codes, refresh_id
                ) VALUES (?, 'host', 'machine.telemetry', 2, ?, 1000, [], 'r1')
                """,
                [datetime(2026, 5, day, tzinfo=timezone.utc), float(day)],
            )

    analysis = analyze_machine_telemetry(path=db, start=date(2026, 5, 2), end=date(2026, 5, 2))

    assert analysis.coverage.sample_count == 1
    assert analysis.daily[0].day == date(2026, 5, 2)
