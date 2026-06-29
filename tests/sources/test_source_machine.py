import sqlite3
import json
from datetime import date
from types import SimpleNamespace

from lynchpin.sources import machine


def test_machine_source_reads_live_sqlite(monkeypatch, tmp_path):
    db = tmp_path / "telemetry.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE metric_sample (
              observed_at TEXT, host TEXT, boot_id TEXT, schema_version INTEGER,
              cpu_package_w REAL, cpu_core_w REAL, cpu_pkg_c REAL, cpu_max_core_c REAL,
              gpu_power_w REAL, gpu_fan_pct REAL, gpu_temp_c REAL, gpu_util_pct REAL,
              gpu_pstate TEXT, gpu_pcie_gen INTEGER, gpu_pcie_width INTEGER,
              load_1m REAL, mem_avail_mb INTEGER, swap_used_mb INTEGER, io_psi_some_avg10 REAL,
              io_psi_full_avg10 REAL, latency_oversleep_ms REAL,
              dstate_task_count INTEGER, gap_codes_json TEXT
            );
            INSERT INTO metric_sample VALUES (
              '2026-05-12T12:00:00+00:00', 'sinnix-prime', 'boot-a', 1,
              16.5, 15.2, 36.0, 45.0, 28.0, 0.0, 40.0, 3.0,
              'P8', 1, 16, 0.8, 48000, 512, 0.2, 0.0, 3.5, 0,
              '["fan.hwmon_unavailable"]'
            );
            INSERT INTO metric_sample VALUES (
              '2026-05-12T12:10:00+00:00', 'sinnix-prime', 'boot-a', 1,
              20.5, 19.2, 38.0, 48.0, 32.0, 0.0, 42.0, 8.0,
              'P8', 1, 16, 1.8, 46000, 256, 0.3, 0.1, 4.5, 1,
              '[]'
            );
            CREATE TABLE service_state (
              observed_at TEXT, host TEXT, boot_id TEXT, unit TEXT, scope TEXT,
              active_state TEXT, sub_state TEXT, main_pid INTEGER, control_group TEXT,
              memory_current_bytes INTEGER, cpu_usage_nsec INTEGER,
              io_read_bytes INTEGER, io_write_bytes INTEGER
            );
            INSERT INTO service_state VALUES (
              '2026-05-12T12:00:00+00:00', 'sinnix-prime', 'boot-a',
              'polylogued.service', 'user', 'active', 'running', 42,
              '/user.slice/user-1000.slice', 1234, 5678, 90, 12
            );
            CREATE TABLE block_device_sample (
              observed_at TEXT, host TEXT, boot_id TEXT, schema_version INTEGER,
              major INTEGER, minor INTEGER, device TEXT,
              reads_completed INTEGER, reads_merged INTEGER, sectors_read INTEGER,
              read_time_ms INTEGER, writes_completed INTEGER, writes_merged INTEGER,
              sectors_written INTEGER, write_time_ms INTEGER,
              ios_in_progress INTEGER, io_time_ms INTEGER, weighted_io_time_ms INTEGER,
              discards_completed INTEGER, discards_merged INTEGER,
              sectors_discarded INTEGER, discard_time_ms INTEGER,
              flushes_completed INTEGER, flush_time_ms INTEGER
            );
            INSERT INTO block_device_sample VALUES (
              '2026-05-12T12:00:00+00:00', 'sinnix-prime', 'boot-a', 2,
              259, 0, 'nvme0n1',
              10, 1, 2000, 30, 20, 2, 4000, 60,
              0, 70, 120, 0, 0, 0, 0, 3, 4
            );
            CREATE TABLE service_cgroup_io_sample (
              observed_at TEXT, host TEXT, boot_id TEXT, schema_version INTEGER,
              unit TEXT, scope TEXT, control_group TEXT, major INTEGER, minor INTEGER,
              rbytes INTEGER, wbytes INTEGER, rios INTEGER, wios INTEGER,
              dbytes INTEGER, dios INTEGER
            );
            INSERT INTO service_cgroup_io_sample VALUES (
              '2026-05-12T12:00:00+00:00', 'sinnix-prime', 'boot-a', 2,
              'transmission.service', 'system',
              '/system.slice/transmission.service', 8, 0,
              4096, 8192, 4, 8, 0, 0
            );
            CREATE TABLE service_cgroup_pressure_sample (
              observed_at TEXT, host TEXT, boot_id TEXT, schema_version INTEGER,
              unit TEXT, scope TEXT, control_group TEXT,
              cpu_some_avg10 REAL, cpu_some_avg60 REAL, cpu_some_avg300 REAL,
              cpu_some_total_us REAL,
              io_some_avg10 REAL, io_some_avg60 REAL, io_some_avg300 REAL,
              io_some_total_us REAL,
              io_full_avg10 REAL, io_full_avg60 REAL, io_full_avg300 REAL,
              io_full_total_us REAL,
              memory_some_avg10 REAL, memory_some_avg60 REAL,
              memory_some_avg300 REAL, memory_some_total_us REAL,
              memory_full_avg10 REAL, memory_full_avg60 REAL,
              memory_full_avg300 REAL, memory_full_total_us REAL
            );
            INSERT INTO service_cgroup_pressure_sample VALUES (
              '2026-05-12T12:00:00+00:00', 'sinnix-prime', 'boot-a', 2,
              'transmission.service', 'system',
              '/system.slice/transmission.service',
              0.1, 0.2, 0.3, 10,
              1.1, 1.2, 1.3, 20,
              2.1, 2.2, 2.3, 30,
              3.1, 3.2, 3.3, 40,
              4.1, 4.2, 4.3, 50
            );
            CREATE TABLE process_io_delta_sample (
              observed_at TEXT, host TEXT, boot_id TEXT, schema_version INTEGER,
              interval_s REAL, pid INTEGER, process_start_time_ticks INTEGER,
              comm TEXT, exe TEXT, cgroup TEXT, unit TEXT, scope TEXT,
              read_bytes_delta INTEGER, write_bytes_delta INTEGER,
              cancelled_write_bytes_delta INTEGER,
              read_chars_delta INTEGER, write_chars_delta INTEGER,
              read_syscalls_delta INTEGER, write_syscalls_delta INTEGER,
              total_bytes_delta INTEGER, total_syscalls_delta INTEGER
            );
            INSERT INTO process_io_delta_sample VALUES (
              '2026-05-12T12:00:10+00:00', 'sinnix-prime', 'boot-a', 3,
              10.0, 123, 456789, 'rustc', '/nix/store/rustc/bin/rustc',
              '/user.slice/user-1000.slice/session.scope',
              'session.scope', 'user',
              1048576, 2097152, 0, 4096, 8192, 11, 22, 3145728, 33
            );
            CREATE TABLE gpu_sample (
              observed_at TEXT, host TEXT, boot_id TEXT,
              gpu_power_w REAL, gpu_power_limit_w REAL, gpu_temp_c REAL,
              gpu_fan_pct REAL, gpu_util_pct REAL, gpu_mem_util_pct REAL,
              gpu_clock_mhz REAL, gpu_mem_clock_mhz REAL,
              gpu_pstate TEXT, gpu_pcie_gen INTEGER, gpu_pcie_width INTEGER
            );
            INSERT INTO gpu_sample VALUES (
              '2026-05-12T12:00:01+00:00', 'sinnix-prime', 'boot-a',
              30.0, 320.0, 41.0, 0.0, 4.0, 2.0, 210.0, 405.0,
              'P8', 4, 16
            );
            CREATE TABLE network_sample (
              observed_at TEXT, host TEXT, boot_id TEXT, schema_version INTEGER,
              interface TEXT, gateway_ip TEXT, ping_json TEXT, bloat_json TEXT,
              iface_json TEXT, nic_json TEXT, tcp_json TEXT, dns_ms INTEGER,
              pmtu_1492 INTEGER, conntrack_json TEXT, gap_codes_json TEXT
            );
            INSERT INTO network_sample VALUES (
              '2026-05-12T12:05:00+00:00', 'sinnix-prime', 'boot-a', 1,
              'enp6s0', '192.168.1.1',
              '{"gateway":{"avg_ms":0.5,"loss":0}}', null,
              '{"rx_bytes":10}', '{"speed_mbps":2500,"link":"yes"}',
              '{"retrans":1}', 2, 1, '{"count":5,"max":262144}', '[]'
            );
            """
        )
        conn.executescript(
            """
            ALTER TABLE metric_sample ADD COLUMN mem_total_mb INTEGER;
            ALTER TABLE metric_sample ADD COLUMN mem_used_mb INTEGER;
            ALTER TABLE metric_sample ADD COLUMN mem_anon_mb INTEGER;
            ALTER TABLE metric_sample ADD COLUMN mem_file_cache_mb INTEGER;
            ALTER TABLE metric_sample ADD COLUMN mem_slab_reclaimable_mb INTEGER;
            ALTER TABLE metric_sample ADD COLUMN mem_slab_unreclaimable_mb INTEGER;
            ALTER TABLE metric_sample ADD COLUMN mem_dirty_mb INTEGER;
            ALTER TABLE metric_sample ADD COLUMN mem_writeback_mb INTEGER;
            ALTER TABLE metric_sample ADD COLUMN mem_shmem_mb INTEGER;
            UPDATE metric_sample
            SET schema_version = 4,
                mem_total_mb = 32000,
                mem_used_mb = 15000,
                mem_anon_mb = 9000,
                mem_file_cache_mb = 4200,
                mem_slab_reclaimable_mb = 700,
                mem_slab_unreclaimable_mb = 300,
                mem_dirty_mb = 25,
                mem_writeback_mb = 3,
                mem_shmem_mb = 500
            WHERE observed_at = '2026-05-12T12:00:00+00:00';

            ALTER TABLE service_state ADD COLUMN memory_anon_bytes INTEGER;
            ALTER TABLE service_state ADD COLUMN memory_file_bytes INTEGER;
            ALTER TABLE service_state ADD COLUMN memory_kernel_bytes INTEGER;
            ALTER TABLE service_state ADD COLUMN memory_slab_bytes INTEGER;
            ALTER TABLE service_state ADD COLUMN memory_sock_bytes INTEGER;
            ALTER TABLE service_state ADD COLUMN memory_shmem_bytes INTEGER;
            ALTER TABLE service_state ADD COLUMN memory_swapcached_bytes INTEGER;
            ALTER TABLE service_state ADD COLUMN memory_zswap_bytes INTEGER;
            ALTER TABLE service_state ADD COLUMN memory_zswapped_bytes INTEGER;
            UPDATE service_state
            SET memory_anon_bytes = 1000,
                memory_file_bytes = 200,
                memory_kernel_bytes = 34,
                memory_slab_bytes = 30,
                memory_sock_bytes = 2,
                memory_shmem_bytes = 20,
                memory_swapcached_bytes = 4,
                memory_zswap_bytes = 5,
                memory_zswapped_bytes = 6;

            ALTER TABLE process_io_delta_sample ADD COLUMN command_line TEXT;
            UPDATE process_io_delta_sample
            SET command_line = '/nix/store/rustc/bin/rustc --crate-name demo';
            """
        )
    monkeypatch.setattr(
        machine,
        "get_config",
        lambda: SimpleNamespace(machine_telemetry_db=db),
    )
    monkeypatch.setattr(machine, "default_route_interface", lambda: "enp6s0")

    ready = machine.readiness()
    assert ready.status == "ready"
    rows = list(machine.metric_samples(start=date(2026, 5, 12), end=date(2026, 5, 12)))
    assert len(rows) == 2
    assert rows[0].cpu_package_w == 16.5
    assert rows[0].gpu_pcie_gen == 1
    assert rows[0].source_schema_version == 4
    assert rows[0].mem_total_mb == 32000
    assert rows[0].mem_used_mb == 15000
    assert rows[0].mem_anon_mb == 9000
    assert rows[0].mem_file_cache_mb == 4200
    assert rows[0].mem_slab_reclaimable_mb == 700
    assert rows[0].mem_slab_unreclaimable_mb == 300
    assert rows[0].mem_dirty_mb == 25
    assert rows[0].mem_writeback_mb == 3
    assert rows[0].mem_shmem_mb == 500
    assert rows[0].swap_used_mb == 512
    assert rows[0].gap_codes == ("fan.hwmon_unavailable",)
    latest = machine.latest_metric_sample()
    assert latest is not None
    assert latest.cpu_package_w == 20.5
    assert latest.dstate_task_count == 1
    states = list(
        machine.service_states(start=date(2026, 5, 12), end=date(2026, 5, 12))
    )
    assert len(states) == 1
    assert states[0].unit == "polylogued.service"
    assert states[0].scope == "user"
    assert states[0].memory_current_bytes == 1234
    assert states[0].memory_anon_bytes == 1000
    assert states[0].memory_file_bytes == 200
    assert states[0].memory_kernel_bytes == 34
    devices = list(
        machine.block_device_samples(start=date(2026, 5, 12), end=date(2026, 5, 12))
    )
    assert len(devices) == 1
    assert devices[0].device == "nvme0n1"
    assert devices[0].sectors_read == 2000
    assert devices[0].weighted_io_time_ms == 120
    cgroup_io = list(
        machine.service_cgroup_io_samples(
            start=date(2026, 5, 12), end=date(2026, 5, 12)
        )
    )
    assert len(cgroup_io) == 1
    assert cgroup_io[0].unit == "transmission.service"
    assert cgroup_io[0].rbytes == 4096
    assert cgroup_io[0].wios == 8
    cgroup_pressure = list(
        machine.service_cgroup_pressure_samples(
            start=date(2026, 5, 12), end=date(2026, 5, 12)
        )
    )
    assert len(cgroup_pressure) == 1
    assert cgroup_pressure[0].unit == "transmission.service"
    assert cgroup_pressure[0].io_full_avg10 == 2.1
    assert cgroup_pressure[0].memory_full_total_us == 50
    process_io = list(
        machine.process_io_delta_samples(
            start=date(2026, 5, 12), end=date(2026, 5, 12)
        )
    )
    assert len(process_io) == 1
    assert process_io[0].comm == "rustc"
    assert process_io[0].unit == "session.scope"
    assert process_io[0].command_line == "/nix/store/rustc/bin/rustc --crate-name demo"
    assert process_io[0].read_bytes_delta == 1048576
    assert process_io[0].total_syscalls_delta == 33
    gpu = list(machine.gpu_samples(start=date(2026, 5, 12), end=date(2026, 5, 12)))
    assert len(gpu) == 1
    assert gpu[0].gpu_power_w == 30.0
    assert gpu[0].gpu_pcie_gen == 4
    network = list(
        machine.network_samples(start=date(2026, 5, 12), end=date(2026, 5, 12))
    )
    assert len(network) == 1
    assert network[0].interface == "enp6s0"
    assert network[0].ping["gateway"]["avg_ms"] == 0.5
    assert network[0].pmtu_1492 is True


def test_machine_canonical_fallback_materializes(monkeypatch, tmp_path):
    calls = []
    missing_live = tmp_path / "missing.sqlite"
    product = tmp_path / "machine/processed/metric_sample.ndjson"
    product.parent.mkdir(parents=True)
    product.write_text(
        json.dumps(
            {
                "observed_at": "2026-05-12T12:00:00+00:00",
                "host": "sinnix-prime",
                "boot_id": "boot-a",
                "source": "machine.telemetry",
                "source_schema_version": 1,
                "cpu_package_w": 16.5,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        machine,
        "get_config",
        lambda: SimpleNamespace(machine_telemetry_db=missing_live, captures_root=tmp_path),
    )
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    rows = list(machine.metric_samples(start=date(2026, 5, 12), end=date(2026, 5, 12)))

    assert calls == [("machine", (date(2026, 5, 12), date(2026, 5, 13)))]
    assert [row.cpu_package_w for row in rows] == [16.5]


def test_block_device_samples_tolerate_old_live_sqlite(tmp_path):
    db = tmp_path / "telemetry.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE metric_sample (observed_at TEXT)")

    assert list(machine.block_device_samples(path=db)) == []
    assert list(machine.service_cgroup_io_samples(path=db)) == []
    assert list(machine.service_cgroup_pressure_samples(path=db)) == []
    assert list(machine.process_io_delta_samples(path=db)) == []


def test_network_samples_filter_stale_interfaces(monkeypatch, tmp_path):
    db = tmp_path / "telemetry.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE network_sample (
              observed_at TEXT, host TEXT, boot_id TEXT, schema_version INTEGER,
              interface TEXT, gateway_ip TEXT, ping_json TEXT, bloat_json TEXT,
              iface_json TEXT, nic_json TEXT, tcp_json TEXT, dns_ms INTEGER,
              pmtu_1492 INTEGER, conntrack_json TEXT, gap_codes_json TEXT
            );
            INSERT INTO network_sample VALUES (
              '2026-05-15T12:00:00+00:00', 'sinnix-prime', 'boot-a', 1,
              'enp6s0', '192.168.1.1',
              '{"gateway":{"avg_ms":0.5,"loss":0}}', null,
              '{"rx_bytes":0}', '{"link":"no"}', '{}', 2, 1, '{}', '[]'
            );
            INSERT INTO network_sample VALUES (
              '2026-05-15T12:05:00+00:00', 'sinnix-prime', 'boot-a', 1,
              'enp4s0', '192.168.1.1',
              '{"gateway":{"avg_ms":0.6,"loss":0}}', null,
              '{"rx_bytes":10}', '{"link":"yes"}', '{}', 2, 1, '{}', '[]'
            );
            """
        )
    monkeypatch.setattr(
        machine,
        "get_config",
        lambda: SimpleNamespace(machine_telemetry_db=db),
    )
    monkeypatch.setattr(machine, "default_route_interface", lambda: "enp4s0")

    network = list(
        machine.network_samples(start=date(2026, 5, 15), end=date(2026, 5, 15), path=db)
    )
    assert [sample.interface for sample in network] == ["enp4s0"]
