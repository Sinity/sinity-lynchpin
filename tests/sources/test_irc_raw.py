"""Tests for IRC raw-log source (irc_raw.py)."""

from datetime import date, datetime, timezone
import json

from lynchpin.sources import irc_raw


# ── Fixtures ───────────────────────────────────────────────────────────────────

_WEE_CHAT_SAMPLE = """\
2026-04-21 10:00:00	alice	hello everyone
2026-04-21 10:00:15	bob	hey Alice, what's up?
2026-04-21 10:00:30	alice	bob: just working on the parser
2026-04-21 10:01:00	--	tantalum.libera.chat: *** some server message
2026-04-21 10:01:30	alice	anyone seen the new release?
2026-04-21 11:30:00	carol	alice: yes, it's great
2026-04-21 11:30:15	alice	carol, awesome, thanks
2026-04-21 11:31:00	dave	late to the party
"""


def _write_weechat_log(dir_path, channel: str, name: str, content: str):
    ch_dir = dir_path / channel
    ch_dir.mkdir(parents=True, exist_ok=True)
    (ch_dir / name).write_text(content)


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_iter_messages_parses_weechat_format(tmp_path):
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", _WEE_CHAT_SAMPLE)

    msgs = list(irc_raw.iter_messages(root=tmp_path))

    assert len(msgs) == 8
    human = [m for m in msgs if not m.is_meta]
    assert len(human) == 7
    assert human[0].speaker == "alice"
    assert human[0].text == "hello everyone"
    assert human[0].channel == "#test"
    assert human[0].word_count == 2


def test_iter_messages_filters_by_channel(tmp_path):
    _write_weechat_log(tmp_path, "#chan1", "2026-04-21.log", _WEE_CHAT_SAMPLE)
    _write_weechat_log(tmp_path, "#chan2", "2026-04-21.log",
                       "2026-04-21 12:00:00\tx\ty\n")

    msgs = list(irc_raw.iter_messages(channel="#chan1", root=tmp_path))
    speakers = {m.speaker for m in msgs}
    assert "x" not in speakers


def test_iter_messages_prefers_materialized_events(monkeypatch, tmp_path):
    calls = []
    product = tmp_path / "irc-events.ndjson"
    product.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-21T10:00:00+00:00",
                "speaker_raw": "alice",
                "text": "from product",
                "channel": "#test",
                "source_file": "/raw/#test/2026-04-21.log",
                "line_no": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(irc_raw, "irc_events_path", lambda root=None: product)
    monkeypatch.setattr(irc_raw, "irc_raw_root", lambda: tmp_path / "missing-raw")
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    msgs = list(irc_raw.iter_messages())

    assert calls == [("irc", None)]
    assert len(msgs) == 1
    assert msgs[0].text == "from product"
    assert msgs[0].source_file == "/raw/#test/2026-04-21.log"


def test_iter_messages_in_range_filters_by_date(tmp_path):
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", _WEE_CHAT_SAMPLE)

    msgs = list(irc_raw.iter_messages_in_range(
        start=date(2026, 4, 21), end=date(2026, 4, 21), root=tmp_path,
    ))
    assert len(msgs) == 8

    msgs_empty = list(irc_raw.iter_messages_in_range(
        start=date(2025, 1, 1), end=date(2025, 1, 1), root=tmp_path,
    ))
    assert len(msgs_empty) == 0


def test_is_meta_flags_server_lines(tmp_path):
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", _WEE_CHAT_SAMPLE)

    msgs = list(irc_raw.iter_messages(root=tmp_path))
    meta = [m for m in msgs if m.is_meta]
    assert len(meta) == 1
    assert meta[0].speaker == "--"


