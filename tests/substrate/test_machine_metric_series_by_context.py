"""Contract test for the context-segmented machine metric series reader.

Pins the ASOF "active generation at each sample" semantics, the null-before-
first-activation honesty, and the hardware-regime (PCIe) segmentation. In-memory
DuckDB; no real substrate.
"""

from __future__ import annotations

from datetime import date

import duckdb

from lynchpin.substrate.machine import load_machine_metric_series_by_context


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE machine_metric_sample (
            refresh_id VARCHAR, host VARCHAR, observed_at TIMESTAMPTZ,
            gpu_pcie_gen INTEGER, gpu_pcie_width INTEGER,
            cpu_package_w DOUBLE, gpu_power_w DOUBLE,
            io_psi_full_avg10 DOUBLE, cpu_psi_some_avg60 DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE sinnix_generation (
            refresh_id VARCHAR, host VARCHAR, generation VARCHAR,
            activated_at TIMESTAMPTZ, sinnix_revision VARCHAR
        )
        """
    )
    conn.execute(
        "INSERT INTO sinnix_generation VALUES "
        "('g','h','7','2026-05-20 10:00:00+00','rev7'),"
        "('g','h','8','2026-05-20 18:00:00+00','rev8')"
    )
    # before any generation (09:00): gen NULL; under gen 7 (12:00, two PCIe
    # regimes); under gen 8 (20:00).
    conn.execute(
        "INSERT INTO machine_metric_sample VALUES "
        "('r','h','2026-05-20 09:00:00+00',1,16,40.0,5.0,2.0,0.5),"
        "('r','h','2026-05-20 12:00:00+00',4,16,90.0,80.0,3.0,0.7),"
        "('r','h','2026-05-20 12:30:00+00',1,16,50.0,10.0,9.0,0.6),"
        "('r','h','2026-05-20 20:00:00+00',4,16,88.0,75.0,4.0,0.8)"
    )
    return conn


def test_segments_by_generation_and_pcie_with_asof_and_null_pre_activation() -> None:
    conn = _conn()
    rows = load_machine_metric_series_by_context(
        conn, refresh_id="r", generations_refresh_id="g",
        start=date(2026, 5, 20), end=date(2026, 5, 20), host="h",
    )
    # key each segment by (generation, gpu_pcie_gen)
    seg = {(r[1], r[3]): r for r in rows}
    # 09:00 sample predates gen 7 -> generation NULL (honest, not imputed)
    assert (None, 1) in seg
    assert seg[(None, 1)][5] == 1  # samples
    # 12:00 + 12:30 fall under gen 7, split by PCIe regime (4 vs 1)
    assert seg[("7", 4)][2] == "rev7"  # sinnix_revision via ASOF
    assert seg[("7", 4)][5] == 1
    assert seg[("7", 1)][5] == 1
    # 20:00 falls under gen 8 (ASOF picks latest activated_at <= observed_at)
    assert seg[("8", 4)][2] == "rev8"
    assert seg[("8", 4)][5] == 1
    # four distinct (generation, pcie) segments total
    assert len(rows) == 4


def test_generations_refresh_id_none_still_matches() -> None:
    conn = _conn()
    rows = load_machine_metric_series_by_context(
        conn, refresh_id="r", generations_refresh_id=None, host="h",
    )
    gens = {r[1] for r in rows}
    assert gens == {None, "7", "8"}
