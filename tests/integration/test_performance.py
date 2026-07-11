"""Performance benchmarks for critical iterators.

Not strict pass/fail tests — informational benchmarks that flag
regressions when timing exceeds expected baselines on this hardware
(spinning disk sda, NVMe nvme0n1). Baselines established 2026-05-28.
"""

import time

import pytest

pytestmark = pytest.mark.slow


def _timeit(fn, label: str, baseline_s: float) -> None:
    """Run fn, print timing, warn if >2× baseline."""
    start = time.monotonic()
    result = fn()
    elapsed = time.monotonic() - start
    status = "OK" if elapsed < baseline_s * 2 else "SLOW"
    print(f"  {label:40s} {elapsed:6.2f}s (baseline {baseline_s:.1f}s) [{status}]")
    if elapsed > baseline_s * 3:
        pytest.fail(f"{label}: {elapsed:.1f}s > 3× baseline {baseline_s:.1f}s")
    return result


# ── Source iterators ────────────────────────────────────────────────────────

def test_svn_iter_commits_speed():
    """SVN michab commits: ~1200 records from 3 XML files."""
    from lynchpin.sources.svn import iter_commits

    def load():
        return sum(1 for _ in iter_commits(author="michab"))

    _timeit(load, "svn.iter_commits(michab)", baseline_s=30.0)


def test_sms_iter_messages_speed():
    """SMS: ~1800 records from 12 CSV files."""
    from lynchpin.sources.sms import iter_messages

    def load():
        return sum(1 for _ in iter_messages())

    _timeit(load, "sms.iter_messages", baseline_s=2.0)


def test_wykop_iter_comments_speed():
    """Wykop: ~12600 comments from 2 JSONL files."""
    from lynchpin.sources.wykop import iter_comments

    def load():
        return sum(1 for _ in iter_comments())

    _timeit(load, "wykop.iter_comments", baseline_s=3.0)


def test_stress_bins_speed():
    """Stress bins: first 100k from ~640k total."""
    from lynchpin.sources.samsung_binning import iter_stress_bins

    def load():
        count = 0
        for _ in iter_stress_bins():
            count += 1
            if count >= 100000:
                break
        return count

    _timeit(load, "samsung_binning.stress (100k)", baseline_s=10.0)


def test_hrv_bins_speed():
    """HRV bins: ~118k records from 7 CSV files."""
    from lynchpin.sources.samsung_binning import iter_hrv_bins

    def load():
        return sum(1 for _ in iter_hrv_bins())

    _timeit(load, "samsung_binning.hrv (all)", baseline_s=3.0)


# ── Analysis modules ────────────────────────────────────────────────────────

def test_operator_daily_fast_window():
    """Narrow window (7 days) with skip_slow=True should be <10s."""
    from datetime import date
    from lynchpin.analysis.operator_daily import operator_daily_matrix

    def load():
        return operator_daily_matrix(date(2026, 5, 20), date(2026, 5, 27), skip_slow=True)

    _timeit(load, "operator_daily (7d, skip_slow)", baseline_s=90.0)


def test_health_modeling_report_speed():
    """Health modeling on 50k stress bins should be <30s."""
    from lynchpin.sources.samsung_binning import iter_stress_bins, iter_hrv_bins, iter_hr_bins
    from lynchpin.analysis.health_modeling import align_signals, build_report

    stress = list(iter_stress_bins())[:50000]
    hrv = list(iter_hrv_bins())
    hr = list(iter_hr_bins())[:50000]

    def load():
        rows = align_signals(iter(stress), iter(hrv), iter(hr))
        return build_report(rows)

    _timeit(load, "health_modeling.build_report (50k)", baseline_s=20.0)


# ── AW pipeline ─────────────────────────────────────────────────────────────

def test_focus_spans_single_day():
    """focus_spans on a single day should be <5s."""
    from datetime import date
    from lynchpin.sources.activitywatch import focus_spans

    def load():
        return focus_spans(start=date(2026, 5, 27), end=date(2026, 5, 28))

    _timeit(load, "aw.focus_spans (1 day)", baseline_s=30.0)


def test_daily_activity_aw_single_day():
    """AW daily_activity on a single day should be <30s."""
    from datetime import date
    from lynchpin.sources.activitywatch import daily_activity

    def load():
        return daily_activity(start=date(2026, 5, 27), end=date(2026, 5, 27))

    _timeit(load, "aw.daily_activity (1 day)", baseline_s=30.0)