def test_extract_sessions_groups_by_idle_gap(tmp_path):
    content = (
        "2026-04-21 10:00:00\talice\thi\n"
        "2026-04-21 10:00:30\tbob\they\n"
        "2026-04-21 10:01:00\talice\twhat's new\n"
        # gap > 30 min → new session
        "2026-04-21 11:00:00\tcarol\tback now\n"
        "2026-04-21 11:00:15\tdave\tyep\n"
    )
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", content)

    sessions = list(irc_raw.extract_sessions(
        root=tmp_path, max_idle_minutes=30, min_messages=2,
    ))

    assert len(sessions) == 2
    assert sessions[0].message_count == 3
    assert sessions[1].message_count == 2
    assert sessions[0].channel == "#test"


def test_extract_sessions_respects_min_messages(tmp_path):
    content = (
        "2026-04-21 10:00:00\talice\tonly one msg\n"
    )
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", content)

    sessions = list(irc_raw.extract_sessions(
        root=tmp_path, max_idle_minutes=30, min_messages=2,
    ))
    assert len(sessions) == 0


def test_extract_sessions_uses_bounded_message_iterator(monkeypatch) -> None:
    calls = []
    messages = [
        irc_raw.IRCRawMessage(
            timestamp=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
            speaker="alice",
            text="hello",
            channel="#test",
            source_file="fixture",
            line_no=1,
        ),
        irc_raw.IRCRawMessage(
            timestamp=datetime(2026, 4, 21, 10, 5, tzinfo=timezone.utc),
            speaker="bob",
            text="hi",
            channel="#test",
            source_file="fixture",
            line_no=2,
        ),
    ]

    def fake_in_range(*, start, end, channel=None, root=None, ensure=True):
        calls.append((start, end, channel, root, ensure))
        yield from messages

    monkeypatch.setattr(irc_raw, "iter_messages_in_range", fake_in_range)
    monkeypatch.setattr(
        irc_raw,
        "iter_messages",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unbounded iterator should not run")),
    )

    sessions = list(
        irc_raw.extract_sessions(
            start=date(2026, 4, 21),
            end=date(2026, 4, 21),
            channel="#test",
            ensure=False,
        )
    )

    assert calls == [(date(2026, 4, 21), date(2026, 4, 21), "#test", None, False)]
    assert len(sessions) == 1


def test_speaker_stats_computes_reply_patterns(tmp_path):
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", _WEE_CHAT_SAMPLE)

    stats_list = irc_raw.speaker_stats(root=tmp_path)
    alice = next(s for s in stats_list if s.speaker == "alice")

    assert alice.message_count == 4
    assert alice.total_words > 0
    assert alice.avg_message_length > 0
    # alice replied to carol (line: "carol, awesome, thanks")
    reply_targets = dict(alice.reply_to)
    assert "carol" in reply_targets


def test_speaker_stats_uses_bounded_message_iterator(monkeypatch) -> None:
    calls = []
    messages = [
        irc_raw.IRCRawMessage(
            timestamp=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
            speaker="alice",
            text="hello bob",
            channel="#test",
            source_file="fixture",
            line_no=1,
        ),
        irc_raw.IRCRawMessage(
            timestamp=datetime(2026, 4, 21, 10, 5, tzinfo=timezone.utc),
            speaker="bob",
            text="alice: hi",
            channel="#test",
            source_file="fixture",
            line_no=2,
        ),
    ]

    def fake_in_range(*, start, end, channel=None, root=None, ensure=True):
        calls.append((start, end, channel, root, ensure))
        yield from messages

    monkeypatch.setattr(irc_raw, "iter_messages_in_range", fake_in_range)
    monkeypatch.setattr(
        irc_raw,
        "iter_messages",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unbounded iterator should not run")),
    )

    stats = irc_raw.speaker_stats(
        start=date(2026, 4, 21),
        end=date(2026, 4, 21),
        channel="#test",
        ensure=False,
    )

    assert calls == [
        (date(2026, 4, 21), date(2026, 4, 21), "#test", None, False),
        (date(2026, 4, 21), date(2026, 4, 21), "#test", None, False),
    ]
    assert {row.speaker for row in stats} == {"alice", "bob"}


