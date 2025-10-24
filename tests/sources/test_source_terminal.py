"""Tests for sources/terminal.py — Atuin commands, shell sessions, recordings."""

import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from lynchpin.sources import terminal
from lynchpin.sources.terminal import (
    _extract_project, _categorise_command, _to_unit, _from_unit,
    ShellSession, commands, commands_from_atuin_db, daily_terminal_activity,
)

UTC = timezone.utc


class TestExtractProject:
    def test_realm_project(self):
        assert _extract_project("/realm/project/sinex/src") == "sinex"

    def test_no_project(self):
        assert _extract_project("/home/user") is None

    def test_lynchpin(self):
        assert _extract_project("/realm/project/sinity-lynchpin") == "sinity-lynchpin"

    def test_rejects_inactive_namespace_as_project(self):
        assert _extract_project("/realm/project/_inactive/codex") is None

    def test_target_vision(self):
        assert _extract_project("/realm/project/sinex-target-vision") == "sinex-target-vision"


class TestCategorise:
    def test_sinex(self):
        assert _categorise_command("/realm/project/sinex") == "development:sinex"

    def test_sinnix(self):
        assert _categorise_command("/realm/project/sinnix") == "infrastructure:sinnix"

    def test_other_project(self):
        assert _categorise_command("/realm/project/polylogue") == "development:other"

    def test_home(self):
        assert _categorise_command("/home/sinity") == "home"

    def test_misc(self):
        assert _categorise_command("/tmp") == "misc"


class TestTimestampUnits:
    def test_roundtrip_ns(self):
        dt = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
        ns = _to_unit(dt, "ns")
        back = _from_unit(ns, "ns")
        assert abs((back - dt).total_seconds()) < 0.001

    def test_roundtrip_s(self):
        dt = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
        s = _to_unit(dt, "s")
        back = _from_unit(s, "s")
        assert abs((back - dt).total_seconds()) < 1


