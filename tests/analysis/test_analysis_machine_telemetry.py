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
                    gpu_pcie_width, load_1m, mem_total_mb, mem_used_mb,
                    mem_avail_mb, mem_anon_mb, mem_file_cache_mb,
                    mem_slab_reclaimable_mb, mem_slab_unreclaimable_mb,
                    mem_dirty_mb, mem_writeback_mb, mem_shmem_mb, swap_used_mb,
                    io_psi_some_avg10, io_psi_full_avg10,
                    gap_codes, refresh_id
                ) VALUES (?, 'host', 'boot', 'machine.telemetry', 2,
                    ?, 40, 5, ?, 16, ?, 32000, ?, ?, ?, ?, ?, ?, 2, 1, ?, ?, ?, ?, [], 'r1')
                """,
                [
                    datetime(2026, 5, day, 12, tzinfo=timezone.utc),
                    20.0 + idx,
                    4 if day >= 5 else 2,
                    float(idx),
                    12000 + idx,
                    32000 - (idx * 100),
                    8000 + idx,
                    3000 + idx,
                    700 + idx,
                    300 + idx,
                    500 + idx,
                    idx * 256,
                    0.1 * idx,
                    0.05 * idx,
                ],
            )

    analysis = analyze_machine_telemetry(path=db)

    assert analysis.coverage.sample_count == 8
    assert len(analysis.daily) == 8
    assert {signal.metric for signal in analysis.signals} >= {
        "p95_load_1m",
        "min_mem_avail_mb",
        "max_swap_used_mb",
    }
    assert analysis.daily[-1].max_swap_used_mb == 2048
    assert analysis.daily[-1].max_mem_used_mb == 12008
    assert analysis.daily[-1].max_mem_file_cache_mb == 3008
    assert analysis.memory_breakdown[-1].max_mem_anon_mb == 8008
    assert analysis.memory_breakdown[-1].max_mem_slab_reclaimable_mb == 708
    assert analysis.memory_breakdown[-1].max_mem_slab_unreclaimable_mb == 308
    assert analysis.hardware_regimes[0].sample_count == 4
    assert max(regime.max_swap_used_mb or 0 for regime in analysis.hardware_regimes) == 2048
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


def test_machine_telemetry_dedupes_refresh_partitions(tmp_path):
    db = tmp_path / "sub.duckdb"
    observed = datetime(2026, 5, 1, tzinfo=timezone.utc)
    with connect(db) as conn:
        apply_schema(conn)
        for refresh in ("old", "new"):
            conn.execute(
                """
                INSERT INTO machine_metric_sample (
                    observed_at, host, source, source_schema_version,
                    load_1m, mem_avail_mb, gap_codes, refresh_id
                ) VALUES (?, 'host', 'machine.telemetry', 2, ?, 1000, [], ?)
                """,
                [observed, 1.0 if refresh == "old" else 3.0, refresh],
            )

    analysis = analyze_machine_telemetry(path=db)

    assert analysis.coverage.sample_count == 1
    assert analysis.daily[0].avg_load_1m == 3.0


def test_machine_telemetry_excludes_null_pcie_from_hardware_regimes(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, gpu_pcie_gen, gpu_pcie_width, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 1, NULL, NULL, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 1, 4, 16, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 2, tzinfo=timezone.utc),
            ],
        )

    analysis = analyze_machine_telemetry(path=db)

    assert analysis.coverage.sample_count == 2
    assert analysis.coverage.pcie_state_sample_count == 1
    assert [(row.gpu_pcie_gen, row.gpu_pcie_width) for row in analysis.hardware_regimes] == [(4, 16)]
    assert "rows without PCIe state are excluded from hardware-regime comparisons" in analysis.caveats