def test_extract_conversations_requires_multiple_speakers(tmp_path):
    # Single-speaker session → no conversation extracted
    content = (
        "2026-04-21 10:00:00\talice\tmsg1\n"
        "2026-04-21 10:00:05\talice\tmsg2\n"
        "2026-04-21 10:00:10\talice\tmsg3\n"
        "2026-04-21 10:00:15\talice\tmsg4\n"
    )
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", content)

    convs = list(irc_raw.extract_conversations(root=tmp_path, min_speakers=2))
    assert len(convs) == 0


def test_extract_conversations_returns_dense_clusters(tmp_path):
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", _WEE_CHAT_SAMPLE)

    convs = list(irc_raw.extract_conversations(
        root=tmp_path, max_idle_minutes=5, min_speakers=2, min_messages=4,
    ))
    assert len(convs) >= 1
    conv = convs[0]
    assert conv.channel == "#test"
    assert conv.message_count >= 4
    assert conv.unique_speakers >= 2
    assert conv.conversation_id.startswith("irc-")


def test_daily_irc_activity_rolls_up_correctly(tmp_path):
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", _WEE_CHAT_SAMPLE)

    daily = irc_raw.daily_irc_activity(
        start=date(2026, 4, 21), end=date(2026, 4, 22), root=tmp_path,
    )

    assert len(daily) == 1
    day = daily[0]
    assert day.date == date(2026, 4, 21)
    assert day.total_messages == 7  # 8 total, 1 meta
    assert day.unique_speakers >= 3  # alice, bob, carol, dave
    assert day.operator_messages == 0  # no operator nicks in sample


def test_daily_irc_activity_uses_logical_day_for_materialized_events(monkeypatch, tmp_path):
    calls = []
    product = tmp_path / "irc-events.ndjson"
    product.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-06T01:00:00+00:00",
                "speaker_raw": "alice",
                "text": "late local message",
                "channel": "#test",
                "source_file": "/raw/#test/2026-06-06.log",
                "line_no": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(irc_raw, "irc_events_path", lambda root=None: product)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    daily = irc_raw.daily_irc_activity(start=date(2026, 6, 5), end=date(2026, 6, 5))

    assert calls == [("irc", (date(2026, 6, 5), date(2026, 6, 6)))]
    assert len(daily) == 1
    assert daily[0].date == date(2026, 6, 5)
    assert daily[0].total_messages == 1


def test_daily_irc_activity_can_skip_ensure(monkeypatch, tmp_path):
    product = tmp_path / "irc-events.ndjson"
    product.write_text("", encoding="utf-8")

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("pre-ensured reads must not materialize again")

    monkeypatch.setattr(irc_raw, "irc_events_path", lambda root=None: product)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fail_ensure)

    assert (
        irc_raw.daily_irc_activity(
            start=date(2026, 6, 5),
            end=date(2026, 6, 5),
            ensure=False,
        )
        == []
    )


