from __future__ import annotations

from lynchpin.analysis.machine.below import analyze_below_exports


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

    analysis = analyze_below_exports(root=tmp_path, top_n=5)

    assert analysis.window_count == 1
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
