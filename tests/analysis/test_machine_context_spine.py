"""Contract tests for the machine context spine resolver.

Pins the coverage-honesty behaviour: the resolver attaches the *active*
generation and *nearest* telemetry sample, and emits caveats — never imputed
values — when a fact is absent or the nearest sample is out of the configured
gap. Uses an in-memory DuckDB so no real substrate is required.
"""

from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pytest

from lynchpin.analysis.machine.context_spine import resolve_machine_context


def _fixture_conn() -> duckdb.DuckDBPyConnection:
    from lynchpin.substrate.connection import apply_schema

    conn = duckdb.connect()
    apply_schema(conn)
    conn.execute(
        "INSERT INTO sinnix_generation (host, generation, activated_at, store_path, sinnix_revision, nixos_label, refresh_id) VALUES "
        "('h','50','2026-05-18 17:00:00+00','/nix/store/g50','rev50','label50','fixture'),"
        "('h','51','2026-05-19 14:00:00+00','/nix/store/g51','rev51','label51','fixture')"
    )
    # one sample at 2026-05-20 12:00:05Z (5s after the query instant below)
    conn.execute(
        "INSERT INTO machine_metric_sample (host, observed_at, boot_id, gpu_pcie_gen, gpu_pcie_width, gpu_pstate, "
        "cpu_package_w, cpu_psi_some_avg60, io_psi_some_avg10, io_psi_full_avg10, io_psi_some_avg60, "
        "io_psi_full_avg60, memory_psi_some_avg60, memory_psi_full_avg60, source, source_schema_version, refresh_id) VALUES "
        "('h','2026-05-20 12:00:05+00','boot-a',1,16,'P8',23.0,0.7,"
        "1.1,3.8,0.9,2.2,0.3,0.05,'fixture',1,'fixture')"
    )
    return conn


def test_in_coverage_resolves_generation_and_nearest_sample() -> None:
    conn = _fixture_conn()
    ctx = resolve_machine_context(
        conn, at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc), host="h"
    )
    # active generation = latest activated_at <= t (gen 51, activated 05-19)
    assert ctx.software.generation == "51"
    assert ctx.software.sinnix_revision == "rev51"
    assert ctx.software.nixos_label == "label51"
    # hardware/contention from the nearest sample (5s away, within default gap)
    assert ctx.hardware.gpu_pcie_gen == 1
    assert ctx.hardware.gpu_pcie_width == 16
    assert ctx.hardware.gpu_pstate == "P8"
    assert ctx.hardware.sample_age_seconds == pytest.approx(5.0, abs=0.5)
    assert ctx.contention.io_psi_full_avg10 == pytest.approx(3.8)
    assert ctx.caveats == ()


def test_before_first_generation_emits_caveat_not_value() -> None:
    conn = _fixture_conn()
    ctx = resolve_machine_context(
        conn, at=datetime(2026, 5, 18, 0, 0, 0, tzinfo=timezone.utc), host="h"
    )
    assert ctx.software.generation is None
    assert "software_revision.no_generation_at_or_before_t" in ctx.caveats


def test_out_of_coverage_sample_flagged_by_gap() -> None:
    conn = _fixture_conn()
    # query a week before the only sample -> far beyond max_sample_gap_s
    ctx = resolve_machine_context(
        conn, at=datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc), host="h"
    )
    assert ctx.hardware.sample_age_seconds is not None
    assert ctx.hardware.sample_age_seconds > 300
    assert any(c.startswith("telemetry.nearest_sample_gap_s=") for c in ctx.caveats)


def test_unknown_host_has_no_sample() -> None:
    conn = _fixture_conn()
    ctx = resolve_machine_context(
        conn, at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc), host="other"
    )
    assert ctx.hardware.boot_id is None
    assert "telemetry.no_metric_sample_for_host" in ctx.caveats
