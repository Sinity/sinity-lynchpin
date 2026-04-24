"""Tests for IRC and raw-log sources."""

from datetime import date

from lynchpin.sources import irc, raw_log


def test_irc_parses_processed_conversation_file(tmp_path):
    root = tmp_path / "_processed" / "sinity"
    root.mkdir(parents=True)
    (root / "0001_lesswrong_20260421T100000_20260421T101000.log").write_text(
        "=== Conversation 1 | #lesswrong | 20260421T100000 -> 20260421T101000 | sources: concat.log | sinity_lines: 1 | mentions: 1 | total: 2\n"
        "2026-04-21 10:00:00\talice\tSinity: ping\n"
        "2026-04-21 10:01:00\tsinity\tpong\n"
    )

    rows = irc.conversations_in_range(start=date(2026, 4, 21), end=date(2026, 4, 21), root=tmp_path)

    assert len(rows) == 1
    assert rows[0].channel == "#lesswrong"
    assert rows[0].sinity_lines == 1
    assert rows[0].mention_lines == 1
    assert rows[0].messages[1].text == "pong"


def test_raw_log_parses_timestamped_entries(tmp_path):
    path = tmp_path / "logs.raw-log.md"
    path.write_text(
        "# Raw log\n\n"
        "- **2026-04-21 10:00:00** first thought\n"
        "- **2026-04-22 10:00:00** second thought\n"
    )

    rows = raw_log.entries_in_range(start=date(2026, 4, 21), end=date(2026, 4, 21), path=path)

    assert len(rows) == 1
    assert rows[0].text == "first thought"
    assert rows[0].line_no == 3
