from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lynchpin.analysis.machine.episodes import analyze_machine_episodes
from lynchpin.substrate.connection import apply_schema, connect


def _ts(minute: int) -> datetime:
    return datetime(2026, 5, 1, 12, minute, tzinfo=timezone.utc)


def _metric(conn: Any, minute: int, **values: Any) -> None:
    cols: dict[str, Any] = {
        "observed_at": _ts(minute),
        "host": "host",
        "source": "machine.telemetry",
        "source_schema_version": 2,
        "gap_codes": [],
        "refresh_id": "r1",
    }
    cols.update(values)
    names = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO machine_metric_sample ({names}) VALUES ({placeholders})",
        list(cols.values()),
    )


def _gpu(conn: Any, minute: int, **values: Any) -> None:
    cols: dict[str, Any] = {
        "observed_at": _ts(minute),
        "host": "host",
        "source": "machine.gpu",
        "refresh_id": "r1",
    }
    cols.update(values)
    names = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO machine_gpu_sample ({names}) VALUES ({placeholders})",
        list(cols.values()),
    )


def test_sustained_multisource_pressure_emits_split_kinds(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute in range(4):  # 4 samples > the 3-sample sustained floor
            _metric(
                conn,
                minute,
                load_1m=30,
                io_psi_some_avg10=40,
                io_psi_full_avg10=30,
                memory_psi_some_avg60=20,
                swap_used_mb=4096,
                latency_oversleep_ms=80,
                dstate_task_count=15,
                gpu_temp_c=90,
                gpu_pcie_gen=4,
                gpu_pcie_width=16,
            )
        conn.execute(
            """
            INSERT INTO machine_network_sample (
                observed_at, host, source_schema_version, interface, gateway_ip,
                ping, iface, nic, tcp, dns_ms, pmtu_1492, conntrack, gap_codes, refresh_id
            ) VALUES (?, 'host', 1, 'enp7s0', '192.0.2.1',
                '{"avg_ms":150,"loss_pct":2}', '{}', '{}', '{}', 250, true, '{}', [], 'r1')
            """,
            [_ts(1)],
        )
        conn.execute(
            """
            INSERT INTO machine_service_state (
                observed_at, host, unit, scope, active_state, sub_state,
                memory_current_bytes, refresh_id
            ) VALUES (?, 'host', 'polylogued.service', 'user', 'failed', 'failed', 10, 'r1')
            """,
            [_ts(1)],
        )

    analysis = analyze_machine_episodes(path=db)
    kinds = {episode.kind for episode in analysis.episodes}

    assert {
        "load_pressure",
        "io_pressure",
        "system_stall",
        "memory_pressure",
        "swap_pressure",
        "blocked_task_pressure",
        "gpu_thermal",
        "scheduler_latency",
        "network_degraded",
        "service_instability",
    }.issubset(kinds)
    # The mislabeled legacy kind is gone.
    assert "gpu_power_or_thermal" not in kinds
    # Everything is sustained, so nothing was suppressed.
    assert analysis.suppressed_transient_count == 0
    assert analysis.detector_version == "sustained-pressure-v2"
    assert analysis.min_sustained_samples == 3

    io = next(e for e in analysis.episodes if e.kind == "io_pressure")
    assert io.sample_count == 4
    assert {item.metric for item in io.evidence} == {"io_psi_some"}

    stall = next(e for e in analysis.episodes if e.kind == "system_stall")
    assert {item.metric for item in stall.evidence} >= {"io_psi_full"}


def test_single_sample_pressure_is_suppressed_not_emitted(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        _metric(conn, 0, load_1m=30, io_psi_some_avg10=40)

    analysis = analyze_machine_episodes(path=db)
    kinds = {episode.kind for episode in analysis.episodes}

    assert "load_pressure" not in kinds
    assert "io_pressure" not in kinds
    # Two transient point groups (load, io) were dropped, and the drop is
    # surfaced rather than silently swallowed.
    assert analysis.suppressed_transient_count == 2
    assert any("suppressed" in caveat for caveat in analysis.caveats)


def test_blocked_task_pressure_requires_real_backlog_not_one_task(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute in range(4):
            _metric(conn, minute, dstate_task_count=3)

    analysis = analyze_machine_episodes(path=db)

    # 2-3 D-state tasks is normal on a busy host; below the floor of 10 it must
    # not manufacture an episode.
    assert "blocked_task_pressure" not in {e.kind for e in analysis.episodes}


def test_system_stall_is_distinct_from_some_pressure(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute in range(4):
            # full PSI high, some PSI quiet: the everything-stalled freeze signal
            # without routine per-resource pressure.
            _metric(conn, minute, io_psi_some_avg10=0, io_psi_full_avg10=30)

    analysis = analyze_machine_episodes(path=db)
    kinds = {episode.kind for episode in analysis.episodes}

    assert "system_stall" in kinds
    assert "io_pressure" not in kinds


def test_low_available_memory_alone_is_not_pressure(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute in range(4):
            # Low free memory but no reclaim PSI and no swap: Linux caching, not
            # stall. Must not emit memory_pressure or swap_pressure.
            _metric(conn, minute, mem_avail_mb=500, memory_psi_some_avg60=0, swap_used_mb=0)

    analysis = analyze_machine_episodes(path=db)
    kinds = {episode.kind for episode in analysis.episodes}

    assert "memory_pressure" not in kinds
    assert "swap_pressure" not in kinds


def test_gpu_power_capped_detected_from_power_limit(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute in range(4):
            _gpu(conn, minute, gpu_power_w=320.0, gpu_power_limit_w=320.0)
        # A well-below-limit sample must not contribute.
        _gpu(conn, 10, gpu_power_w=80.0, gpu_power_limit_w=320.0)

    analysis = analyze_machine_episodes(path=db)
    capped = [e for e in analysis.episodes if e.kind == "gpu_power_capped"]

    assert capped
    assert capped[0].sample_count == 4


def test_machine_episode_detector_reports_sparse_coverage_caveats(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    analysis = analyze_machine_episodes(path=db)

    assert analysis.episode_count == 0
    assert analysis.coverage.metric_samples == 0
    assert analysis.episodes == []
    assert "machine_metric_sample has no rows in this window" in analysis.caveats
    assert "no machine episodes crossed configured absolute or robust thresholds" in analysis.caveats


def test_machine_episode_detector_does_not_flag_idle_services_or_gpu_utilization(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        _metric(conn, 0, gpu_temp_c=55, gpu_util_pct=100, gpu_pcie_gen=4, gpu_pcie_width=16)
        conn.execute(
            """
            INSERT INTO machine_service_state (
                observed_at, host, unit, scope, active_state, sub_state, refresh_id
            ) VALUES (?, 'host', 'btrbk.service', 'system', 'inactive', 'dead', 'r1')
            """,
            [_ts(0)],
        )

    analysis = analyze_machine_episodes(path=db)

    assert {episode.kind for episode in analysis.episodes}.isdisjoint({
        "gpu_thermal",
        "gpu_power_or_thermal",
        "service_instability",
    })


def test_machine_episode_service_failure_evidence_is_not_inactive_wording(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_service_state (
                observed_at, host, unit, scope, active_state, sub_state, refresh_id
            ) VALUES (?, 'host', 'postgresql.service', 'system', 'failed', 'failed', 'r1')
            """,
            [_ts(0)],
        )

    analysis = analyze_machine_episodes(path=db)
    episode = next(episode for episode in analysis.episodes if episode.kind == "service_instability")

    assert episode.evidence[0].threshold == "not failed"
    assert episode.evidence[0].reason == "sampled service state is failed"
    assert episode.confidence == 0.95


def test_machine_episode_detector_keeps_gpu_link_states_separate(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute, gen in ((0, 4), (1, 1), (2, 2)):
            _metric(conn, minute, gpu_util_pct=50, gpu_pcie_gen=gen, gpu_pcie_width=16)

    analysis = analyze_machine_episodes(path=db)
    link_episodes = [episode for episode in analysis.episodes if episode.kind == "gpu_link_regime"]

    # gpu_link_regime is event-exempt: single-sample link states still surface.
    assert {episode.subject for episode in link_episodes} == {"gen1x16", "gen2x16"}
    for episode in link_episodes:
        assert episode.payload is not None
        assert f"gen{episode.payload['gpu_pcie_gen']}x{episode.payload['gpu_pcie_width']}" == episode.subject


def test_machine_episode_kinds_are_defined(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute in range(4):
            _metric(conn, minute, load_1m=30)

    analysis = analyze_machine_episodes(path=db)
    definitions = {definition.kind: definition for definition in analysis.kind_definitions}

    for kind in ("load_pressure", "memory_pressure", "swap_pressure", "io_pressure",
                 "system_stall", "blocked_task_pressure", "gpu_thermal", "gpu_power_capped"):
        assert kind in definitions
    assert "gpu_power_or_thermal" not in definitions
    assert "not CPU saturation" in definitions["load_pressure"].interpretation_boundary
    assert "freeze" in definitions["system_stall"].interpretation_boundary.lower()
    assert "inactive/dead" in definitions["service_instability"].interpretation_boundary.lower()
    assert {episode.kind for episode in analysis.episodes}.issubset(definitions)


def test_machine_episode_severity_is_metric_scaled(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute in range(4):
            _metric(conn, minute, load_1m=22, dstate_task_count=11, io_psi_some_avg10=12)

    analysis = analyze_machine_episodes(path=db)
    by_kind = {episode.kind: episode for episode in analysis.episodes}

    assert 0 < by_kind["load_pressure"].severity < 0.1
    assert 0 < by_kind["blocked_task_pressure"].severity < 0.1
    assert 0 < by_kind["io_pressure"].severity < 0.1
