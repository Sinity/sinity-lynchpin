"""Tests for IRC and raw-log sources."""

from datetime import date

from lynchpin.sources import irc, irc_raw, raw_log


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


def test_irc_range_can_treat_end_as_exclusive(tmp_path):
    root = tmp_path / "_processed" / "sinity"
    root.mkdir(parents=True)
    (root / "0001_lesswrong_20260421T100000_20260421T101000.log").write_text(
        "=== Conversation 1 | #lesswrong | 20260421T100000 -> 20260421T101000 | sources: concat.log | sinity_lines: 1 | mentions: 1 | total: 2\n"
        "2026-04-21 10:00:00\talice\tSinity: ping\n"
        "2026-04-21 10:01:00\tsinity\tpong\n"
    )
    (root / "0002_lesswrong_20260422T100000_20260422T101000.log").write_text(
        "=== Conversation 2 | #lesswrong | 20260422T100000 -> 20260422T101000 | sources: concat.log | sinity_lines: 1 | mentions: 1 | total: 2\n"
        "2026-04-22 10:00:00\talice\tSinity: ping\n"
        "2026-04-22 10:01:00\tsinity\tpong\n"
    )

    rows = irc.conversations_in_range(
        start=date(2026, 4, 21),
        end=date(2026, 4, 22),
        root=tmp_path,
        end_exclusive=True,
    )

    assert [row.conversation_id for row in rows] == ["1"]


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


def test_raw_irc_daily_activity_counts_same_day_groups_only(tmp_path):
    channel = tmp_path / "#lesswrong"
    channel.mkdir()
    (channel / "2026-04.log").write_text(
        "2026-04-21 10:00:00\talice\tping\n"
        "2026-04-21 10:01:00\tsinity\tpong\n"
        "2026-04-21 10:02:00\talice\tmore\n"
        "2026-04-21 10:03:00\tsinity\tdone\n"
        "2026-04-22 10:00:00\tbob\tping\n"
        "2026-04-22 10:01:00\tsinity\tpong\n"
        "2026-04-22 10:02:00\tbob\tmore\n"
        "2026-04-22 10:03:00\tsinity\tdone\n",
        encoding="utf-8",
    )

    rows = irc_raw.daily_irc_activity(
        start=date(2026, 4, 21),
        end=date(2026, 4, 22),
        root=tmp_path,
    )

    assert [(row.date, row.total_messages) for row in rows] == [
        (date(2026, 4, 21), 4),
        (date(2026, 4, 22), 4),
    ]
    assert [row.session_count for row in rows] == [1, 1]
    assert [row.conversation_count for row in rows] == [1, 1]


def test_raw_log_extracts_substance_entries(tmp_path):
    """Extract substance entries from raw-log."""
    path = tmp_path / "logs.raw-log.md"
    path.write_text(
        "# Raw log\n\n"
        "- **2026-04-21 10:00:00** took 100 mg caffeine\n"
        "- **2026-04-21 11:00:00** some random thought\n"
        "- **2026-04-21 12:00:00** coffee 25mg in latte\n"
        "- **2026-04-21 13:00:00** another thought\n"
    )

    entries = list(raw_log.substance_entries(path=path))

    assert len(entries) == 2
    assert entries[0].substance == "caffeine"
    assert entries[0].dose_mg == 100.0
    assert entries[1].substance == "coffee"
    assert entries[1].dose_mg == 25.0


def test_raw_log_extracts_subjective_entries(tmp_path):
    """Extract and classify subjective entries from raw-log."""
    path = tmp_path / "logs.raw-log.md"
    path.write_text(
        "# Raw log\n\n"
        "- **2026-04-21 10:00:00** want to finish the PR\n"
        "- **2026-04-21 11:00:00** 100 mg caffeine\n"
        "- **2026-04-21 12:00:00** I realized the bug is in the parser\n"
        "- **2026-04-21 13:00:00** decided to use the new refactored version\n"
        "- **2026-04-21 14:00:00** on the screen right now is the dashboard\n"
    )

    entries = list(raw_log.subjective_entries(path=path))

    assert len(entries) == 4  # All except the substance entry
    assert entries[0].kind == "intent"
    assert entries[0].body == "want to finish the PR"
    assert entries[1].kind == "reflection"
    assert entries[2].kind == "decision"
    assert entries[3].kind == "observation"


def test_raw_log_subjective_entries_skips_substance(tmp_path):
    """Subjective entries should not include substance entries."""
    path = tmp_path / "logs.raw-log.md"
    path.write_text(
        "# Raw log\n\n"
        "- **2026-04-21 10:00:00** 50mg caffeine\n"
        "- **2026-04-21 11:00:00** feeling focused\n"
    )

    subjective = list(raw_log.subjective_entries(path=path))
    substance = list(raw_log.substance_entries(path=path))

    assert len(substance) == 1
    assert len(subjective) == 1
    assert subjective[0].body == "feeling focused"
