from datetime import datetime, timezone


def test_promote_machine_metric_samples_round_trip(tmp_path):
    from lynchpin.sources.machine import MachineMetricSample
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import promote_machine_metric_samples
    from lynchpin.substrate.machine import load_machine_metric_samples

    db = tmp_path / "sub.duckdb"
    sample = MachineMetricSample(
        observed_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        boot_id="boot-a",
        source="machine.telemetry",
        source_schema_version=1,
        cpu_package_w=16.5,
        gpu_power_w=28.0,
        mem_avail_mb=2048,
        swap_used_mb=1536,
        gpu_pcie_gen=1,
        gpu_pcie_width=16,
        io_psi_some_avg60=0.3,
        io_psi_some_avg300=0.4,
        io_psi_some_total_us=12345.0,
        cpu_psi_some_avg60=0.1,
        memory_psi_full_total_us=67890.0,
        gap_codes=("fan.hwmon_unavailable",),
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_machine_metric_samples(conn, refresh_id="r1", samples=[sample]) == 1
        row = conn.execute(
            """
            SELECT host, cpu_package_w, gpu_power_w, gpu_pcie_gen,
                   mem_avail_mb, swap_used_mb,
                   io_psi_some_avg60, cpu_psi_some_avg60,
                   memory_psi_full_total_us, gap_codes
            FROM machine_metric_sample
            WHERE refresh_id = 'r1'
            """
        ).fetchone()
        loaded = load_machine_metric_samples(conn, refresh_id="r1")

    assert row[0] == "sinnix-prime"
    assert row[1] == 16.5
    assert row[2] == 28.0
    assert row[3] == 1
    assert row[4] == 2048
    assert row[5] == 1536
    assert row[6] == 0.3
    assert row[7] == 0.1
    assert row[8] == 67890.0
    assert row[9] == ["fan.hwmon_unavailable"]
    assert len(loaded) == 1
    assert loaded[0].host == "sinnix-prime"
    assert loaded[0].swap_used_mb == 1536
    assert loaded[0].io_psi_some_total_us == 12345.0
    assert loaded[0].gap_codes == ("fan.hwmon_unavailable",)


def test_promote_machine_experiment_runs_round_trip(tmp_path):
    from lynchpin.sources.machine_experiments import MachineExperimentRun
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import promote_machine_experiment_runs
    from lynchpin.substrate.machine import load_machine_experiment_runs

    db = tmp_path / "sub.duckdb"
    run = MachineExperimentRun(
        run_id="run-1",
        run_group_id="grp1",
        host="sinnix-prime",
        workload="sinex.xtask",
        command=("xtask", "test"),
        cwd="/realm/project/sinex",
        started_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 12, 12, 1, tzinfo=timezone.utc),
        monotonic_started_ns=1,
        monotonic_ended_ns=60_000_000_000,
        exit_status=0,
        execution_outcome={"status": "success"},
        service_profile="full",
        cache_profile="warm",
        measurement_context={"host_boot_id": "boot1"},
        planned_treatment={"turbo": "on"},
        nix_internal_json_path="/tmp/run-1/nix-internal-json.ndjson",
        git_root="/realm/project/sinex",
        git_head="abc123",
        git_branch="master",
        git_dirty=True,
        pre_state={"cpu": {"governor": "performance"}},
        post_state={"cpu": {"governor": "performance"}},
        notes=("smoke",),
        validation_status="invalid",
        validation_issues=("fixture issue",),
        validation_warnings=("fixture warning",),
        manifest_validation={"valid": False, "issues": ["fixture issue"]},
        manifest_path=tmp_path / "run-1" / "manifest.json",
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_machine_experiment_runs(conn, refresh_id="r1", runs=[run]) == 1
        loaded = load_machine_experiment_runs(conn, refresh_id="r1")

    assert len(loaded) == 1
    assert loaded[0]["run_id"] == "run-1"
    assert loaded[0]["run_group_id"] == "grp1"
    assert loaded[0]["command"] == ["xtask", "test"]
    assert loaded[0]["monotonic_started_ns"] == 1
    assert loaded[0]["monotonic_ended_ns"] == 60_000_000_000
    assert loaded[0]["execution_outcome"] == '{"status": "success"}'
    assert loaded[0]["measurement_context"] == '{"host_boot_id": "boot1"}'
    assert loaded[0]["planned_treatment"] == '{"turbo": "on"}'
    assert loaded[0]["nix_internal_json_path"] == "/tmp/run-1/nix-internal-json.ndjson"
    assert loaded[0]["validation_status"] == "invalid"
    assert loaded[0]["validation_issues"] == ["fixture issue"]
    assert loaded[0]["validation_warnings"] == ["fixture warning"]
    assert loaded[0]["manifest_validation"] == '{"valid": false, "issues": ["fixture issue"]}'


def test_promote_machine_gpu_samples_round_trip(tmp_path):
    from lynchpin.sources.machine import MachineGpuSample
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import promote_machine_gpu_samples
    from lynchpin.substrate.machine import load_machine_gpu_samples

    db = tmp_path / "sub.duckdb"
    sample = MachineGpuSample(
        observed_at=datetime(2026, 5, 12, 12, 0, 1, tzinfo=timezone.utc),
        host="sinnix-prime",
        boot_id="boot-a",
        source="machine.telemetry.gpu",
        gpu_power_w=30.0,
        gpu_power_limit_w=320.0,
        gpu_temp_c=41.0,
        gpu_fan_pct=0.0,
        gpu_util_pct=4.0,
        gpu_mem_util_pct=2.0,
        gpu_clock_mhz=210.0,
        gpu_mem_clock_mhz=405.0,
        gpu_pstate="P8",
        gpu_pcie_gen=4,
        gpu_pcie_width=16,
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_machine_gpu_samples(conn, refresh_id="r1", samples=[sample]) == 1
        loaded = load_machine_gpu_samples(conn, refresh_id="r1")

    assert len(loaded) == 1
    assert loaded[0].gpu_power_w == 30.0
    assert loaded[0].gpu_pcie_gen == 4
    assert loaded[0].gpu_pcie_width == 16


def test_promote_machine_service_states_round_trip(tmp_path):
    from lynchpin.sources.machine import MachineServiceState
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import promote_machine_service_states
    from lynchpin.substrate.machine import load_machine_service_states

    db = tmp_path / "sub.duckdb"
    state = MachineServiceState(
        observed_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        boot_id="boot-a",
        unit="polylogued.service",
        scope="user",
        active_state="active",
        sub_state="running",
        main_pid=42,
        control_group="/user.slice/user-1000.slice",
        memory_current_bytes=1234,
        cpu_usage_nsec=5678,
        io_read_bytes=90,
        io_write_bytes=12,
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_machine_service_states(conn, refresh_id="r1", states=[state]) == 1
        loaded = load_machine_service_states(conn, refresh_id="r1")

    assert len(loaded) == 1
    assert loaded[0].unit == "polylogued.service"
    assert loaded[0].scope == "user"
    assert loaded[0].memory_current_bytes == 1234


def test_machine_service_state_summary_reports_counter_deltas(tmp_path):
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import load_machine_service_state_summary

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_service_state (
                observed_at, host, unit, scope, active_state, sub_state,
                memory_current_bytes, cpu_usage_nsec, io_read_bytes,
                io_write_bytes, refresh_id
            ) VALUES
                (?, 'host', 'below.service', 'system', 'active', 'running', 100, 1000, 50, 10, 'r1'),
                (?, 'host', 'below.service', 'system', 'active', 'running', 200, 1750, 90, 25, 'r1')
            """,
            [
                datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 12, 12, 1, tzinfo=timezone.utc),
            ],
        )
        rows = load_machine_service_state_summary(conn, refresh_id="r1")

    assert len(rows) == 1
    row = rows[0]
    assert row[5] == 200
    assert row[6] == 750
    assert row[7] == 40
    assert row[8] == 15
    assert row[11] == 1750


def test_promote_machine_network_samples_round_trip(tmp_path):
    from lynchpin.sources.machine import MachineNetworkSample
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import promote_machine_network_samples
    from lynchpin.substrate.machine import load_machine_network_samples

    db = tmp_path / "sub.duckdb"
    sample = MachineNetworkSample(
        observed_at=datetime(2026, 5, 12, 12, 5, tzinfo=timezone.utc),
        host="sinnix-prime",
        boot_id="boot-a",
        source_schema_version=1,
        interface="enp6s0",
        gateway_ip="192.168.1.1",
        ping={"gateway": {"avg_ms": 0.5, "loss": 0}},
        bloat=None,
        iface={"rx_bytes": 10},
        nic={"speed_mbps": 2500, "link": "yes"},
        tcp={"retrans": 1},
        dns_ms=2,
        pmtu_1492=True,
        conntrack={"count": 5, "max": 262144},
        gap_codes=(),
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_machine_network_samples(conn, refresh_id="r1", samples=[sample]) == 1
        loaded = load_machine_network_samples(conn, refresh_id="r1")

    assert len(loaded) == 1
    assert loaded[0].interface == "enp6s0"
    assert loaded[0].pmtu_1492 is True


def test_load_bufferbloat_daily_filters_refresh_id(tmp_path):
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import load_bufferbloat_daily

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for refresh_id, avg_ms in (("old-rid", 100.0), ("new-rid", 10.0)):
            conn.execute(
                """
                INSERT INTO machine_network_sample (
                    observed_at, host, boot_id, source_schema_version,
                    interface, gateway_ip, ping, bloat, iface, nic, tcp,
                    dns_ms, pmtu_1492, conntrack, gap_codes, refresh_id
                ) VALUES (?, 'sinnix-prime', 'boot-a', 1,
                    'enp6s0', '192.168.1.1', '{}', ?, '{}', '{}', '{}',
                    NULL, NULL, '{}', [], ?)
                """,
                [
                    datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
                    {"avg_ms": avg_ms, "loss": 0.0},
                    refresh_id,
                ],
            )

        rows = load_bufferbloat_daily(conn, refresh_id="new-rid")

    assert len(rows) == 1
    assert rows[0][2] == 1
    assert rows[0][3] == 10.0
