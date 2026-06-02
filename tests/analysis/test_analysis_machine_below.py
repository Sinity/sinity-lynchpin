from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from lynchpin.analysis.machine.below import analyze_below_exports, export_live_below_window, failed_below_exports


def test_below_analysis_summarizes_bounded_exports(tmp_path):
    report = tmp_path / "20260312-154233-auto" / "report"
    report.mkdir(parents=True)
    (report / "below-system.csv").write_text(
        "\n".join(
            [
                "Datetime,Usage,IOWait,Available,OOM Kills,Running Procs,",
                "2026-03-12 15:42:34,2.00%,0.50%,30.0 GB,0,1,",
                "2026-03-12 15:42:35,4.00%,1.50%,29.0 GB,1,3,",
            ]
        )
        + "\n"
    )
    (report / "below-top-processes.csv").write_text(
        "\n".join(
            [
                "Datetime,Pid,Comm,State,CPU,RSS,Cmdline,",
                "2026-03-12 15:42:34,10,pytest,RUNNING,20.00%,100 MB,pytest -q,",
                "2026-03-12 15:42:35,10,pytest,RUNNING,30.00%,120 MB,pytest -q,",
            ]
        )
        + "\n"
    )
    (report / "below-top-cgroups.csv").write_text(
        "\n".join(
            [
                "Datetime,Name,Full Path,CPU Usage,Mem Total,CPU Some Pressure,Mem Pressure,RW Total,",
                "2026-03-12 15:42:34,user.slice,/user.slice,10.00%,1.0 GB,0.0%,0.0%,1 MB/s,",
                "2026-03-12 15:42:35,user.slice,/user.slice,12.00%,1.5 GB,0.0%,0.0%,1 MB/s,",
            ]
        )
        + "\n"
    )

    live_store = tmp_path / "below-store"
    live_store.mkdir()
    epoch = int(datetime(2026, 3, 12, tzinfo=timezone.utc).timestamp())
    (live_store / f"index_{epoch}").write_text("")
    (live_store / f"data_{epoch}").write_text("")

    analysis = analyze_below_exports(root=tmp_path, live_store=live_store, top_n=5)

    assert analysis.window_count == 1
    assert analysis.live_store.index_count == 1
    assert analysis.live_store.data_count == 1
    assert analysis.live_store.first_observed_at == datetime(2026, 3, 12, tzinfo=timezone.utc)
    assert analysis.live_store.last_observed_at == datetime(2026, 3, 13, tzinfo=timezone.utc)
    assert len(analysis.system) == 1
    assert analysis.top_process_count == 1
    assert analysis.top_cgroup_count == 1
    assert analysis.system[0].avg_cpu_pct == 3.0
    assert analysis.system[0].oom_kills == 1
    assert analysis.top_processes[0].key == "pytest -q"
    assert analysis.top_processes[0].first_observed_at is not None
    assert analysis.top_processes[0].last_observed_at is not None
    assert analysis.top_processes[0].max_rss_mb == 120.0
    assert analysis.top_cgroups[0].key == "/user.slice"
    assert analysis.top_cgroups[0].max_mem_total_mb == 1536.0


def test_export_live_below_window_writes_existing_bounded_csv_shape(monkeypatch, tmp_path):
    def fake_run(command, capture_output, text, timeout, check):
        assert capture_output is True
        assert text is True
        assert timeout == 30
        assert check is False
        kind = command[2]
        if kind == "system":
            stdout = "2026-06-01 18:18:16\t50.0\t2.5\t1073741824\t0\t7\t\n"
        elif kind == "process":
            stdout = "2026-06-01 18:18:16\t42\tpytest\tRUNNING\t40.0\t104857600\tpytest -q\t\n"
        elif kind == "cgroup":
            stdout = "2026-06-01 18:18:16\tuser.slice\t/user.slice\t20.0\t1073741824\t1.0\t0.5\t4096\t\n"
        else:
            raise AssertionError(kind)
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("lynchpin.analysis.machine.below.subprocess.run", fake_run)

    export = export_live_below_window(
        root=tmp_path,
        begin="2026-06-01 18:18:16",
        duration="3 sec",
        capture_id="cap",
        top_n=3,
        timeout_s=30,
    )
    analysis = analyze_below_exports(root=tmp_path, live_store=tmp_path / "missing-store", top_n=5)

    assert export.capture_id == "cap"
    assert export.system_rows == 1
    assert export.process_rows == 1
    assert export.cgroup_rows == 1
    assert export.errors == ()
    assert analysis.window_count == 1
    assert analysis.system[0].avg_cpu_pct == 50.0
    assert analysis.system[0].min_available_gb == 1.0
    assert analysis.top_processes[0].key == "pytest -q"
    assert analysis.top_processes[0].max_rss_mb == 100.0
    assert analysis.top_cgroups[0].key == "/user.slice"
    assert analysis.top_cgroups[0].max_mem_total_mb == 1024.0


def test_export_live_below_window_records_dump_errors(monkeypatch, tmp_path):
    def fake_run(command, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="No samples found\n")

    monkeypatch.setattr("lynchpin.analysis.machine.below.subprocess.run", fake_run)

    export = export_live_below_window(
        root=tmp_path,
        begin="2026-06-01 12:00:00",
        duration="3 sec",
        capture_id="empty",
        timeout_s=30,
    )

    assert export.system_rows == 0
    assert export.process_rows == 0
    assert export.cgroup_rows == 0
    assert len(export.errors) == 3
    assert (tmp_path / "empty" / "report" / "below-system.csv").read_text().startswith("Datetime,Usage")


def test_failed_below_exports_detects_header_only_system_export(tmp_path):
    report = tmp_path / "pressure-load-1" / "report"
    report.mkdir(parents=True)
    (report / "below-system.csv").write_text("Datetime,Usage,IOWait,Available,OOM Kills,Running Procs,\n")
    (report / "below-top-processes.csv").write_text("Datetime,Pid,Comm,State,CPU,RSS,Cmdline,\n")
    (report / "below-top-cgroups.csv").write_text("Datetime,Name,Full Path,CPU Usage,Mem Total,CPU Some Pressure,Mem Pressure,RW Total,\n")

    failed = failed_below_exports(root=tmp_path)

    assert len(failed) == 1
    assert failed[0].capture_id == "pressure-load-1"
    assert "below-system.csv" in failed[0].empty_files
