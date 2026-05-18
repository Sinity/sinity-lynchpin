from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.analysis.machine.gap_summary import (
    DEFAULT_REGRESSION_PCT,
    analyze_gap_summary,
)
from lynchpin.substrate.connection import apply_schema, connect


def _insert_metric(conn, *, observed_at: datetime, gap_codes: list[str], refresh_id: str = "r1") -> None:
    conn.execute(
        """
        INSERT INTO machine_metric_sample (
            observed_at, host, source, source_schema_version,
            load_1m, mem_avail_mb, swap_used_mb, io_psi_some_avg10, io_psi_full_avg10,
            latency_oversleep_ms, dstate_task_count, gpu_temp_c, gpu_util_pct,
            gpu_pcie_gen, gpu_pcie_width, gap_codes, refresh_id
        ) VALUES (?, 'host', 'machine.telemetry', 2,
                  1, 32000, 0, 0, 0, 1, 0, 40, 5, 4, 16, ?, ?)
        """,
        [observed_at, gap_codes, refresh_id],
    )


def _insert_network(conn, *, observed_at: datetime, gap_codes: list[str], refresh_id: str = "r1") -> None:
    conn.execute(
        """
        INSERT INTO machine_network_sample (
            observed_at, host, source_schema_version, interface, gateway_ip,
            ping, iface, nic, tcp, dns_ms, pmtu_1492, conntrack, gap_codes, refresh_id
        ) VALUES (?, 'host', 1, 'enp4s0', '192.0.2.1',
                  '{"avg_ms":1}', '{}', '{}', '{}', 10, true, '{}', ?, ?)
        """,
        [observed_at, gap_codes, refresh_id],
    )


def test_gap_summary_detects_persistent_regression(tmp_path):
    """A code appearing in 100% of rows must surface as a critical regression."""
    db = tmp_path / "sub.duckdb"
    base = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    with connect(db) as conn:
        apply_schema(conn)
        # 5 network samples, all carrying network.interface_missing
        # (this is the 2026-04-11→2026-05-15 enp6s0 regression shape).
        for i in range(5):
            _insert_network(conn, observed_at=base.replace(minute=i),
                            gap_codes=["network.interface_missing"])
        # 5 metric samples, none with codes — proves per-table separation.
        for i in range(5):
            _insert_metric(conn, observed_at=base.replace(minute=i), gap_codes=[])

    analysis = analyze_gap_summary(path=db, lookback_days=30, now=base.replace(hour=23))

    # Network table shows the regression at 100% share.
    network_regressions = [r for r in analysis.regressions if r.table == "machine_network_sample"]
    assert len(network_regressions) == 1
    assert network_regressions[0].code == "network.interface_missing"
    assert network_regressions[0].share_pct == 100.0
    assert network_regressions[0].severity == "critical"
    assert network_regressions[0].rows_with_code == 5
    assert network_regressions[0].rows_in_window == 5

    # Metric table contributed no codes — no regressions from it.
    metric_regressions = [r for r in analysis.regressions if r.table == "machine_metric_sample"]
    assert metric_regressions == []


def test_gap_summary_threshold_filters_noise(tmp_path):
    """A code present in 1 of 100 samples must NOT trigger at default 5%."""
    db = tmp_path / "sub.duckdb"
    base = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    with connect(db) as conn:
        apply_schema(conn)
        for i in range(99):
            _insert_metric(conn, observed_at=base.replace(minute=i % 60, second=i // 60),
                           gap_codes=[])
        _insert_metric(conn, observed_at=base.replace(minute=59, second=59),
                       gap_codes=["collector.late"])

    analysis = analyze_gap_summary(
        path=db,
        lookback_days=30,
        regression_pct=DEFAULT_REGRESSION_PCT,
        now=base.replace(hour=23),
    )

    # collector.late should appear in counts at 1.0% share but not in regressions.
    counts = {c.code: c for c in analysis.counts if c.table == "machine_metric_sample"}
    assert counts["collector.late"].share_pct == 1.0
    assert counts["collector.late"].rows_with_code == 1
    assert counts["collector.late"].rows_in_window == 100
    assert all(r.code != "collector.late" for r in analysis.regressions)


def test_gap_summary_multi_code_unnests(tmp_path):
    """A sample with two codes contributes one count to each — UNNEST behavior."""
    db = tmp_path / "sub.duckdb"
    base = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    with connect(db) as conn:
        apply_schema(conn)
        _insert_metric(conn, observed_at=base,
                       gap_codes=["fan.hwmon_unavailable", "gpu.nvml_init_failed"])
        _insert_metric(conn, observed_at=base.replace(minute=1),
                       gap_codes=["fan.hwmon_unavailable"])

    analysis = analyze_gap_summary(path=db, lookback_days=30, now=base.replace(hour=23))

    metric_counts = {c.code: c for c in analysis.counts if c.table == "machine_metric_sample"}
    assert metric_counts["fan.hwmon_unavailable"].rows_with_code == 2
    assert metric_counts["fan.hwmon_unavailable"].share_pct == 100.0
    assert metric_counts["gpu.nvml_init_failed"].rows_with_code == 1
    assert metric_counts["gpu.nvml_init_failed"].share_pct == 50.0


def test_gap_summary_empty_window_returns_no_counts(tmp_path):
    """No rows in lookback → empty result, not a crash."""
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    analysis = analyze_gap_summary(path=db, lookback_days=7, now=now)
    assert analysis.counts == []
    assert analysis.regressions == []


def test_gap_summary_excludes_legacy_codes_from_regressions(tmp_path):
    """legacy.* codes appear in counts but never flag as regressions —
    they reflect retired-collector backfills that would otherwise drown
    the actionable signal."""
    db = tmp_path / "sub.duckdb"
    base = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    with connect(db) as conn:
        apply_schema(conn)
        # 100% of rows carry both a legacy.* code and a real bug code.
        for i in range(10):
            _insert_metric(
                conn,
                observed_at=base.replace(minute=i),
                gap_codes=["legacy.no_psi", "fan.hwmon_unavailable"],
            )

    analysis = analyze_gap_summary(path=db, lookback_days=30, now=base.replace(hour=23))

    # Both codes are in counts.
    codes_in_counts = {c.code for c in analysis.counts}
    assert "legacy.no_psi" in codes_in_counts
    assert "fan.hwmon_unavailable" in codes_in_counts

    # Only the non-legacy code surfaces as a regression.
    regression_codes = {r.code for r in analysis.regressions}
    assert regression_codes == {"fan.hwmon_unavailable"}

    # And a caller can pass a different prefix list to override.
    analysis_override = analyze_gap_summary(
        path=db,
        lookback_days=30,
        now=base.replace(hour=23),
        legacy_prefixes=(),
    )
    assert {r.code for r in analysis_override.regressions} == {
        "legacy.no_psi",
        "fan.hwmon_unavailable",
    }
