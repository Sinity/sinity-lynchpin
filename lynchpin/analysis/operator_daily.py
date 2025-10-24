"""Cross-source operator daily matrix — every source signal per day.

The panoramic daily view. Joins ActivityWatch, git, health, substance, social,
communication, AI, web, terminal, and music into one typed ``OperatorDay``
record per date. This is the foundation for ALL downstream cross-source
analysis: correlations, anomaly detection, predictive models, life-phase
detection.

Design:
  - One function: ``operator_daily_matrix(start, end) → list[OperatorDay]``
  - Every source is optional — missing data becomes None / default, not an error
  - Graceful degradation: every ``_fill_*`` is routed through ``_try_fill``,
    which catches, logs, and continues. One broken source never aborts the
    whole matrix.
  - Missing ≠ zero: ``OperatorDay.sources_present`` records which sources
    actually contributed data for that day. A genuine-zero day (source covered
    the date but recorded no activity) is distinguishable from an absent day
    (source had no coverage for that date or failed to load).
  - Coverage clamping: ``coverage_bounds()`` is fetched once; a source only
    marks a day present when that day is inside the source's observed coverage.
    Out-of-coverage days are never counted as presence or genuine zeros.
  - Covers the full date range 2011-2026, though early years have few sources.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional, TypeVar

from ..core.coverage import CoverageBounds

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


@dataclass
class OperatorDay:
    """All available signals for one calendar date.

    Numeric fields carry the measured value (or default when absent). The
    ``sources_present`` set is the provenance that lets consumers tell an
    absent/failed source apart from a genuine-zero day: a numeric ``0`` whose
    label is NOT in ``sources_present`` means "not observed", not "no activity".
    """

    date: date

    # ── ActivityWatch (2024-10+) ──
    aw_active_hours: Optional[float] = None
    aw_deep_work_min: Optional[float] = None
    aw_fragmentation: Optional[float] = None
    aw_dominant_project: Optional[str] = None
    aw_outage_hours: Optional[float] = None
    aw_presence_active_hours: Optional[float] = None
    aw_presence_typing_hours: Optional[float] = None
    aw_presence_data_gap_hours: Optional[float] = None

    # ── Git (2024-09+) ──
    git_commits: int = 0
    git_lines_added: int = 0
    git_lines_deleted: int = 0
    git_repos_active: tuple[str, ...] = ()

    # ── SVN / historical workplace (2017-07 → 2022-09) ──
    svn_commits: int = 0
    svn_files_changed: int = 0

    # ── Health / Samsung (2022-08+) ──
    stress_mean: Optional[float] = None
    stress_min: Optional[float] = None
    stress_max: Optional[float] = None
    hr_mean_bpm: Optional[float] = None
    hr_resting_bpm: Optional[float] = None
    hrv_sdnn: Optional[float] = None
    hrv_rmssd: Optional[float] = None
    sleep_hours: Optional[float] = None
    sleep_score: Optional[float] = None
    steps: Optional[int] = None
    spo2_pct: Optional[float] = None
    skin_temp_c: Optional[float] = None

    # ── Substance (2020-06+) ──
    substance_doses: int = 0
    substance_mg_by_name: dict[str, float] = field(default_factory=dict)
    substance_unique_count: int = 0

    # ── Social ──
    wykop_comments: int = 0
    wykop_own_chars: int = 0
    reddit_comments: int = 0
    reddit_own_chars: int = 0

    # ── Communication ──
    sms_sent: int = 0
    sms_received: int = 0
    messenger_sent: int = 0
    messenger_received: int = 0
    outlook_inbox: int = 0
    outlook_sent: int = 0
    gmail_messages: int = 0

    # ── AI (2025-05+) ──
    polylogue_sessions: int = 0
    polylogue_messages: int = 0
    polylogue_engaged_minutes: float = 0.0
    polylogue_projects: tuple[str, ...] = ()

    # ── Web ──
    web_visits: int = 0
    web_unique_domains: int = 0
    web_github_visits: int = 0
    web_social_visits: int = 0

    # ── Terminal ──
    shell_commands: int = 0

    # ── Music ──
    spotify_hours: Optional[float] = None

    # ── Keylog (scribe-tap; 2024+) ──
    keylog_keypresses: int = 0
    keylog_sessions: int = 0
    keylog_keybind_uses: int = 0
    keylog_unique_keybinds: int = 0
    keylog_keybind_families: int = 0
    keylog_top_keybind_family: Optional[str] = None
    keylog_top_keybind_family_uses: int = 0

    # ── Clipboard (Clipse; continuous capture) ──
    clipboard_entries: int = 0

    # ── IRC (continuous when connected) ──
    irc_conversations: int = 0
    irc_lines: int = 0

    # ── Raw log (knowledgebase hotkey capture) ──
    raw_log_entries: int = 0

    # ── Samsung per-minute binning (stress / HRV) ──
    samsung_stress_bins: int = 0
    samsung_hrv_bins: int = 0

    # ── Weather (external API; coverage not source-tracked) ──
    weather_temp_mean: Optional[float] = None
    weather_precip_mm: Optional[float] = None
    weather_sunshine_hours: Optional[float] = None
    weather_cloud_pct: Optional[float] = None
    # ── Mood (sentiment/emotion over own text) ──
    mood_sentiment: Optional[float] = None
    mood_dominant_emotion: Optional[str] = None
    mood_message_count: int = 0
    # ── Web content categories ──
    web_nsfw_share: Optional[float] = None
    web_distraction_ratio: Optional[float] = None
    web_top_category: Optional[str] = None
    # ── Audio features (Spotify-dump; partial coverage) ──
    audio_energy: Optional[float] = None
    audio_valence: Optional[float] = None
    audio_danceability: Optional[float] = None

    # ── Machine telemetry (sinnix-kx4; continuous, 2026-05+) ──
    # OOM/earlyoom kill count and peak memory/io PSI "some_avg10" for the day.
    # Read directly from the DuckDB substrate (metric_sample, kill_event),
    # not the pressure-incidents analysis artifact, so this stays fresh even
    # when that heavier product hasn't been regenerated yet.
    machine_kill_events: int = 0
    machine_peak_memory_psi_some_avg10: Optional[float] = None
    machine_peak_io_psi_some_avg10: Optional[float] = None

    # ── Derived / composite ──
    total_known_source_count: int = 0  # how many distinct sources contributed
    sources_present: frozenset[str] = frozenset()  # missing-vs-zero provenance

    def has_source(self, label: str) -> bool:
        """True when ``label`` was actually observed this day.

        Counter fields (``git_commits``, ``substance_doses``, ``svn_commits``,
        social counts, …) default to ``0`` for BOTH a genuine zero AND a day the
        source did not cover — so a bare ``row.git_commits == 0`` cannot tell the
        two apart. Gate on this (or ``measured``) before treating such a field as
        a real observation; ``label`` is a ``sources_present`` provenance string
        (e.g. "git", "substance", "activitywatch", "health").
        """
        return label in self.sources_present

    def measured(self, label: str, value: _T) -> Optional[_T]:
        """Return ``value`` only if ``label`` was observed, else ``None``.

        The no-data guard for the int-defaulted counters: ``row.measured("git",
        row.git_commits)`` yields ``None`` on days git did not cover instead of a
        fabricated ``0`` that would pollute means/correlations/anomaly scans.
        """
        return value if label in self.sources_present else None


# Coverage-bounds source key per ``_fill_*`` label. Labels whose data is gated
# by their own ``daily_activity`` queries but that have no coverage_bounds entry
# (exports such as reddit/health that report coverage under different keys, or
# sources not yet tracked) are mapped to ``None`` — those are filled without
# clamping (the source's own range query is the guard). Keys here MUST match
# ``coverage_bounds()`` / ``available_sources()`` keys.
_COVERAGE_KEY: dict[str, str | None] = {
    "activitywatch": "activitywatch",
    "git": "git_baseline",
    "svn": "svn",
    "health": None,
    "substance": None,
    "wykop": "wykop",
    "reddit": "reddit",
    "sms": None,
    "messenger": "fbmessenger",
    "outlook": None,
    "sleep": "sleep",
    "polylogue": "polylogue",
    "web": "webhistory",
    "terminal": "atuin",
    "spotify": "spotify",
    "keylog": "keylog",
    "clipboard": "clipboard",
    "irc": "irc",
    "raw_log": "raw_log",
    "samsung_binning": None,
    "weather": None,
    "mood": None,
    "web_category": None,
    "audio_features": None,
    "machine": None,
}


def operator_daily_matrix(
    start: date,
    end: date,
    *,
    skip_slow: bool = False,
    include_external: bool = False,
) -> list[OperatorDay]:
    """Build the panoramic daily matrix for [start, end] inclusive.

    Args:
        start, end: date range (inclusive).
        skip_slow: if True, skip sources that still require heavier session/raw
                   scans (clipboard, raw_log, samsung, and keylog keybind
                   attribution). Product-backed web, terminal, Polylogue, IRC,
                   keylog daily counts, and Spotify daily signals still load
                   because their refresh/read path is cheap and bounded.
        include_external: if True, additionally fill external/network/model-
                   dependent enrichment — weather (Open-Meteo API), mood
                   (HF sentiment models), web_category (LLM classifier), and
                   audio_features (Spotify-dump CSV). OFF by default (also for
                   skip_slow=False local builds) because these hit network /
                   LLM / models and are slow.

    Returns one ``OperatorDay`` per calendar date in the range, with all
    available source signals filled in. Days with zero sources active will
    still have a row (all fields at defaults, ``sources_present`` empty).

    Graceful degradation: every ``_fill_*`` runs through ``_try_fill`` — any
    source that raises is logged and skipped; its columns stay at defaults and
    it does not appear in any day's ``sources_present``.

    Missing ≠ zero / coverage clamping: ``coverage_bounds()`` is fetched once.
    A source only marks a day present (and only treats that day's zero as a
    genuine zero) when the day is inside the source's observed coverage.
    Out-of-coverage days are never fabricated as zeros.
    """
    rows: dict[date, OperatorDay] = {
        start + timedelta(days=i): OperatorDay(date=start + timedelta(days=i))
        for i in range((end - start).days + 1)
    }
    present: dict[date, set[str]] = {d: set() for d in rows}

    bounds = _load_coverage_bounds()

    ctx = _FillContext(rows=rows, present=present, bounds=bounds, start=start, end=end)

    # ── ActivityWatch (presence is part of daily_activity) ──
    _try_fill(ctx, "activitywatch", lambda: _fill_aw(ctx))
    # ── Git ──
    _try_fill(ctx, "git", lambda: _fill_git(ctx))
    # ── SVN ──
    _try_fill(ctx, "svn", lambda: _fill_svn(ctx))
    # ── Health ──
    _try_fill(ctx, "health", lambda: _fill_health(ctx))
    # ── Sleep ──
    _try_fill(ctx, "sleep", lambda: _fill_sleep(ctx))
    # ── Substance ──
    _try_fill(ctx, "substance", lambda: _fill_substance(ctx))
    # ── Wykop ──
    _try_fill(ctx, "wykop", lambda: _fill_wykop(ctx))
    # ── Reddit ──
    _try_fill(ctx, "reddit", lambda: _fill_reddit(ctx))
    # ── SMS ──
    _try_fill(ctx, "sms", lambda: _fill_sms(ctx))
    # ── Messenger ──
    _try_fill(ctx, "messenger", lambda: _fill_messenger(ctx))
    # ── Outlook ──
    _try_fill(ctx, "outlook", lambda: _fill_outlook(ctx))
    # ── Web (product-backed) ──
    _try_fill(ctx, "web", lambda: _fill_web(ctx))
    # ── Terminal (product-backed Atuin) ──
    _try_fill(ctx, "terminal", lambda: _fill_terminal(ctx))
    # ── Polylogue (product-backed archive insights) ──
    _try_fill(ctx, "polylogue", lambda: _fill_polylogue(ctx))
    # ── IRC (product-backed WeeChat events) ──
    _try_fill(ctx, "irc", lambda: _fill_irc(ctx))
    # ── Spotify (product-backed) ──
    _try_fill(ctx, "spotify", lambda: _fill_spotify(ctx))
    # ── Keylog daily counts (product-backed) ──
    _try_fill(ctx, "keylog", lambda: _fill_keylog_daily(ctx))
    # ── Machine telemetry (product-backed DuckDB substrate; sinnix-kx4) ──
    _try_fill(ctx, "machine", lambda: _fill_machine_pressure(ctx))

    if not skip_slow:
        # ── Keylog keybind attribution ──
        _try_fill(ctx, "keylog", lambda: _fill_keylog_keybinds(ctx))
        # ── Clipboard ──
        _try_fill(ctx, "clipboard", lambda: _fill_clipboard(ctx))
        # ── Raw log ──
        _try_fill(ctx, "raw_log", lambda: _fill_raw_log(ctx))
        # ── Samsung per-minute binning ──
        _try_fill(ctx, "samsung_binning", lambda: _fill_samsung_binning(ctx))
    # External enrichment — opt-in (off by default, including for skip_slow=False
    # local builds and tests). These hit the Open-Meteo API, local HF sentiment
    # models, the LLM domain classifier, and the 345MB audio-features CSV, so
    # they must not run in a default matrix build. Enable for a full matrix.
    if include_external:
        # ── Weather (Open-Meteo API, cached) ──
        _try_fill(ctx, "weather", lambda: _fill_weather(ctx))
        # ── Mood (sentiment/emotion models over own text) ──
        _try_fill(ctx, "mood", lambda: _fill_mood(ctx))
        # ── Web content categories (LLM-classified, cached) ──
        _try_fill(ctx, "web_category", lambda: _fill_web_category(ctx))
        # ── Audio features (Spotify-dump; partial coverage) ──
        _try_fill(ctx, "audio_features", lambda: _fill_audio_features(ctx))

    for d, row in rows.items():
        row.sources_present = frozenset(present[d])
        row.total_known_source_count = len(present[d])

    return list(rows.values())


# ══════════════════════════════════════════════════════════════════════════════
# Fill infrastructure
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class _FillContext:
    """Shared state threaded through every ``_fill_*`` helper."""

    rows: dict[date, OperatorDay]
    present: dict[date, set[str]]
    bounds: dict[str, CoverageBounds]
    start: date
    end: date
    source: str = field(default="")  # current source label (set by _try_fill)

    @property
    def end_exclusive(self) -> date:
        """Internal half-open end for source APIs that use ``[start, end)``."""
        return self.end + timedelta(days=1)

    def covered(self, day: date) -> bool:
        """True if *day* is inside the current source's observed coverage.

        When no coverage bounds are known for the source label (export sources
        reported under a different key, or sources not tracked), default to
        True so the source's own range query remains the only guard — we never
        suppress real data, we only refuse to fabricate out-of-coverage zeros
        when bounds ARE known.
        """
        key = _COVERAGE_KEY.get(self.source)
        if key is None:
            return True
        cov = self.bounds.get(key)
        if cov is None or (cov.first is None and cov.last is None):
            return True
        return cov.covers(day)

    def mark(self, day: date) -> bool:
        """Record the current source as present for *day* if it is in coverage.

        Returns True when the day was marked (in coverage), False otherwise.
        Callers should only write numeric values when this returns True so
        out-of-coverage days are never coerced to fabricated zeros.
        """
        if not self.covered(day):
            return False
        self.present[day].add(self.source)
        return True


def _load_coverage_bounds() -> dict[str, CoverageBounds]:
    """Fetch coverage bounds once; degrade to empty mapping on failure."""
    try:
        from ..sources.source_observations import coverage_bounds

        return coverage_bounds()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("operator_daily: coverage_bounds() failed: %s", exc)
        return {}


def _no_overlap(req_start: date, req_end: date, data_start: date, data_end: date) -> bool:
    """True if the requested window has no overlap with the data window."""
    return req_start > data_end or req_end <= data_start


def _try_fill(ctx: _FillContext, source: str, fn: Callable[[], None]) -> None:
    """Run a fill helper with graceful degradation.

    The source label is set on the context so helpers can clamp via coverage
    and record presence. Any exception is caught and logged; the matrix build
    continues with this source's columns left at defaults.
    """
    ctx.source = source
    try:
        fn()
    except Exception as exc:
        logger.warning("operator_daily: source %r failed, skipping: %s", source, exc)


# ══════════════════════════════════════════════════════════════════════════════
# Fill helpers — each fills ctx.rows in-place via ctx.mark() for presence
# ══════════════════════════════════════════════════════════════════════════════


def _fill_aw(ctx: _FillContext) -> None:
    from ..materialization import ensure_materialized
    from ..sources.activitywatch_derived import iter_derived_daily_activity

    ensure_materialized("activitywatch_derived", window=(ctx.start, ctx.end_exclusive))
    daily = iter_derived_daily_activity(start=ctx.start, end=ctx.end, ensure=False)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.aw_active_hours = d.active_hours
            r.aw_deep_work_min = d.deep_work_min
            r.aw_fragmentation = d.fragmentation_score
            r.aw_dominant_project = d.dominant_project
            r.aw_outage_hours = d.outage_hours
            r.aw_presence_active_hours = d.presence_active_hours
            r.aw_presence_typing_hours = d.presence_typing_hours
            r.aw_presence_data_gap_hours = d.presence_data_gap_hours


def _fill_git(ctx: _FillContext) -> None:
    from ..sources.git import daily_activity
    daily = daily_activity(start=ctx.start, end=ctx.end)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.git_commits += d.commit_count
            r.git_lines_added += d.lines_added
            r.git_lines_deleted += d.lines_deleted


def _fill_svn(ctx: _FillContext) -> None:
    if _no_overlap(ctx.start, ctx.end_exclusive, date(2017, 7, 10), date(2022, 9, 23)):
        return
    from ..sources.svn import daily_activity
    daily = daily_activity(start=ctx.start, end=ctx.end)
    for d in daily:
        day = d.date
        if day in ctx.rows and ctx.mark(day):
            r = ctx.rows[day]
            r.svn_commits += d.commit_count
            r.svn_files_changed += d.files_changed


def _fill_health(ctx: _FillContext) -> None:
    from ..sources.health import daily_health_summary
    daily = daily_health_summary(start=ctx.start, end=ctx.end)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.stress_mean = d.stress_avg
            r.hr_mean_bpm = d.heart_rate_avg
            r.hr_resting_bpm = d.heart_rate_resting
            r.hrv_rmssd = d.hrv_rmssd_avg
            r.steps = d.steps
            r.spo2_pct = d.spo2_avg
            r.skin_temp_c = d.skin_temp_avg


def _fill_sleep(ctx: _FillContext) -> None:
    from ..sources.sleep import daily_activity
    daily = daily_activity(start=ctx.start, end=ctx.end)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.sleep_hours = d.total_hours
            r.sleep_score = d.score


def _fill_substance(ctx: _FillContext) -> None:
    from ..sources.substance import daily_summary
    daily = daily_summary(start=ctx.start, end=ctx.end)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.substance_doses = d.dose_count
            r.substance_mg_by_name = dict(d.by_substance_mg)
            r.substance_unique_count = len(d.substances)


def _fill_wykop(ctx: _FillContext) -> None:
    from ..sources.wykop import daily_activity
    daily = daily_activity(start=ctx.start, end=ctx.end)
    for d in daily:
        day = d.date
        if day in ctx.rows and ctx.mark(day):
            r = ctx.rows[day]
            r.wykop_comments = d.comments
            r.wykop_own_chars = d.own_chars


def _fill_reddit(ctx: _FillContext) -> None:
    from ..sources.reddit import daily_activity
    daily = daily_activity(start=ctx.start, end=ctx.end_exclusive)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.reddit_comments = d.comment_count
            r.reddit_own_chars = d.total_words


def _fill_machine_pressure(ctx: _FillContext) -> None:
    """Daily OOM-kill count + peak memory/io PSI (sinnix-kx4).

    Reads the DuckDB substrate directly (``machine_metric_sample`` for PSI
    peaks, ``machine_kill_event`` for kill counts) rather than the
    pressure-incidents analysis artifact, so this stays fresh even when that
    heavier product hasn't been regenerated. A day is only marked present
    when the live substrate actually has metric_sample rows for it; a day
    the machine telemetry source never captured is left absent, not zeroed.
    ``machine_kill_event`` is a newer table (sinnix-fjq) — its absence on an
    older substrate degrades to a zero kill count for every present day
    rather than failing the whole fill.

    Both queries go through ``latest_machine_rows()`` to dedupe across
    refresh_ids (e.g. an old one-off manual rebuild and the daily rolling
    refresh can both hold rows for the same day) rather than a raw
    ``COUNT(*)``/``MAX()`` over every promoted refresh_id, which would double
    (or worse) count the same live event.
    """
    from lynchpin.analysis.machine.sql import latest_machine_rows

    from ..substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        metric_rows = conn.execute(
            f"""
            SELECT
                CAST(observed_at AS DATE) AS day,
                MAX(memory_psi_some_avg10) AS peak_memory_psi,
                MAX(io_psi_some_avg10) AS peak_io_psi
            FROM ({latest_machine_rows("machine_metric_sample")})
            WHERE observed_at >= ? AND observed_at < ?
            GROUP BY day
            """,
            [ctx.start, ctx.end_exclusive],
        ).fetchall()
        kill_by_day: dict[date, int] = {}
        try:
            kill_rows = conn.execute(
                f"""
                SELECT CAST(observed_at AS DATE) AS day, COUNT(*) AS n
                FROM ({latest_machine_rows("machine_kill_event")})
                WHERE observed_at >= ? AND observed_at < ?
                GROUP BY day
                """,
                [ctx.start, ctx.end_exclusive],
            ).fetchall()
            kill_by_day = {day: int(n) for day, n in kill_rows}
        except Exception:
            pass  # older substrates predate machine_kill_event (schema v37 and earlier)

    for day, peak_memory_psi, peak_io_psi in metric_rows:
        if day not in ctx.rows or not ctx.mark(day):
            continue
        r = ctx.rows[day]
        r.machine_peak_memory_psi_some_avg10 = peak_memory_psi
        r.machine_peak_io_psi_some_avg10 = peak_io_psi
        r.machine_kill_events = kill_by_day.get(day, 0)


def _fill_sms(ctx: _FillContext) -> None:
    if _no_overlap(ctx.start, ctx.end_exclusive, date(2021, 8, 1), date(2025, 8, 12)):
        return
    from ..sources.sms import daily_activity
    daily = daily_activity(start=ctx.start, end=ctx.end)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.sms_sent = d.sent_count
            r.sms_received = d.received_count


def _fill_messenger(ctx: _FillContext) -> None:
    from ..sources.exports_messenger import daily_messenger_activity
    daily = daily_messenger_activity(start=ctx.start, end=ctx.end_exclusive)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.messenger_sent = d.sent_count
            r.messenger_received = max(d.message_count - d.sent_count, 0)


def _fill_outlook(ctx: _FillContext) -> None:
    if _no_overlap(ctx.start, ctx.end_exclusive, date(2021, 9, 30), date(2022, 9, 23)):
        return
    from ..sources.outlook import daily_activity
    daily = daily_activity(start=ctx.start, end=ctx.end)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.outlook_inbox = d.inbox_count
            r.outlook_sent = d.sent_count


def _fill_polylogue(ctx: _FillContext) -> None:
    from ..sources.polylogue import daily_activity
    daily = daily_activity(start=ctx.start, end=ctx.end)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.polylogue_sessions += d.session_count
            r.polylogue_messages += d.total_messages
            r.polylogue_engaged_minutes += d.engaged_minutes
            if d.projects:
                r.polylogue_projects = tuple(
                    dict.fromkeys((*r.polylogue_projects, *d.projects))
                )


def _fill_web(ctx: _FillContext) -> None:
    from ..materialization import ensure_materialized
    from ..sources.web import daily_browsing

    ensure_materialized("webhistory", window=(ctx.start, ctx.end_exclusive))
    daily = daily_browsing(start=ctx.start, end=ctx.end, ensure=False)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.web_visits = d.visit_count
            r.web_unique_domains = d.unique_domains


def _fill_terminal(ctx: _FillContext) -> None:
    from ..materialization import ensure_materialized
    from ..sources.terminal import daily_terminal_activity

    ensure_materialized("atuin", window=(ctx.start, ctx.end_exclusive))
    daily = daily_terminal_activity(start=ctx.start, end=ctx.end, ensure=False)
    for d in daily:
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.shell_commands = d.command_count


def _fill_spotify(ctx: _FillContext) -> None:
    from ..materialization import ensure_materialized
    from ..sources.personal_signals import iter_spotify_daily_signals

    ensure_materialized("spotify_daily", window=(ctx.start, ctx.end_exclusive))
    for d in iter_spotify_daily_signals(start=ctx.start, end=ctx.end_exclusive, ensure=False):
        if d.date in ctx.rows and ctx.mark(d.date):
            ctx.rows[d.date].spotify_hours = round(d.minutes_played / 60.0, 2)


def _fill_keylog(ctx: _FillContext) -> None:
    _fill_keylog_daily(ctx)
    _fill_keylog_keybinds(ctx)


def _fill_keylog_daily(ctx: _FillContext) -> None:
    from ..materialization import ensure_materialized
    from ..sources.personal_signals import iter_personal_daily_signals

    ensure_materialized("personal_daily_signals", window=(ctx.start, ctx.end_exclusive))
    metrics_by_day: dict[date, dict[str, float]] = {}
    for row in iter_personal_daily_signals(start=ctx.start, end=ctx.end_exclusive, ensure=False):
        if row.source != "keylog":
            continue
        metrics_by_day.setdefault(row.date, {})[row.metric] = row.value

    for day, metrics in metrics_by_day.items():
        if day not in ctx.rows or not ctx.covered(day):
            continue
        if not metrics:
            continue
        # The daily product stores zero rows for in-coverage zero days; preserve
        # that presence without forcing live keylog scans.
        if ctx.mark(day):
            r = ctx.rows[day]
            r.keylog_keypresses = int(metrics.get("keypress_count", 0))
            r.keylog_sessions = int(metrics.get("session_count", 0))


def _fill_keylog_keybinds(ctx: _FillContext) -> None:
    keybind_usage = _keylog_keybind_usage_rows(ctx.start, ctx.end)
    if keybind_usage is None:
        return
    for use in keybind_usage:
        use_date = use.get("date")
        count = int(use.get("count") or 0)
        if use_date in ctx.rows and ctx.mark(use_date):
            row = ctx.rows[use_date]
            row.keylog_keybind_uses += count
    by_day: dict[date, set[str]] = {}
    family_by_day: dict[date, dict[str, int]] = {}
    for use in keybind_usage:
        use_date = use.get("date")
        chord = use.get("chord")
        if isinstance(use_date, date) and isinstance(chord, str):
            by_day.setdefault(use_date, set()).add(chord)
        family = use.get("family")
        if isinstance(use_date, date) and isinstance(family, str) and family:
            family_counts = family_by_day.setdefault(use_date, {})
            family_counts[family] = family_counts.get(family, 0) + int(use.get("count") or 0)
    for day, chords in by_day.items():
        if day in ctx.rows and ctx.mark(day):
            ctx.rows[day].keylog_unique_keybinds = len(chords)
    for day, family_counts in family_by_day.items():
        if day not in ctx.rows or not ctx.mark(day):
            continue
        top_family, top_count = sorted(
            family_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
        row = ctx.rows[day]
        row.keylog_keybind_families = len(family_counts)
        row.keylog_top_keybind_family = top_family
        row.keylog_top_keybind_family_uses = top_count


def _keylog_keybind_usage_rows(start: date, end: date) -> list[dict[str, Any]] | None:
    from ..materialization import ensure_materialized

    ensure_materialized("keylog_analysis", window=(start, end + timedelta(days=1)))
    artifact_rows = _keylog_artifact_keybind_usage_rows(start, end)
    if artifact_rows is not None:
        return artifact_rows
    try:
        from .keylog import analyze_keylog

        analysis = analyze_keylog(start=start, end=end)
    except Exception:
        return None
    return [
        {
            "date": use.date,
            "chord": use.chord,
            "family": getattr(use, "family", None),
            "count": use.count,
        }
        for use in analysis.keybind_usage
    ]


def _keylog_artifact_keybind_usage_rows(start: date, end: date) -> list[dict[str, Any]] | None:
    from lynchpin.core.io import load_json_if_exists, resolve_analysis_path

    payload = load_json_if_exists(resolve_analysis_path("keylog_analysis.json"))
    if not isinstance(payload, dict):
        return None
    try:
        artifact_start = date.fromisoformat(str(payload.get("start")))
        artifact_end = date.fromisoformat(str(payload.get("end")))
    except ValueError:
        return None
    if artifact_start > start or end > artifact_end:
        return None

    rows = payload.get("keybind_usage")
    if not isinstance(rows, list):
        return None
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            row_date = date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        if start <= row_date <= end:
            parsed.append(
                {
                    "date": row_date,
                    "chord": row.get("chord"),
                    "family": row.get("family"),
                    "count": int(row.get("count") or 0),
                }
            )
    return parsed


def _fill_clipboard(ctx: _FillContext) -> None:
    from collections import Counter

    from ..sources.clipboard import entries_in_range
    counts: Counter[date] = Counter()
    for entry in entries_in_range(start=ctx.start, end=ctx.end):
        counts[entry.date] += 1
    for day, count in counts.items():
        if day in ctx.rows and ctx.mark(day):
            ctx.rows[day].clipboard_entries = count


def _fill_irc(ctx: _FillContext) -> None:
    from ..materialization import ensure_materialized
    from ..sources.irc_raw import daily_irc_activity

    ensure_materialized("irc", window=(ctx.start, ctx.end_exclusive))
    for day_row in daily_irc_activity(start=ctx.start, end=ctx.end, ensure=False):
        if day_row.date in ctx.rows and ctx.mark(day_row.date):
            r = ctx.rows[day_row.date]
            r.irc_conversations = day_row.conversation_count
            r.irc_lines = day_row.total_messages


def _fill_raw_log(ctx: _FillContext) -> None:
    from collections import Counter

    from ..sources.raw_log import entries_in_range
    counts: Counter[date] = Counter()
    for entry in entries_in_range(start=ctx.start, end=ctx.end):
        counts[entry.date] += 1
    for day, count in counts.items():
        if day in ctx.rows and ctx.mark(day):
            ctx.rows[day].raw_log_entries = count


def _fill_samsung_binning(ctx: _FillContext) -> None:
    # The per-minute binning iterators emit UTC-tz timestamps and have no
    # date-bounded query, so iterate once and bucket by logical date, skipping
    # rows outside the requested window. Stress coverage starts 2022-08-30;
    # HRV coverage starts 2025-05-21.
    from collections import Counter

    from ..core.primitives import date_to_dt_range
    from ..core.primitives import logical_date
    from ..sources.samsung_binning import iter_hrv_bins, iter_stress_bins

    start_dt, end_dt = date_to_dt_range(ctx.start, ctx.end)
    stress_counts: Counter[date] = Counter()
    for stress_bin in iter_stress_bins(start=start_dt, end=end_dt):
        day = logical_date(stress_bin.ts)
        if ctx.start <= day < ctx.end_exclusive:
            stress_counts[day] += 1

    hrv_counts: Counter[date] = Counter()
    for hrv_bin in iter_hrv_bins(start=start_dt, end=end_dt):
        day = logical_date(hrv_bin.ts)
        if ctx.start <= day < ctx.end_exclusive:
            hrv_counts[day] += 1

    for day in set(stress_counts) | set(hrv_counts):
        if day in ctx.rows and ctx.mark(day):
            r = ctx.rows[day]
            r.samsung_stress_bins = stress_counts.get(day, 0)
            r.samsung_hrv_bins = hrv_counts.get(day, 0)


def _fill_weather(ctx: _FillContext) -> None:
    # Open-Meteo (external API, cached). WeatherUnavailableError on network
    # failure is caught by _try_fill, so the columns stay absent.
    from ..sources.weather import daily_weather

    for d in daily_weather(ctx.start, ctx.end):
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.weather_temp_mean = d.temperature_2m_mean
            r.weather_precip_mm = d.precipitation_sum
            r.weather_sunshine_hours = (
                d.sunshine_duration / 3600.0 if d.sunshine_duration is not None else None
            )
            r.weather_cloud_pct = d.cloud_cover_mean


def _fill_mood(ctx: _FillContext) -> None:
    # Sentiment/emotion over own text; SourceUnavailableError (transformers
    # absent) is caught by _try_fill.
    from .text_sentiment import daily_mood

    for d in daily_mood(ctx.start, ctx.end):
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.mood_sentiment = d.mean_sentiment
            r.mood_dominant_emotion = d.dominant_emotion
            r.mood_message_count = d.message_count


def _fill_web_category(ctx: _FillContext) -> None:
    from .web_category_daily import daily_web_categories

    for d in daily_web_categories(start=ctx.start, end=ctx.end, ensure=False):
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.web_nsfw_share = d.nsfw_visit_share
            r.web_distraction_ratio = d.distraction_ratio
            if d.minutes_by_category:
                r.web_top_category = max(d.minutes_by_category, key=d.minutes_by_category.__getitem__)


def _fill_audio_features(ctx: _FillContext) -> None:
    # Spotify-dump audio features (partial coverage); SourceUnavailableError
    # (dataset absent) caught by _try_fill.
    from ..sources.audio_features import daily_audio_features

    for d in daily_audio_features(ctx.start, ctx.end_exclusive):
        if d.date in ctx.rows and ctx.mark(d.date):
            r = ctx.rows[d.date]
            r.audio_energy = d.means.get("energy")
            r.audio_valence = d.means.get("valence")
            r.audio_danceability = d.means.get("danceability")


__all__ = ["OperatorDay", "operator_daily_matrix"]
