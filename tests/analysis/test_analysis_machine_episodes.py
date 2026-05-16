from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.analysis.machine.episodes import analyze_machine_episodes
from lynchpin.substrate.connection import apply_schema, connect


def test_machine_episode_detector_merges_multisource_pressure(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, mem_avail_mb, swap_used_mb, io_psi_some_avg10, io_psi_full_avg10,
                latency_oversleep_ms, dstate_task_count, gpu_temp_c, gpu_util_pct,
                gpu_pcie_gen, gpu_pcie_width, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 1, 32000, 0, 0, 0, 1, 0, 40, 5, 4, 16, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 28, 1800, 2048, 16, 2, 75, 2, 86, 98, 2, 16, ['collector.late'], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 31, 1700, 3072, 18, 3, 80, 2, 87, 99, 2, 16, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 2, tzinfo=timezone.utc),
            ],
        )
        conn.execute(
            """
            INSERT INTO machine_network_sample (
                observed_at, host, source_schema_version, interface, gateway_ip,
                ping, iface, nic, tcp, dns_ms, pmtu_1492, conntrack, gap_codes, refresh_id
            ) VALUES (?, 'host', 1, 'enp7s0', '192.0.2.1',
                '{"avg_ms":150,"loss_pct":2}', '{}', '{}', '{}', 250, true, '{}', [], 'r1')
            """,
            [datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc)],
        )
        conn.execute(
            """
            INSERT INTO machine_service_state (
                observed_at, host, unit, scope, active_state, sub_state,
                memory_current_bytes, refresh_id
            ) VALUES (?, 'host', 'polylogued.service', 'user', 'failed', 'failed', 10, 'r1')
            """,
            [datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc)],
        )

    analysis = analyze_machine_episodes(path=db)

    assert analysis.episode_count == len(analysis.episodes)
    kinds = {episode.kind for episode in analysis.episodes}
    assert {
        "load_pressure",
        "memory_pressure",
        "io_pressure",
        "scheduler_latency",
        "blocked_task_pressure",
        "gpu_power_or_thermal",
        "gpu_link_regime",
        "network_degraded",
        "service_instability",
    }.issubset(kinds)

    load = next(episode for episode in analysis.episodes if episode.kind == "load_pressure")
    assert load.sample_count == 2
    assert load.started_at.minute == 1
    assert load.ended_at.minute == 2
    assert {item.metric for item in load.evidence} >= {"load_1m"}
    assert any("collector.late" in caveat for caveat in load.caveats)
    memory = next(episode for episode in analysis.episodes if episode.kind == "memory_pressure")
    assert {item.metric for item in memory.evidence} >= {"mem_avail_mb", "swap_used_mb"}

    network = next(episode for episode in analysis.episodes if episode.kind == "network_degraded")
    assert network.subject == "enp7s0"
    assert {item.metric for item in network.evidence} >= {"dns_ms", "ping_loss_pct", "ping_rtt_ms"}


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
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                gpu_temp_c, gpu_util_pct, gpu_pcie_gen, gpu_pcie_width,
                gap_codes, refresh_id
            ) VALUES (?, 'host', 'machine.telemetry', 2, 55, 100, 4, 16, [], 'r1')
            """,
            [datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)],
        )
        conn.execute(
            """
            INSERT INTO machine_service_state (
                observed_at, host, unit, scope, active_state, sub_state, refresh_id
            ) VALUES (?, 'host', 'btrbk.service', 'system', 'inactive', 'dead', 'r1')
            """,
            [datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)],
        )

    analysis = analyze_machine_episodes(path=db)

    assert {episode.kind for episode in analysis.episodes}.isdisjoint({
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
            [datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)],
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
            conn.execute(
                """
                INSERT INTO machine_metric_sample (
                    observed_at, host, source, source_schema_version,
                    gpu_util_pct, gpu_pcie_gen, gpu_pcie_width, gap_codes, refresh_id
                ) VALUES (?, 'host', 'machine.telemetry', 2, 50, ?, 16, [], 'r1')
                """,
                [datetime(2026, 5, 1, 12, minute, tzinfo=timezone.utc), gen],
            )

    analysis = analyze_machine_episodes(path=db)
    link_episodes = [episode for episode in analysis.episodes if episode.kind == "gpu_link_regime"]

    assert {episode.subject for episode in link_episodes} == {"gen1x16", "gen2x16"}
    for episode in link_episodes:
        assert episode.payload is not None
        assert f"gen{episode.payload['gpu_pcie_gen']}x{episode.payload['gpu_pcie_width']}" == episode.subject


def test_machine_episode_kinds_are_defined(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, gap_codes, refresh_id
            ) VALUES (?, 'host', 'machine.telemetry', 2, 30, [], 'r1')
            """,
            [datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)],
        )

    analysis = analyze_machine_episodes(path=db)
    definitions = {definition.kind: definition for definition in analysis.kind_definitions}

    assert "load_pressure" in definitions
    assert "not CPU saturation" in definitions["load_pressure"].interpretation_boundary
    assert "blocked_task_pressure" in definitions
    assert "not scheduler latency" in definitions["blocked_task_pressure"].interpretation_boundary
    assert "inactive/dead" in definitions["service_instability"].interpretation_boundary.lower()
    assert {episode.kind for episode in analysis.episodes}.issubset(definitions)


def test_machine_episode_severity_is_metric_scaled(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, dstate_task_count, io_psi_full_avg10, gap_codes, refresh_id
                ) VALUES (?, 'host', 'machine.telemetry', 2, 22, 2, 2, [], 'r1')
            """,
            [datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)],
        )

    analysis = analyze_machine_episodes(path=db)
    by_kind = {episode.kind: episode for episode in analysis.episodes}

    assert 0 < by_kind["load_pressure"].severity < 0.1
    assert 0 < by_kind["blocked_task_pressure"].severity < 0.1
    assert 0 < by_kind["io_pressure"].severity < 0.1
    assert by_kind["load_pressure"].confidence == 0.65
