from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lynchpin.analysis.machine.stall_attribution import analyze_stall_attribution
from lynchpin.substrate.connection import apply_schema, connect


def _ts(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, 6, 0, minute, second, tzinfo=timezone.utc)


def _insert(conn: Any, table: str, **cols: Any) -> None:
    names = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", list(cols.values()))


def _metric(conn: Any, minute: int, second: int = 0, **overrides: Any) -> None:
    cols: dict[str, Any] = {
        "observed_at": _ts(minute, second),
        "host": "sinnix-prime",
        "source": "machine.telemetry",
        "source_schema_version": 5,
        "gap_codes": [],
        "refresh_id": "r1",
        "memory_psi_full_avg60": 2.0,
    }
    cols.update(overrides)
    _insert(conn, "machine_metric_sample", **cols)


def _cgroup(conn: Any, minute: int, second: int = 0, **overrides: Any) -> None:
    cols: dict[str, Any] = {
        "observed_at": _ts(minute, second),
        "host": "sinnix-prime",
        "boot_id": "boot-a",
        "source_schema_version": 5,
        "refresh_id": "r1",
    }
    cols.update(overrides)
    _insert(conn, "machine_cgroup_memory_sample", **cols)


def test_sustained_memory_psi_window_attributes_to_peak_slice(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

        # Quiet samples before the freeze.
        _metric(conn, 15, 0)
        _metric(conn, 18, 0)

        # Sustained avg60 memory-PSI freeze.
        _metric(conn, 20, 0, memory_psi_full_avg60=45.0)
        _metric(conn, 21, 0, memory_psi_full_avg60=60.0)
        _metric(conn, 22, 0, memory_psi_full_avg60=55.0)

        # Quiet again afterward.
        _metric(conn, 30, 0)

        # Two candidate slices during the (padded) window: user.agent grows
        # the most and should win the attribution ranking over system.background,
        # which stays flat.
        _cgroup(
            conn, 19, 0, label="user.agent", scope="user",
            control_group="/user.slice/user-1000.slice/user@1000.service/agent.slice",
            memory_current_bytes=500_000_000,
        )
        _cgroup(
            conn, 22, 30, label="user.agent", scope="user",
            control_group="/user.slice/user-1000.slice/user@1000.service/agent.slice",
            memory_current_bytes=6_000_000_000, memory_peak_bytes=6_500_000_000,
        )
        _cgroup(
            conn, 19, 0, label="system.background", scope="system",
            control_group="/system.slice/system-background.slice",
            memory_current_bytes=800_000_000,
        )
        _cgroup(
            conn, 22, 30, label="system.background", scope="system",
            control_group="/system.slice/system-background.slice",
            memory_current_bytes=900_000_000, memory_peak_bytes=900_000_000,
        )
        # A per-service "unit:*" row must never be attributed as a slice.
        _cgroup(
            conn, 21, 0, label="unit:below.service", scope="system",
            control_group="/system.slice/below.service",
            memory_current_bytes=9_999_999_999, memory_peak_bytes=9_999_999_999,
        )

    analysis = analyze_stall_attribution(start=_ts(0).date(), end=_ts(0).date(), path=db)

    assert analysis.window_count == 1
    window = analysis.windows[0]
    assert window.host == "sinnix-prime"
    assert window.started_at == _ts(20, 0)
    assert window.ended_at == _ts(22, 0)
    assert window.peak_memory_psi_full_avg60 == 60.0
    assert not window.caveats

    labels = [s.label for s in window.top_attributed_slices]
    assert labels[0] == "user.agent"
    assert "unit:below.service" not in labels

    top = window.top_attributed_slices[0]
    assert top.peak_bytes == 6_500_000_000
    assert top.delta_bytes == 5_500_000_000

    # Here user.agent also grows the most, so both rankings agree.
    growth_labels = [s.label for s in window.top_by_growth]
    assert growth_labels[0] == "user.agent"
    assert "unit:below.service" not in growth_labels


def test_no_spike_below_threshold_reports_no_windows(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute in range(5):
            _metric(conn, minute)

    analysis = analyze_stall_attribution(start=_ts(0).date(), end=_ts(0).date(), path=db)

    assert analysis.window_count == 0
    assert any("no memory_psi_full_avg60 sample crossed" in c for c in analysis.caveats)


def test_window_with_no_slice_samples_reports_a_caveat(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        _metric(conn, 20, 0, memory_psi_full_avg60=45.0)
        _metric(conn, 21, 0, memory_psi_full_avg60=50.0)

    analysis = analyze_stall_attribution(start=_ts(0).date(), end=_ts(0).date(), path=db)

    assert analysis.window_count == 1
    window = analysis.windows[0]
    assert window.top_attributed_slices == ()
    assert window.top_by_growth == ()
    assert any("no slice-level machine_cgroup_memory_sample rows" in c for c in window.caveats)


def test_isolated_windows_each_self_attribute(tmp_path):
    """Two freezes separated by more than the merge gap stay separate windows,
    each attributing independently to the slice that spiked during it."""
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

        _metric(conn, 10, 0, memory_psi_full_avg60=40.0)
        _metric(conn, 11, 0, memory_psi_full_avg60=42.0)
        # Gap well beyond the default 2-minute merge window.
        _metric(conn, 50, 0, memory_psi_full_avg60=41.0)
        _metric(conn, 51, 0, memory_psi_full_avg60=43.0)

        _cgroup(
            conn, 10, 30, label="user.build", scope="user",
            control_group="/user.slice/build.slice",
            memory_current_bytes=1_000_000_000, memory_peak_bytes=4_000_000_000,
        )
        _cgroup(
            conn, 50, 30, label="system.nix-build", scope="system",
            control_group="/system.slice/nix-build.slice",
            memory_current_bytes=1_000_000_000, memory_peak_bytes=7_000_000_000,
        )

    analysis = analyze_stall_attribution(start=_ts(0).date(), end=_ts(0).date(), path=db)

    assert analysis.window_count == 2
    first, second = analysis.windows
    assert first.top_attributed_slices[0].label == "user.build"
    assert second.top_attributed_slices[0].label == "system.nix-build"


def test_peak_and_growth_rankings_can_disagree(tmp_path):
    """Mirrors the real 2026-08-03 05:11:08 containment-escape window
    (sinnix-a1dp.1): user.build is a huge slice that was already shrinking
    during the window (largest peak, negative delta), while user.agent is
    smaller but the only slice actively growing (smaller peak, only
    positive delta) -- the actual escape driver. Peak-rank and growth-rank
    must pick different winners here rather than silently agreeing."""
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

        _metric(conn, 20, 0, memory_psi_full_avg60=40.0)
        _metric(conn, 21, 0, memory_psi_full_avg60=50.0)

        # user.build: huge and shrinking during the window.
        _cgroup(
            conn, 19, 0, label="user.build", scope="user",
            control_group="/user.slice/build.slice",
            memory_current_bytes=22_720_000_000, memory_peak_bytes=19_320_000_000,
        )
        _cgroup(
            conn, 22, 0, label="user.build", scope="user",
            control_group="/user.slice/build.slice",
            memory_current_bytes=19_320_000_000, memory_peak_bytes=19_320_000_000,
        )
        # user.agent: smaller peak, but the only slice that grew.
        _cgroup(
            conn, 19, 0, label="user.agent", scope="user",
            control_group="/user.slice/user-1000.slice/user@1000.service/agent.slice",
            memory_current_bytes=17_120_000_000, memory_peak_bytes=17_120_000_000,
        )
        _cgroup(
            conn, 22, 0, label="user.agent", scope="user",
            control_group="/user.slice/user-1000.slice/user@1000.service/agent.slice",
            memory_current_bytes=18_220_000_000, memory_peak_bytes=18_220_000_000,
        )

    analysis = analyze_stall_attribution(start=_ts(0).date(), end=_ts(0).date(), path=db)

    assert analysis.window_count == 1
    window = analysis.windows[0]

    assert window.top_attributed_slices[0].label == "user.build"
    assert window.top_attributed_slices[0].delta_bytes < 0

    assert window.top_by_growth[0].label == "user.agent"
    assert window.top_by_growth[0].delta_bytes > 0
    assert window.top_by_growth[0].label != window.top_attributed_slices[0].label