def test_commands_from_atuin_db_uses_timestamp_bounds(tmp_path):
    db = tmp_path / "history.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE history (
            timestamp INTEGER,
            duration INTEGER,
            exit INTEGER,
            cwd TEXT,
            command TEXT
        );
        """
    )
    rows = [
        (datetime(2026, 6, 5, 8, tzinfo=UTC), "before"),
        (datetime(2026, 6, 6, 8, tzinfo=UTC), "inside"),
        (datetime(2026, 6, 7, 8, tzinfo=UTC), "after"),
    ]
    for stamp, command in rows:
        conn.execute(
            "INSERT INTO history VALUES (?, ?, ?, ?, ?)",
            (_to_unit(stamp, "ns"), 1, 0, "/repo", command),
        )
    conn.commit()
    conn.close()

    commands_in_window = list(
        commands_from_atuin_db(
            db,
            start=datetime(2026, 6, 6, tzinfo=UTC),
            end=datetime(2026, 6, 7, tzinfo=UTC),
        )
    )

    assert [row.command for row in commands_in_window] == ["inside"]


def test_commands_default_reader_materializes(monkeypatch, tmp_path):
    import json

    calls = []
    history = tmp_path / "shell/atuin/history.ndjson"
    history.parent.mkdir(parents=True)
    history.write_text(
        json.dumps(
            {
                "timestamp": "2026-05-12T12:00:00+00:00",
                "duration_ns": 1000,
                "exit_code": 0,
                "cwd": "/realm/project/sinity-lynchpin",
                "command": "pytest",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(terminal, "get_config", lambda: SimpleNamespace(captures_root=tmp_path))
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    rows = list(
        commands(
            start=datetime(2026, 5, 12, 0, tzinfo=UTC),
            end=datetime(2026, 5, 13, 0, tzinfo=UTC),
        )
    )

    assert calls == [("atuin", (date(2026, 5, 12), date(2026, 5, 13)))]
    assert [row.command for row in rows] == ["pytest"]


def test_recordings_parses_asciinema_v2(tmp_path):
    """v2 cast format must still parse (back-compat with older recordings)."""
    import json
    from lynchpin.sources.terminal import _parse_cast_file
    p = tmp_path / "session.cast"
    p.write_text(json.dumps({
        "version": 2, "width": 120, "height": 40,
        "timestamp": 1700000000,
        "env": {"SHELL": "/bin/zsh"},
    }) + "\n")
    rec = _parse_cast_file(p)
    assert rec is not None
    assert rec.shell == "/bin/zsh"
    assert rec.created_at is not None
    assert rec.created_at.year == 2023


def test_recordings_parses_asciinema_v3(tmp_path):
    """v3 cast format (asciinema 2025-08) uses nested 'term' block.

    Regression: the prior parser hardcoded ``version in (2, "2")`` and
    silently returned None for 94% of the operator's archive (which is
    all v3 since switching to a recent asciinema build).
    """
    import json
    from lynchpin.sources.terminal import _parse_cast_file
    p = tmp_path / "session.cast"
    p.write_text(json.dumps({
        "version": 3,
        "term": {"cols": 289, "rows": 75, "type": "xterm-kitty"},
        "timestamp": 1779104404,
        "command": "/bin/zsh",
        "env": {"SHELL": "/bin/zsh", "TERM": "xterm-kitty"},
    }) + "\n")
    rec = _parse_cast_file(p)
    assert rec is not None
    assert rec.shell == "/bin/zsh"
    assert rec.created_at is not None


def test_recordings_accepts_date_or_datetime_filter(tmp_path, monkeypatch):
    """recordings(start=date) used to crash with 'can't compare datetime to date'.

    LynchpinConfig is frozen, so we patch the module-level get_config.
    """
    import dataclasses
    import json
    from datetime import date
    from lynchpin.sources.terminal import recordings
    from lynchpin.core.config import get_config

    cfg = get_config()
    cfg_patched = dataclasses.replace(cfg, asciinema_root=tmp_path)
    monkeypatch.setattr("lynchpin.sources.terminal.get_config", lambda: cfg_patched)

    # v3 cast dated 2026-05-22 should be included when start=2026-05-20.
    cast = tmp_path / "session.cast"
    cast.write_text(json.dumps({
        "version": 3,
        "term": {"cols": 100, "rows": 30, "type": "xterm"},
        "timestamp": 1779747200,
        "env": {"SHELL": "/bin/zsh"},
    }) + "\n")

    result = list(recordings(start=date(2026, 5, 20), end=date(2026, 5, 27)))
    assert len(result) == 1


# ---------------------------------------------------------------------------
# download_provenance — yt-dlp URL<->filename pairs from cast content
# ---------------------------------------------------------------------------

def _write_cast(path, *, version=3, timestamp=1779747200, outputs=()):
    import json
    header = ({"version": 3, "term": {"cols": 100, "rows": 30, "type": "xterm"},
               "timestamp": timestamp, "env": {"SHELL": "/bin/zsh"}}
              if version == 3 else
              {"version": 2, "width": 100, "height": 30, "timestamp": timestamp})
    lines = [json.dumps(header)]
    for t, text in outputs:
        lines.append(json.dumps([t, "o", text]))
    path.write_text("\n".join(lines) + "\n")


def _patch_root(monkeypatch, tmp_path):
    import dataclasses
    from lynchpin.core.config import get_config
    cfg = dataclasses.replace(get_config(), asciinema_root=tmp_path)
    monkeypatch.setattr("lynchpin.sources.terminal.get_config", lambda: cfg)


def test_download_provenance_pairs_url_and_filename(tmp_path, monkeypatch):
    from lynchpin.sources.terminal import download_provenance
    _patch_root(monkeypatch, tmp_path)
    _write_cast(tmp_path / "session.cast", outputs=[
        (1.0, "[video-host] Extracting URL: https://video-host.example/7un8j/video/clip+title\r\n"),
        (1.5, "[download] Destination: Clip title [7un8j].mp4\r\n"),
    ])
    evs = list(download_provenance())
    assert len(evs) == 1
    e = evs[0]
    assert e.url == "https://video-host.example/7un8j/video/clip+title"
    assert e.filename == "Clip title [7un8j].mp4"
    assert e.code == "7un8j"
    assert e.host == "video-host.example"
    assert e.ext == "mp4"


def test_download_provenance_pairs_by_code_despite_interleaving(tmp_path, monkeypatch):
    """Two downloads with progress noise between them must pair by shared code,
    not by proximity/order."""
    from lynchpin.sources.terminal import download_provenance
    _patch_root(monkeypatch, tmp_path)
    _write_cast(tmp_path / "session.cast", outputs=[
        (1.0, "Extracting URL: https://video-host-a.example/view_video.php?viewkey=vh_AAA\r\n"),
        (1.1, "Extracting URL: https://video-host-b.example/videos/clip-zzz123\r\n"),
        (2.0, "[download]  12.3% of 50MiB\r"),
        (2.5, 'Merging formats into "Second [zzz123].webm"\r\n'),
        (3.0, "[download] Destination: First [vh_AAA].mp4\r\n"),
    ])
    evs = {e.code: e for e in download_provenance()}
    assert set(evs) == {"vh_AAA", "zzz123"}
    assert evs["vh_AAA"].url.endswith("viewkey=vh_AAA")
    assert evs["zzz123"].host == "video-host-b.example"


def test_download_provenance_date_filter_skips_out_of_window(tmp_path, monkeypatch):
    from lynchpin.sources.terminal import download_provenance
    _patch_root(monkeypatch, tmp_path)
    # timestamp 1704067200 = 2024-01-01 UTC, outside the 2026 window.
    _write_cast(tmp_path / "old.cast", timestamp=1704067200, outputs=[
        (1.0, "Extracting URL: https://video-host.example/9aaaa/video/x\r\n"),
        (1.5, "[download] Destination: X [9aaaa].mp4\r\n"),
    ])
    assert list(download_provenance(start=date(2026, 1, 1), end=date(2026, 12, 31))) == []


# ---------------------------------------------------------------------------
# daily_terminal_activity — logical-date bucketing (fix: was calendar .date())
# ---------------------------------------------------------------------------

def _make_session(start: datetime, duration_s: float = 60.0, cwd: str = "/home/sinity") -> ShellSession:
    end = start + timedelta(seconds=duration_s)
    return ShellSession(
        cwd=cwd,
        project=None,
        start=start,
        end=end,
        duration_s=duration_s,
        command_count=2,
        error_count=0,
        commands_summary=("git", "ls"),
        category="home",
    )


def test_daily_terminal_activity_post_midnight_belongs_to_previous_logical_day(monkeypatch):
    """A session starting at 03:00 local (before 06:00 boundary) must be
    attributed to the *previous* calendar day, not the calendar day of start.

    Before the fix this used s.start.date() (calendar), so 03:00 on May 15
    would create a row for May 15. After the fix it uses logical_date(s.start),
    so 03:00 local May 15 (= 01:00 UTC May 15 for UTC+2) → logical day May 14.
    """
    from lynchpin.core.parse import local_tz

    tz = local_tz()
    # 03:00 local on May 15 — before the 06:00 boundary so logical day = May 14
    post_midnight = datetime(2026, 5, 15, 3, 0, tzinfo=tz)
    session = _make_session(post_midnight)

    monkeypatch.setattr(
        "lynchpin.sources.terminal.shell_sessions",
        lambda **kw: [session],
    )

    result = daily_terminal_activity(start=date(2026, 5, 14), end=date(2026, 5, 14))
    assert len(result) == 1
    assert result[0].date == date(2026, 5, 14), (
        "Post-midnight session must be bucketed to the logical day (May 14), "
        "not the calendar day of start (May 15)"
    )


def test_daily_terminal_activity_out_of_range_session_dropped(monkeypatch):
    """A session that logical_date maps outside [start, end] must be dropped.

    Before the fix, calendar .date() on a 01:00 UTC session at UTC+2 put it
    on the next logical day (outside the requested window), creating a spurious
    extra row. After the fix it is clamped and dropped.
    """
    from lynchpin.core.parse import local_tz

    tz = local_tz()
    # 03:00 local on May 16 — logical day May 15, outside [May 14, May 14]
    out_of_range = datetime(2026, 5, 16, 3, 0, tzinfo=tz)
    session = _make_session(out_of_range)

    monkeypatch.setattr(
        "lynchpin.sources.terminal.shell_sessions",
        lambda **kw: [session],
    )

    result = daily_terminal_activity(start=date(2026, 5, 14), end=date(2026, 5, 14))
    assert result == [], "Session whose logical date is outside [start, end] must be dropped"


def test_daily_terminal_activity_normal_daytime_session_unaffected(monkeypatch):
    """Sessions during normal hours (after 06:00) still land on their calendar day."""
    from lynchpin.core.parse import local_tz

    tz = local_tz()
    noon = datetime(2026, 5, 15, 12, 0, tzinfo=tz)
    session = _make_session(noon)

    monkeypatch.setattr(
        "lynchpin.sources.terminal.shell_sessions",
        lambda **kw: [session],
    )

    result = daily_terminal_activity(start=date(2026, 5, 15), end=date(2026, 5, 15))
    assert len(result) == 1
    assert result[0].date == date(2026, 5, 15)


def test_daily_terminal_activity_can_skip_ensure(monkeypatch):
    calls = []

    def fake_shell_sessions(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("lynchpin.sources.terminal.shell_sessions", fake_shell_sessions)

    assert daily_terminal_activity(
        start=date(2026, 5, 15),
        end=date(2026, 5, 15),
        ensure=False,
    ) == []
    assert calls[0]["ensure"] is False
