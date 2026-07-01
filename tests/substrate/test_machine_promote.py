from datetime import datetime, timezone


def test_promote_machine_metric_samples_round_trip(tmp_path):
    from lynchpin.sources.machine import MachineMetricSample
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import promote_machine_metric_samples
    from lynchpin.substrate.machine import load_machine_metric_samples
    from lynchpin.substrate.machine import load_machine_memory_breakdown

    db = tmp_path / "sub.duckdb"
    sample = MachineMetricSample(
        observed_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        boot_id="boot-a",
        source="machine.telemetry",
        source_schema_version=1,
        cpu_package_w=16.5,
        gpu_power_w=28.0,
        mem_total_mb=32000,
        mem_used_mb=15000,
        mem_avail_mb=2048,
        mem_anon_mb=9000,
        mem_file_cache_mb=4200,
        mem_slab_reclaimable_mb=700,
        mem_slab_unreclaimable_mb=300,
        mem_dirty_mb=25,
        mem_writeback_mb=3,
        mem_shmem_mb=500,
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
                   mem_total_mb, mem_used_mb, mem_avail_mb, mem_anon_mb,
                   mem_file_cache_mb, mem_slab_reclaimable_mb,
                   mem_slab_unreclaimable_mb, mem_dirty_mb, mem_writeback_mb,
                   mem_shmem_mb, swap_used_mb,
                   io_psi_some_avg60, cpu_psi_some_avg60,
                   memory_psi_full_total_us, gap_codes
            FROM machine_metric_sample
            WHERE refresh_id = 'r1'
            """
        ).fetchone()
        loaded = load_machine_metric_samples(conn, refresh_id="r1")
        memory = load_machine_memory_breakdown(conn, refresh_id="r1", limit=10)

    assert row[0] == "sinnix-prime"
    assert row[1] == 16.5
    assert row[2] == 28.0
    assert row[3] == 1
    assert row[4] == 32000
    assert row[5] == 15000
    assert row[6] == 2048
    assert row[7] == 9000
    assert row[8] == 4200
    assert row[9] == 700
    assert row[10] == 300
    assert row[11] == 25
    assert row[12] == 3
    assert row[13] == 500
    assert row[14] == 1536
    assert row[15] == 0.3
    assert row[16] == 0.1
    assert row[17] == 67890.0
    assert row[18] == ["fan.hwmon_unavailable"]
    assert len(loaded) == 1
    assert loaded[0].host == "sinnix-prime"
    assert loaded[0].mem_anon_mb == 9000
    assert loaded[0].mem_file_cache_mb == 4200
    assert loaded[0].swap_used_mb == 1536
    assert loaded[0].io_psi_some_total_us == 12345.0
    assert loaded[0].gap_codes == ("fan.hwmon_unavailable",)
    assert len(memory) == 1
    assert memory[0]["mem_total_mb"] == 32000
    assert memory[0]["mem_used_mb"] == 15000
    assert memory[0]["mem_anon_mb"] == 9000
    assert memory[0]["mem_file_cache_mb"] == 4200
    assert memory[0]["mem_slab_reclaimable_mb"] == 700
    assert memory[0]["mem_slab_unreclaimable_mb"] == 300


def test_load_machine_memory_breakdown_filters_exact_timestamps_without_default_limit(tmp_path):
    from lynchpin.sources.machine import MachineMetricSample
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import load_machine_memory_breakdown
    from lynchpin.substrate.machine import promote_machine_metric_samples

    db = tmp_path / "sub.duckdb"

    def sample(hour: int) -> MachineMetricSample:
        return MachineMetricSample(
            observed_at=datetime(2026, 5, 12, hour, 0, tzinfo=timezone.utc),
            host="sinnix-prime",
            boot_id="boot-a",
            source="machine.telemetry",
            source_schema_version=4,
            mem_total_mb=32000,
            mem_used_mb=10_000 + hour,
            mem_avail_mb=20_000 - hour,
        )

    with connect(db) as conn:
        apply_schema(conn)
        assert promote_machine_metric_samples(
            conn,
            refresh_id="r1",
            samples=[sample(hour) for hour in range(6)],
        ) == 6
        rows = load_machine_memory_breakdown(
            conn,
            refresh_id="r1",
            start=datetime(2026, 5, 12, 2, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 12, 4, 0, tzinfo=timezone.utc),
        )

    assert [row["mem_used_mb"] for row in rows] == [10004, 10003, 10002]


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
        memory_anon_bytes=1000,
        memory_file_bytes=200,
        memory_kernel_bytes=34,
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
    assert loaded[0].memory_anon_bytes == 1000
    assert loaded[0].memory_file_bytes == 200
    assert loaded[0].memory_kernel_bytes == 34


def test_promote_machine_process_io_delta_samples_round_trip(tmp_path):
    from lynchpin.sources.machine import MachineProcessIODeltaSample
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import load_machine_process_io_delta_samples
    from lynchpin.substrate.machine import promote_machine_process_io_delta_samples

    db = tmp_path / "sub.duckdb"
    sample = MachineProcessIODeltaSample(
        observed_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        boot_id="boot-a",
        source_schema_version=4,
        interval_s=10.0,
        pid=123,
        process_start_time_ticks=456789,
        comm="rustc",
        exe="/nix/store/rustc/bin/rustc",
        cgroup="/user.slice/user-1000.slice/session.scope",
        unit="session.scope",
        scope="user",
        command_line="/nix/store/rustc/bin/rustc --crate-name demo",
        read_bytes_delta=1_048_576,
        write_bytes_delta=2_097_152,
        cancelled_write_bytes_delta=0,
        read_chars_delta=4096,
        write_chars_delta=8192,
        read_syscalls_delta=11,
        write_syscalls_delta=22,
        total_bytes_delta=3_145_728,
        total_syscalls_delta=33,
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert (
            promote_machine_process_io_delta_samples(
                conn, refresh_id="r1", samples=[sample]
            )
            == 1
        )
        loaded = load_machine_process_io_delta_samples(conn, refresh_id="r1")

    assert len(loaded) == 1
    assert loaded[0].comm == "rustc"
    assert loaded[0].unit == "session.scope"
    assert loaded[0].command_line == "/nix/store/rustc/bin/rustc --crate-name demo"
    assert loaded[0].total_bytes_delta == 3_145_728
    assert loaded[0].total_syscalls_delta == 33


def test_promote_machine_process_memory_samples_round_trip(tmp_path):
    from lynchpin.sources.machine import MachineProcessMemorySample
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import load_machine_process_memory_samples
    from lynchpin.substrate.machine import promote_machine_process_memory_samples

    db = tmp_path / "sub.duckdb"
    sample = MachineProcessMemorySample(
        observed_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        boot_id="boot-a",
        source_schema_version=4,
        pid=123,
        process_start_time_ticks=456789,
        comm="rustc",
        exe="/nix/store/rustc/bin/rustc",
        cgroup="/user.slice/user-1000.slice/session.scope",
        unit="session.scope",
        scope="user",
        command_line="/nix/store/rustc/bin/rustc --crate-name demo",
        rss_kb=409600,
        pss_kb=307200,
        pss_anon_kb=204800,
        pss_file_kb=81920,
        pss_shmem_kb=20480,
        private_clean_kb=10240,
        private_dirty_kb=194560,
        shared_clean_kb=40960,
        shared_dirty_kb=61440,
        swap_kb=0,
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert (
            promote_machine_process_memory_samples(
                conn, refresh_id="r1", samples=[sample]
            )
            == 1
        )
        loaded = load_machine_process_memory_samples(conn, refresh_id="r1")

    assert len(loaded) == 1
    assert loaded[0].comm == "rustc"
    assert loaded[0].unit == "session.scope"
    assert loaded[0].pss_kb == 307200
    assert loaded[0].pss_anon_kb == 204800
    assert loaded[0].private_dirty_kb == 194560


def test_promote_machine_cgroup_memory_samples_round_trip(tmp_path):
    from lynchpin.sources.machine import MachineCgroupMemorySample
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import load_machine_cgroup_memory_samples
    from lynchpin.substrate.machine import promote_machine_cgroup_memory_samples

    db = tmp_path / "sub.duckdb"
    sample = MachineCgroupMemorySample(
        observed_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        boot_id="boot-a",
        source_schema_version=4,
        label="system.nix-build",
        scope="system",
        control_group="/nix.slice/nix-build.slice",
        memory_current_bytes=104857600,
        memory_peak_bytes=209715200,
        memory_swap_current_bytes=0,
        memory_swap_peak_bytes=0,
        memory_high_bytes=19327352832,
        memory_max_bytes=25769803776,
        memory_anon_bytes=73400320,
        memory_file_bytes=20971520,
        memory_kernel_bytes=8388608,
        memory_slab_bytes=4194304,
        memory_sock_bytes=0,
        memory_shmem_bytes=0,
        memory_swapcached_bytes=0,
        memory_zswap_bytes=0,
        memory_zswapped_bytes=0,
        cgroup_populated=1,
        cgroup_frozen=1,
        cgroup_freeze=1,
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert (
            promote_machine_cgroup_memory_samples(
                conn, refresh_id="r1", samples=[sample]
            )
            == 1
        )
        loaded = load_machine_cgroup_memory_samples(conn, refresh_id="r1")

    assert len(loaded) == 1
    assert loaded[0].label == "system.nix-build"
    assert loaded[0].memory_anon_bytes == 73400320
    assert loaded[0].cgroup_frozen == 1


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
                memory_current_bytes, memory_anon_bytes, memory_file_bytes,
                memory_kernel_bytes, cpu_usage_nsec, io_read_bytes,
                io_write_bytes, refresh_id
            ) VALUES
                (?, 'host', 'below.service', 'system', 'active', 'running', 100, 70, 20, 10, 1000, 50, 10, 'r1'),
                (?, 'host', 'below.service', 'system', 'active', 'running', 200, 120, 50, 30, 1750, 90, 25, 'r1')
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
    assert row[6] == 120
    assert row[7] == 50
    assert row[8] == 30
    assert row[9] == 750
    assert row[10] == 40
    assert row[11] == 15
    assert row[14] == 1750


def test_machine_pressure_explainer_joins_metric_service_and_process_io(tmp_path):
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import load_machine_pressure_explainer

    db = tmp_path / "sub.duckdb"
    observed_at = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, boot_id, source, source_schema_version,
                mem_total_mb, mem_used_mb, mem_avail_mb, mem_anon_mb,
                mem_file_cache_mb, mem_slab_reclaimable_mb,
                mem_slab_unreclaimable_mb, mem_dirty_mb, mem_writeback_mb,
                mem_shmem_mb, swap_used_mb,
                io_psi_some_avg10, io_psi_some_avg60,
                io_psi_full_avg10, io_psi_full_avg60,
                memory_psi_some_avg60, memory_psi_full_avg60,
                refresh_id
            ) VALUES (
                ?, 'host', 'boot', 'machine.telemetry', 4,
                32000, 18000, 14000, 8000,
                16000, 1500,
                500, 2, 0,
                200, 256,
                5.0, 8.0,
                2.0, 3.0,
                0.1, 0.0,
                'r1'
            )
            """,
            [observed_at],
        )
        conn.execute(
            """
            INSERT INTO machine_service_state (
                observed_at, host, boot_id, unit, scope, active_state, sub_state,
                memory_current_bytes, memory_anon_bytes, memory_file_bytes,
                memory_kernel_bytes, refresh_id
            ) VALUES (
                ?, 'host', 'boot', 'transmission.service', 'system', 'active', 'running',
                2097152000, 104857600, 1887436800, 104857600, 'r1'
            )
            """,
            [observed_at],
        )
        conn.execute(
            """
            INSERT INTO machine_process_io_delta_sample (
                observed_at, host, boot_id, source_schema_version, interval_s,
                pid, process_start_time_ticks, comm, unit, scope, command_line,
                read_bytes_delta, write_bytes_delta, cancelled_write_bytes_delta,
                read_chars_delta, write_chars_delta,
                read_syscalls_delta, write_syscalls_delta,
                total_bytes_delta, total_syscalls_delta, refresh_id
            ) VALUES (
                ?, 'host', 'boot', 4, 10.0,
                123, 456, 'codex', 'session.scope', 'user', 'codex exec',
                104857600, 209715200, 0,
                4096, 8192,
                10, 20,
                314572800, 30, 'r1'
            )
            """,
            [observed_at],
        )
        conn.execute(
            """
            INSERT INTO machine_process_memory_sample (
                observed_at, host, boot_id, source_schema_version,
                pid, process_start_time_ticks, comm, unit, scope, command_line,
                rss_kb, pss_kb, pss_anon_kb, pss_file_kb, pss_shmem_kb,
                private_clean_kb, private_dirty_kb,
                shared_clean_kb, shared_dirty_kb, swap_kb, refresh_id
            ) VALUES (
                ?, 'host', 'boot', 4,
                123, 456, 'codex', 'session.scope', 'user', 'codex exec',
                409600, 307200, 204800, 81920, 20480,
                10240, 194560,
                40960, 61440, 0, 'r1'
            )
            """,
            [observed_at],
        )

        windows = load_machine_pressure_explainer(conn, refresh_id="r1", focus="io")

    assert len(windows) == 1
    assert windows[0]["metric"]["mem_file_cache_mb"] == 16000
    assert "reclaimable cache/slab exceeds anonymous process memory" in windows[0]["notes"]
    assert windows[0]["top_services_by_memory"][0]["unit"] == "transmission.service"
    assert windows[0]["top_services_by_memory"][0]["max_file_mib"] == 1800
    assert windows[0]["top_process_io_deltas"][0]["comm"] == "codex"
    assert windows[0]["top_process_io_deltas"][0]["total_mib_delta"] == 300
    assert windows[0]["top_processes_by_pss"][0]["comm"] == "codex"
    assert windows[0]["top_processes_by_pss"][0]["pss_mib"] == 300


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
