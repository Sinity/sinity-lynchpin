"""Tests for the terminal-session reconstruction join (sinnix-3w9n)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lynchpin.analysis import terminal_reconstruction as recon


def _write_session(root: Path, session_id: str, *, started_at_ms: int, events_start_ms: int | None = None) -> Path:
    session_dir = root / "2026" / "08" / "17" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session.cast").write_text(
        json.dumps({"version": 3, "term": {"cols": 80, "rows": 24}, "timestamp": started_at_ms // 1000}) + "\n",
        encoding="utf-8",
    )
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "tty": "/dev/pts/4",
                "terminal": "kitty",
                "started_at_ms": started_at_ms,
                "finished_at_ms": None,
            }
        ),
        encoding="utf-8",
    )
    if events_start_ms is not None:
        (session_dir / "events.jsonl").write_text(
            json.dumps({"type": "session_start", "ts_ms": events_start_ms, "tty": "/dev/pts/4"}) + "\n",
            encoding="utf-8",
        )
    return session_dir


def _write_kitty_meta(root: Path, *, kitty_pid: int, window_id: int, title: str, captured_at: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    ansi_name = f"{captured_at}-sinnix-prime-pid{kitty_pid}-win{window_id}-slug.ansi"
    (root / ansi_name).write_text("", encoding="utf-8")
    meta_path = root / ansi_name.replace(".ansi", ".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "window_id": window_id,
                "kitty_pid": kitty_pid,
                "title": title,
                "cwd": "/home/sinity",
                "captured_at": captured_at,
                "hostname": "sinnix-prime",
                "ansi_file": ansi_name,
            }
        ),
        encoding="utf-8",
    )
    return meta_path


def _write_screen_frame_lane(root: Path, day: str, records: list[dict]) -> Path:
    lane_dir = root / "screen-frames"
    lane_dir.mkdir(parents=True, exist_ok=True)
    path = lane_dir / f"screen-frames-{day}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for i, payload in enumerate(records):
            handle.write(
                json.dumps(
                    {
                        "host": "sinnix-prime",
                        "lane": "screen-frames",
                        "payload": payload,
                        "raw_ref": f"/frames/{i}.webp",
                        "schema": "sinnix-capture-v1",
                        "schema_version": 1,
                        "seq": i,
                        "ts": payload["ts"],
                    }
                )
                + "\n"
            )
    return path


class TestNormalizeTitle:
    def test_strips_leading_spinner_glyph(self):
        assert recon._normalize_title("◑ My task") == "My task"

    def test_strips_different_glyph_same_text(self):
        assert recon._normalize_title("✳ My task") == recon._normalize_title("◀ My task")

    def test_plain_title_unchanged(self):
        assert recon._normalize_title("My task") == "My task"


class TestReconstructSessionResolved:
    def test_joins_cast_kitty_window_and_geometry(self, tmp_path):
        session_id = "sinnix-prime-_dev_pts_4-1786988199622"
        started_at_ms = 1786988199622
        session_root = tmp_path / "asciinema"
        session_dir = _write_session(
            session_root, session_id, started_at_ms=started_at_ms, events_start_ms=started_at_ms + 80
        )

        scrollback_root = tmp_path / "kitty-scrollback"
        _write_kitty_meta(
            scrollback_root,
            kitty_pid=14568,
            window_id=37,
            title="◑ My task",
            captured_at="20260817T180000Z",
        )

        base_ts = started_at_ms / 1000 + 60
        activity_root = tmp_path / "activity"
        _write_screen_frame_lane(
            activity_root,
            "20260817",
            [
                {
                    "trigger": "hyprland-event",
                    "monitor": "DP-3",
                    "workspace": "3",
                    "window_class": "kitty",
                    "window_title": "✳ My task",
                    "geometry": {"x": 23, "y": 23, "width": 1886, "height": 2053},
                    "sha256": "abc",
                    "ts": base_ts,
                },
                {
                    # different window title -> must not join
                    "trigger": "hyprland-event",
                    "monitor": "DP-3",
                    "workspace": "3",
                    "window_class": "kitty",
                    "window_title": "◑ Some other window",
                    "geometry": {"x": 0, "y": 0, "width": 100, "height": 100},
                    "sha256": "def",
                    "ts": base_ts + 5,
                },
                {
                    # kitty-class but wrong title, and a non-kitty class row
                    "trigger": "periodic-floor",
                    "monitor": "DP-3",
                    "workspace": "2",
                    "window_class": "google-chrome",
                    "window_title": "My task",
                    "geometry": {"x": 1, "y": 1, "width": 1, "height": 1},
                    "sha256": "ghi",
                    "ts": base_ts + 10,
                },
            ],
        )

        record = recon.reconstruct_session(
            session_id,
            now=datetime.fromtimestamp(started_at_ms / 1000 + 3600, tz=timezone.utc),
            session_roots=(session_root,),
            kitty_scrollback_root=scrollback_root,
            screen_frames_root=activity_root,
            resolve_window=(14568, 37),
        )

        assert record.link_method == "live_proc_environ"
        assert record.kitty_pid == 14568
        assert record.kitty_window_id == 37
        assert record.kitty_title == "◑ My task"
        assert record.tty == "/dev/pts/4"
        assert len(record.scrollback_captures) == 1
        # Only the one screen-frames row whose normalized title matches and
        # whose window_class is kitty joins in -- the other two are excluded.
        assert len(record.geometry_timeline) == 1
        frame = record.geometry_timeline[0]
        assert frame.workspace == "3"
        assert frame.geometry == {"x": 23, "y": 23, "width": 1886, "height": 2053}
        # session.json started_at_ms vs events.jsonl session_start ts_ms.
        assert record.alignment_max_skew_s == pytest.approx(0.08, abs=0.001)
        assert record.warnings == ()
        assert record.cast_path == str(session_dir / "session.cast")

    def test_alignment_skew_over_tolerance_produces_warning(self, tmp_path):
        session_id = "sinnix-prime-_dev_pts_9-1786800000000"
        started_at_ms = 1786800000000
        session_root = tmp_path / "asciinema"
        _write_session(
            session_root,
            session_id,
            started_at_ms=started_at_ms,
            events_start_ms=started_at_ms + 2500,  # 2.5s skew, over the 1s tolerance
        )

        record = recon.reconstruct_session(
            session_id,
            session_roots=(session_root,),
            kitty_scrollback_root=tmp_path / "kitty-scrollback",
            screen_frames_root=tmp_path / "activity",
            resolve_window=None,
        )

        assert record.alignment_max_skew_s == pytest.approx(2.5)
        assert any("disagree" in w for w in record.warnings)


class TestReconstructSessionUnresolved:
    def test_no_live_window_marks_unresolved(self, tmp_path):
        session_id = "sinnix-prime-_dev_pts_5-1786700000000"
        session_root = tmp_path / "asciinema"
        _write_session(session_root, session_id, started_at_ms=1786700000000)

        record = recon.reconstruct_session(
            session_id,
            session_roots=(session_root,),
            kitty_scrollback_root=tmp_path / "kitty-scrollback",
            screen_frames_root=tmp_path / "activity",
            resolve_window=None,
        )

        assert record.link_method == "unresolved"
        assert record.kitty_pid is None
        assert record.kitty_window_id is None
        assert record.geometry_timeline == ()
        assert any("no live asciinema recorder" in w for w in record.warnings)

    def test_missing_session_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            recon.reconstruct_session("does-not-exist", session_roots=(tmp_path / "asciinema",))


class TestResolveLiveKittyWindow:
    def test_unknown_session_id_returns_none(self):
        # No live asciinema recorder on this host will ever carry a
        # SINNIX_CAPTURE_SESSION_ID matching a value this specific -- a real,
        # unmocked /proc scan that should come back empty.
        assert recon.resolve_live_kitty_window("lynchpin-test-nonexistent-session-id") is None


class TestWriteReconstruction:
    def test_writes_json_round_trip(self, tmp_path):
        record = recon.TerminalReconstruction(
            session_id="s1",
            cast_path="/x/session.cast",
            events_path=None,
            tty="/dev/pts/1",
            terminal="kitty",
            started_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
            finished_at=None,
            link_method="unresolved",
            kitty_pid=None,
            kitty_window_id=None,
            kitty_title=None,
            scrollback_captures=(),
            geometry_timeline=(),
            alignment_max_skew_s=None,
            warnings=("no live asciinema recorder process for this session_id",),
        )
        out_path = recon.write_reconstruction(record, output_dir=tmp_path)
        assert out_path == tmp_path / "s1.json"
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert loaded["session_id"] == "s1"
        assert loaded["link_method"] == "unresolved"


@pytest.mark.slow
@pytest.mark.skipif(
    recon.resolve_live_kitty_window("sinnix-prime-_dev_pts_4-1786988199622") is None,
    reason="requires a live recorder for the known real session on this host (operator machine only)",
)
class TestRealProductionSession:
    """Anti-vacuity: runs the actual join against this host's real capture data.

    This is the specific session this bead's own coordinator agent is running
    in (kitty window 37, tty /dev/pts/4). It is real evidence, not a fixture,
    and only runs on the operator's own host where that session is live.
    """

    SESSION_ID = "sinnix-prime-_dev_pts_4-1786988199622"

    def test_reconstructs_real_session_with_resolved_window_and_geometry(self):
        record = recon.reconstruct_session(self.SESSION_ID)

        assert record.link_method == "live_proc_environ"
        assert record.kitty_pid is not None
        assert record.kitty_window_id is not None
        assert record.tty == "/dev/pts/4"
        assert record.started_at is not None
        assert record.alignment_max_skew_s is not None
        assert record.alignment_max_skew_s < recon.ALIGNMENT_TOLERANCE_S
        # The whole point of the join: a real cast now has a real geometry
        # timeline attached, not an empty placeholder.
        assert len(record.geometry_timeline) > 0
        for frame in record.geometry_timeline:
            assert record.started_at <= frame.timestamp
            assert frame.geometry
