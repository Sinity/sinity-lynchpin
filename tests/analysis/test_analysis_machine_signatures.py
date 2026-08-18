from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from lynchpin.analysis.machine.signatures import (
    classify_signatures,
    detect_burst_alloc_starvation,
    detect_cache_thrash,
    detect_io_livelock_risk,
    detect_kill_storm,
    detect_leak_candidates,
    detect_swap_thrash,
    detect_writeback_pressure,
    headline_signature,
)
from lynchpin.sources.machine_models import (
    MachineKillEvent,
    MachineMetricSample,
    MachineProcessMemoryGrowth,
)

START = datetime(2026, 7, 6, 8, 0, tzinfo=timezone.utc)


def _ts(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def _metric(seconds: float, **overrides: Any) -> MachineMetricSample:
    defaults: dict[str, Any] = {
        "observed_at": _ts(seconds),
        "host": "sinnix-prime",
        "boot_id": "boot-a",
        "source": "machine.telemetry",
        "source_schema_version": 5,
        "mem_avail_mb": 14_000,
        "memory_psi_some_avg10": 1.0,
        "memory_psi_full_avg10": 0.2,
        "io_psi_full_avg10": 0.5,
        "dstate_task_count": 0,
        "mem_dirty_mb": 20,
        "vmstat_workingset_refault_file": 0,
        "vmstat_pswpin": 0,
        "vmstat_pswpout": 0,
        "vmstat_allocstall_normal": 0,
        "vmstat_allocstall_movable": 0,
    }
    defaults.update(overrides)
    return MachineMetricSample(**defaults)


def _kill(seconds: float, *, comm: str, rss_mib: int, pid: int) -> MachineKillEvent:
    return MachineKillEvent(
        observed_at=_ts(seconds),
        host="sinnix-prime",
        boot_id="boot-a",
        source_schema_version=5,
        killer="earlyoom",
        victim_comm=comm,
        victim_pid=pid,
        victim_rss_mib=rss_mib,
        cgroup_path=None,
        oom_score=1000,
        raw_line=f"sending SIGTERM to process {pid} ... {comm}",
        source_row_id=pid,
    )


# ── 1. cache-thrash ───────────────────────────────────────────────────────────


def test_cache_thrash_fires_on_refault_storm_with_quiet_swap():
    samples = [
        _metric(0, vmstat_workingset_refault_file=0, vmstat_pswpin=0, vmstat_pswpout=0, memory_psi_some_avg10=25.0),
        _metric(60, vmstat_workingset_refault_file=300_000, vmstat_pswpin=0, vmstat_pswpout=0, memory_psi_some_avg10=25.0),
    ]
    finding = detect_cache_thrash(samples)
    assert finding.fires
    assert finding.evidence
    assert finding.evidence[0].metric == "vmstat_workingset_refault_file_rate_per_hour"


def test_cache_thrash_does_not_fire_on_small_refault_delta():
    samples = [
        _metric(0, vmstat_workingset_refault_file=0, memory_psi_some_avg10=25.0),
        _metric(60, vmstat_workingset_refault_file=10, memory_psi_some_avg10=25.0),
    ]
    finding = detect_cache_thrash(samples)
    assert not finding.fires
    assert finding.confidence == 0.0


def test_cache_thrash_does_not_fire_when_swap_is_also_cycling():
    # High refault but swap is churning too -> this is swap-thrash's territory, not cache-thrash's.
    samples = [
        _metric(0, vmstat_workingset_refault_file=0, vmstat_pswpin=0, vmstat_pswpout=0, memory_psi_some_avg10=25.0),
        _metric(
            60,
            vmstat_workingset_refault_file=300_000,
            vmstat_pswpin=5_000,
            vmstat_pswpout=5_000,
            memory_psi_some_avg10=25.0,
        ),
    ]
    finding = detect_cache_thrash(samples)
    assert not finding.fires


# ── 2. swap-thrash ────────────────────────────────────────────────────────────


def test_swap_thrash_fires_on_sustained_bidirectional_cycling_under_psi():
    samples = [
        _metric(0, vmstat_pswpin=0, vmstat_pswpout=0, memory_psi_full_avg10=1.0),
        _metric(60, vmstat_pswpin=2_000, vmstat_pswpout=2_000, memory_psi_full_avg10=25.0),
    ]
    finding = detect_swap_thrash(samples)
    assert finding.fires
    assert len(finding.evidence) == 2  # one row for pswpin, one for pswpout at the same tick


def test_swap_thrash_does_not_fire_when_psi_is_calm():
    samples = [
        _metric(0, vmstat_pswpin=0, vmstat_pswpout=0, memory_psi_full_avg10=1.0),
        _metric(60, vmstat_pswpin=2_000, vmstat_pswpout=2_000, memory_psi_full_avg10=1.0, io_psi_full_avg10=1.0),
    ]
    finding = detect_swap_thrash(samples)
    assert not finding.fires


def test_swap_thrash_does_not_fire_on_one_directional_swap():
    # Only pswpout moving (e.g. initial page-out, not a cycle) shouldn't count.
    samples = [
        _metric(0, vmstat_pswpin=0, vmstat_pswpout=0, memory_psi_full_avg10=25.0),
        _metric(60, vmstat_pswpin=0, vmstat_pswpout=5_000, memory_psi_full_avg10=25.0),
    ]
    finding = detect_swap_thrash(samples)
    assert not finding.fires


# ── 3. io-livelock risk ───────────────────────────────────────────────────────


def test_io_livelock_risk_fires_on_dstate_pileup_under_io_full_stall():
    samples = [_metric(0, dstate_task_count=6, io_psi_full_avg10=45.0)]
    finding = detect_io_livelock_risk(samples)
    assert finding.fires
    assert any("journal write-stall" in caveat for caveat in finding.caveats)


def test_io_livelock_risk_does_not_fire_with_low_dstate():
    samples = [_metric(0, dstate_task_count=1, io_psi_full_avg10=45.0)]
    finding = detect_io_livelock_risk(samples)
    assert not finding.fires


# ── 4. kill-storm ─────────────────────────────────────────────────────────────


def test_kill_storm_fires_on_repeated_kills_in_a_short_window_real_shape():
    # Shapes drawn from the real 2026-07-06 08:32:33 earlyoom storm on this
    # host (582 kills in one journal timestamp; rustc/ld.mold/nix victims).
    kills = [
        _kill(0, comm="rustc", rss_mib=4168, pid=1),
        _kill(1, comm="ld.mold", rss_mib=1494, pid=2),
        _kill(2, comm="rustc", rss_mib=5546, pid=3),
        _kill(3, comm="nix", rss_mib=4890, pid=4),
    ]
    finding = detect_kill_storm(kills)
    assert finding.fires
    assert finding.evidence


def test_kill_storm_flags_degenerate_victim_choice_when_victims_are_tiny():
    kills = [_kill(i * 10, comm="worker", rss_mib=5, pid=i) for i in range(4)]
    finding = detect_kill_storm(kills)
    assert finding.fires
    assert any("degenerate victim choice" in caveat for caveat in finding.caveats)


def test_kill_storm_does_not_fire_on_a_single_isolated_kill():
    kills = [_kill(0, comm="rustc", rss_mib=4168, pid=1)]
    finding = detect_kill_storm(kills)
    assert not finding.fires


def test_kill_storm_does_not_fire_when_kills_are_spread_out():
    kills = [_kill(i * 900, comm="rustc", rss_mib=4168, pid=i) for i in range(4)]  # 15 min apart
    finding = detect_kill_storm(kills)
    assert not finding.fires


# ── 5. burst-alloc starvation ─────────────────────────────────────────────────


def test_burst_alloc_starvation_fires_when_allocstall_spikes_while_memory_looks_free():
    samples = [
        _metric(0, vmstat_allocstall_normal=0, mem_avail_mb=14_000),
        _metric(60, vmstat_allocstall_normal=500, mem_avail_mb=14_000),
    ]
    finding = detect_burst_alloc_starvation(samples)
    assert finding.fires


def test_burst_alloc_starvation_does_not_fire_when_memory_is_actually_low():
    # High allocstall while mem_avail is ALSO low is a genuine shortage, not this signature.
    samples = [
        _metric(0, vmstat_allocstall_normal=0, mem_avail_mb=1_000),
        _metric(60, vmstat_allocstall_normal=500, mem_avail_mb=1_000),
    ]
    finding = detect_burst_alloc_starvation(samples)
    assert not finding.fires


def test_burst_alloc_starvation_does_not_fire_on_a_trickle_of_allocstalls():
    samples = [
        _metric(0, vmstat_allocstall_normal=0, mem_avail_mb=14_000),
        _metric(60, vmstat_allocstall_normal=1, mem_avail_mb=14_000),
    ]
    finding = detect_burst_alloc_starvation(samples)
    assert not finding.fires


# ── 6. leak candidates ────────────────────────────────────────────────────────


def test_leak_candidates_fires_on_real_kitty_pid_2724511_history():
    # Real telemetry, sinnix-prime, pid 2724511 (.kitty-wrapped): drawn from
    # process_memory_growth_candidates(start=2026-07-03, end=2026-07-06) on
    # this host's live capture (sinnix-6o2's own cited fixture).
    growth = [
        MachineProcessMemoryGrowth(
            pid=2724511,
            process_start_time_ticks=26432542,
            comm=".kitty-wrapped",
            unit="wayland-wm@hyprland-uwsm.desktop.service",
            scope="user",
            sample_count=2060,
            first_observed_at=datetime(2026, 7, 3, 22, 3, 27, tzinfo=timezone.utc),
            first_pss_anon_kb=46_912,
            last_observed_at=datetime(2026, 7, 5, 10, 29, 32, tzinfo=timezone.utc),
            last_pss_anon_kb=3_019_980,
        )
    ]
    finding = detect_leak_candidates(growth)
    assert finding.fires
    assert len(finding.evidence) == 1
    assert finding.evidence[0].value > 1_000  # ~1.9 GB/day on this fixture
    assert ".kitty-wrapped" in finding.evidence[0].metric


def test_leak_candidates_does_not_fire_on_a_short_lived_build_process():
    # A one-minute nix-build process ballooning by 10GB reads as an absurd
    # GB/day rate if naively extrapolated; the minimum-span floor excludes it.
    growth = [
        MachineProcessMemoryGrowth(
            pid=1,
            process_start_time_ticks=1,
            comm="nix",
            unit="nix-daemon.service",
            scope="system",
            sample_count=2,
            first_observed_at=_ts(0),
            first_pss_anon_kb=100_000,
            last_observed_at=_ts(60),
            last_pss_anon_kb=9_000_000,
        )
    ]
    finding = detect_leak_candidates(growth)
    assert not finding.fires


def test_leak_candidates_does_not_fire_below_the_rate_floor():
    growth = [
        MachineProcessMemoryGrowth(
            pid=1,
            process_start_time_ticks=1,
            comm="steady",
            unit=None,
            scope="user",
            sample_count=100,
            first_observed_at=START,
            first_pss_anon_kb=100_000,
            last_observed_at=START + timedelta(hours=24),
            last_pss_anon_kb=110_000,  # 10MB/day, well under the 200MB/day floor
        )
    ]
    finding = detect_leak_candidates(growth)
    assert not finding.fires


# ── 7. writeback pressure ─────────────────────────────────────────────────────


def test_writeback_pressure_fires_on_repeated_high_dirty_pages():
    samples = [_metric(i * 60, mem_dirty_mb=800) for i in range(4)]
    finding = detect_writeback_pressure(samples)
    assert finding.fires
    assert any("vm.dirty_bytes" in caveat for caveat in finding.caveats)


def test_writeback_pressure_does_not_fire_on_a_single_spike():
    samples = [_metric(0, mem_dirty_mb=20), _metric(60, mem_dirty_mb=800), _metric(120, mem_dirty_mb=20)]
    finding = detect_writeback_pressure(samples)
    assert not finding.fires


# ── golden: kill-storm + nix-build culprit (2026-07-06 real shape) ───────────


def test_kill_storm_golden_2026_07_06_shape_fires_and_headline_selects_it():
    # Real 2026-07-06 08:32:33 earlyoom storm on this host (582 kills, one
    # journal timestamp; rustc/ld.mold/nix build toolchain victims).
    kills = [
        _kill(0, comm="rustc", rss_mib=4168, pid=1),
        _kill(0, comm="ld.mold", rss_mib=1494, pid=2),
        _kill(0, comm="rustc", rss_mib=5546, pid=3),
        _kill(0, comm="nix", rss_mib=4890, pid=4),
        _kill(0, comm="nix", rss_mib=7219, pid=5),
    ]
    samples = [_metric(0)]
    growth: list[MachineProcessMemoryGrowth] = []
    findings = classify_signatures(samples=samples, kills=kills, growth=growth)
    by_name = {f.name: f for f in findings}
    assert by_name["kill-storm"].fires
    headline = headline_signature(findings)
    assert headline is not None
    assert headline.name == "kill-storm"


# ── classify_signatures / headline_signature plumbing ────────────────────────


def test_classify_signatures_returns_all_seven_in_fixed_order():
    findings = classify_signatures(samples=[_metric(0)], kills=[], growth=[])
    assert [f.name for f in findings] == [
        "cache-thrash",
        "swap-thrash",
        "io-livelock-risk",
        "kill-storm",
        "burst-alloc-starvation",
        "leak-candidates",
        "writeback-pressure",
    ]


def test_headline_signature_is_none_when_nothing_fires():
    findings = classify_signatures(samples=[_metric(0)], kills=[], growth=[])
    assert headline_signature(findings) is None


def test_headline_signature_picks_highest_confidence_firing_signature():
    samples = [
        _metric(0, dstate_task_count=6, io_psi_full_avg10=45.0),
        _metric(60, dstate_task_count=6, io_psi_full_avg10=45.0),
        _metric(120, dstate_task_count=6, io_psi_full_avg10=45.0),
        _metric(180, dstate_task_count=6, io_psi_full_avg10=45.0),
        _metric(240, dstate_task_count=6, io_psi_full_avg10=45.0),
        _metric(300, dstate_task_count=6, io_psi_full_avg10=45.0),
    ]
    kills = [_kill(0, comm="rustc", rss_mib=4168, pid=1), _kill(1, comm="nix", rss_mib=100, pid=2)]
    findings = classify_signatures(samples=samples, kills=kills, growth=[])
    headline = headline_signature(findings)
    assert headline is not None
    assert headline.fires
    assert headline.confidence == max(f.confidence for f in findings if f.fires)
