import sqlite3
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
    monkeypatch.setattr(
        machine,
        "get_config",
        lambda: SimpleNamespace(machine_telemetry_db=db),
    )
    monkeypatch.setattr(machine, "default_route_interface", lambda: "enp6s0")

    ready = machine.readiness()
    assert ready.status == "ready"
    rows = list(machine.metric_samples(start=date(2026, 5, 12), end=date(2026, 5, 12), path=db))
    assert len(rows) == 1
    assert rows[0].cpu_package_w == 16.5
    assert rows[0].gpu_pcie_gen == 1
    assert rows[0].swap_used_mb == 512
    assert rows[0].gap_codes == ("fan.hwmon_unavailable",)
    states = list(machine.service_states(start=date(2026, 5, 12), end=date(2026, 5, 12), path=db))
    assert len(states) == 1
    assert states[0].unit == "polylogued.service"
    assert states[0].scope == "user"
    assert states[0].memory_current_bytes == 1234
    gpu = list(machine.gpu_samples(start=date(2026, 5, 12), end=date(2026, 5, 12), path=db))
    assert len(gpu) == 1
    assert gpu[0].gpu_power_w == 30.0
    assert gpu[0].gpu_pcie_gen == 4
    network = list(machine.network_samples(start=date(2026, 5, 12), end=date(2026, 5, 12), path=db))
    assert len(network) == 1
    assert network[0].interface == "enp6s0"
    assert network[0].ping["gateway"]["avg_ms"] == 0.5
    assert network[0].pmtu_1492 is True


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

    network = list(machine.network_samples(start=date(2026, 5, 15), end=date(2026, 5, 15), path=db))
    assert [sample.interface for sample in network] == ["enp4s0"]
