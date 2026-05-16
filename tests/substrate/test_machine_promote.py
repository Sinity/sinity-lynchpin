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
        host="sinnix-prime",
        workload="sinex.xtask",
        command=("xtask", "test"),
        cwd="/realm/project/sinex",
        started_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 12, 12, 1, tzinfo=timezone.utc),
        exit_status=0,
        service_profile="full",
        cache_profile="warm",
        planned_treatment={"turbo": "on"},
        git_root="/realm/project/sinex",
        git_head="abc123",
        git_branch="master",
        git_dirty=True,
        pre_state={"cpu": {"governor": "performance"}},
        post_state={"cpu": {"governor": "performance"}},
        notes=("smoke",),
        manifest_path=tmp_path / "run-1" / "manifest.json",
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_machine_experiment_runs(conn, refresh_id="r1", runs=[run]) == 1
        loaded = load_machine_experiment_runs(conn, refresh_id="r1")

    assert len(loaded) == 1
    assert loaded[0]["run_id"] == "run-1"
    assert loaded[0]["command"] == ["xtask", "test"]
    assert loaded[0]["planned_treatment"] == '{"turbo": "on"}'


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