def test_extract_conversations_can_skip_ensure(monkeypatch, tmp_path):
    product = tmp_path / "irc-events.ndjson"
    rows = [
        ("2026-04-21T10:00:00+00:00", "alice", "hello bob"),
        ("2026-04-21T10:00:30+00:00", "bob", "hey alice"),
        ("2026-04-21T10:01:00+00:00", "alice", "working on parser"),
        ("2026-04-21T10:01:30+00:00", "bob", "sounds good"),
    ]
    product.write_text(
        "\n".join(
            json.dumps(
                {
                    "timestamp": stamp,
                    "speaker_raw": speaker,
                    "text": text,
                    "channel": "#test",
                    "source_file": "/raw/#test/2026-04-21.log",
                    "line_no": line_no,
                }
            )
            for line_no, (stamp, speaker, text) in enumerate(rows, 1)
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("pre-ensured reads must not materialize again")

    monkeypatch.setattr(irc_raw, "irc_events_path", lambda root=None: product)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fail_ensure)

    conversations = list(
        irc_raw.extract_conversations(
            start=date(2026, 4, 21),
            end=date(2026, 4, 21),
            ensure=False,
        )
    )

    assert conversations


def test_daily_irc_activity_counts_operator_messages(tmp_path):
    content = (
        "2026-04-21 10:00:00\tsinity\thi everyone\n"
        "2026-04-21 10:00:30\talice\they sinity\n"
        "2026-04-21 10:01:00\tsinity\twhat's up\n"
        "2026-04-21 10:01:30\tbob\tfine here\n"
    )
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", content)

    daily = irc_raw.daily_irc_activity(
        start=date(2026, 4, 21), end=date(2026, 4, 22), root=tmp_path,
    )
    assert len(daily) == 1
    assert daily[0].total_messages == 4
    assert daily[0].operator_messages == 2  # sinity counts


def test_daily_irc_activity_empty_range(tmp_path):
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", _WEE_CHAT_SAMPLE)

    daily = irc_raw.daily_irc_activity(
        start=date(2025, 1, 1), end=date(2025, 1, 2), root=tmp_path,
    )
    assert len(daily) == 0


def test_irc_channels_lists_channel_dirs(tmp_path):
    _write_weechat_log(tmp_path, "#chan-a", "2026-04-21.log", _WEE_CHAT_SAMPLE)
    _write_weechat_log(tmp_path, "#chan-b", "2026-04-21.log", _WEE_CHAT_SAMPLE)
    # Directory starting with dot is excluded
    (tmp_path / ".config").mkdir()

    channels = irc_raw.irc_channels(root=tmp_path)
    assert "#chan-a" in channels
    assert "#chan-b" in channels
    assert ".config" not in channels


def test_session_duration_minutes(tmp_path):
    content = (
        "2026-04-21 10:00:00\talice\tmsg1\n"
        "2026-04-21 10:30:00\tbob\tmsg2\n"
    )
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", content)

    sessions = list(irc_raw.extract_sessions(root=tmp_path, max_idle_minutes=60, min_messages=2))
    assert len(sessions) == 1
    assert sessions[0].duration_minutes == 30.0


def test_conversation_messages_are_ordered(tmp_path):
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", _WEE_CHAT_SAMPLE)

    msgs = list(irc_raw.iter_messages(root=tmp_path))
    timestamps = [m.timestamp for m in msgs]
    assert timestamps == sorted(timestamps)


# ── Nick normalization tests ───────────────────────────────────────────────────


def test_normalize_nick_uses_known_aliases():
    assert irc_raw.normalize_nick("dbohdan[phone]") == "dbohdan"
    assert irc_raw.normalize_nick("dbohdan[goguma]") == "dbohdan"
    assert irc_raw.normalize_nick("+Robomot") == "robomot"
    assert irc_raw.normalize_nick("Robomot") == "robomot"
    assert irc_raw.normalize_nick("sinity2") == "sinity"
    assert irc_raw.normalize_nick("Obormot\\Arcturus") == "obormot"


def test_normalize_nick_heuristic_fallback():
    assert irc_raw.normalize_nick("someone|afk") == "someone"
    assert irc_raw.normalize_nick("nick_") == "nick"
    assert irc_raw.normalize_nick("user2") == "user"
    assert irc_raw.normalize_nick("guest[web]") == "guest"


def test_normalize_nick_unknown_is_preserved():
    assert irc_raw.normalize_nick("totally_unique_nick") == "totally_unique_nick"


# ── Speaker classification tests ───────────────────────────────────────────────


def test_classify_action_speaker():
    assert irc_raw.classify_speaker("*") == irc_raw.IRCSpeakerClass.ACTION


def test_classify_guest_speaker():
    assert irc_raw.classify_speaker("Guest12345") == irc_raw.IRCSpeakerClass.GUEST
    assert irc_raw.classify_speaker("Guest7") == irc_raw.IRCSpeakerClass.GUEST


def test_classify_bot_by_name():
    assert irc_raw.classify_speaker("feepbot") == irc_raw.IRCSpeakerClass.BOT_OTHER
    assert irc_raw.classify_speaker("--serv--") == irc_raw.IRCSpeakerClass.BOT_OTHER


def test_classify_bot_by_relay_content():
    # Build messages with <gwern> relay pattern
    msg = irc_raw.IRCRawMessage(
        timestamp=irc_raw.as_local(irc_raw.datetime(2026, 4, 21, 10, 0)),
        speaker="relaybot",
        text="<gwern> https://example.com/article",
        channel="#test",
        source_file="test.log",
        line_no=1,
    )
    msgs = [msg] * 20  # 20 messages with relay pattern
    assert irc_raw.classify_speaker("relaybot", msgs) == irc_raw.IRCSpeakerClass.BOT_RELAY


def test_classify_bot_by_url_pattern():
    msg = irc_raw.IRCRawMessage(
        timestamp=irc_raw.as_local(irc_raw.datetime(2026, 4, 21, 10, 0)),
        speaker="linkbot",
        text="Check this out: https://example.com/article and https://other.com/thing",
        channel="#test",
        source_file="test.log",
        line_no=1,
    )
    msgs = [msg] * 20
    assert irc_raw.classify_speaker("linkbot", msgs) == irc_raw.IRCSpeakerClass.BOT_LINK


def test_classify_normal_human():
    msg = irc_raw.IRCRawMessage(
        timestamp=irc_raw.as_local(irc_raw.datetime(2026, 4, 21, 10, 0)),
        speaker="alice",
        text="hey everyone, what's going on?",
        channel="#test",
        source_file="test.log",
        line_no=1,
    )
    assert irc_raw.classify_speaker("alice", [msg] * 5) == irc_raw.IRCSpeakerClass.HUMAN


# ── Speaker identity tests ─────────────────────────────────────────────────────


def test_speaker_identities_normalize_and_classify(tmp_path):
    content = (
        "2026-04-21 10:00:00\tdbohdan[phone]\tmsg1\n"
        "2026-04-21 10:01:00\tdbohdan[goguma]\tmsg2\n"
        "2026-04-21 10:02:00\tGuest12\tmsg3\n"
    )
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", content)

    identities = irc_raw.speaker_identities(root=tmp_path)

    # dbohdan[phone] and dbohdan[goguma] should be separate raw identities
    # but the canonical_nick should be the same
    dbohdan_ids = [si for si in identities if si.canonical_nick == "dbohdan"]
    assert len(dbohdan_ids) == 2  # two raw identities map to same canonical

    guest_ids = [si for si in identities if si.speaker_class == irc_raw.IRCSpeakerClass.GUEST]
    assert len(guest_ids) == 1
    assert guest_ids[0].canonical_nick == "Guest"


def test_is_meta_includes_me_actions():
    msg = irc_raw.IRCRawMessage(
        timestamp=irc_raw.as_local(irc_raw.datetime(2026, 4, 21, 10, 0)),
        speaker="*",
        text="someone waves",
        channel="#test",
        source_file="test.log",
        line_no=1,
    )
    assert msg.is_meta is True


def test_speaker_stats_uses_normalized_nicks(tmp_path):
    content = (
        "2026-04-21 10:00:00\tdbohdan[phone]\tmsg1\n"
        "2026-04-21 10:01:00\tdbohdan[goguma]\tmsg2\n"
    )
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", content)

    stats = irc_raw.speaker_stats(root=tmp_path, use_normalized=True)
    assert len(stats) == 1  # merged into one canonical identity
    assert "dbohdan" in stats[0].speaker.lower()
    assert stats[0].message_count == 2


def test_speaker_stats_raw_mode_preserves_separate_nicks(tmp_path):
    content = (
        "2026-04-21 10:00:00\tdbohdan[phone]\tmsg1\n"
        "2026-04-21 10:01:00\tdbohdan[goguma]\tmsg2\n"
    )
    _write_weechat_log(tmp_path, "#test", "2026-04-21.log", content)

    stats = irc_raw.speaker_stats(root=tmp_path, use_normalized=False)
    assert len(stats) == 2  # preserved as separate identities
