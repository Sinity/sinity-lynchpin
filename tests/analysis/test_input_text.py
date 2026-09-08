from datetime import UTC, datetime, timedelta
from dataclasses import replace

from lynchpin.analysis.input_text import match_anchors, reconstruct_fragments
from lynchpin.sources.keylog import KeylogEvent


BASE = datetime(2026, 4, 2, 10, tzinfo=UTC)


def keys(text, offset=0):
    return [KeylogEvent(
        BASE + timedelta(seconds=offset + i), "press", "keyboard", None,
        "KEY_SPACE" if char == " " else f"KEY_{char.upper()}", True,
        source_path="synthetic.jsonl", source_line=offset + i + 1,
    ) for i, char in enumerate(text)]


def test_lossy_candidate_with_character_provenance_and_backspace():
    events = keys("ab")
    events.append(KeylogEvent(BASE + timedelta(seconds=2), "press", "keyboard", None, "KEY_BACKSPACE", True))
    events.extend(keys("c", 3))
    fragment = reconstruct_fragments(events, [])[0]
    assert fragment["text"] == "ac"
    assert fragment["characters"][-1]["event_ref"] == "synthetic.jsonl#L4"
    assert "modifier_state_missing" in fragment["flags"]
    assert "not_final_application_text" in fragment["flags"]


def test_cursor_paste_and_timeout_do_not_invent_contiguous_text():
    events = keys("ab")
    events.append(KeylogEvent(BASE + timedelta(seconds=2), "press", "keyboard", None, "KEY_LEFT", True))
    events.extend(keys("cd", 3))
    events.append(KeylogEvent(BASE + timedelta(seconds=5), "press", "keyboard", None, "KEY_V", False, has_clipboard=True))
    events.extend(keys("ef", 6))
    events.extend(keys("gh", 200))
    assert [r["text"] for r in reconstruct_fragments(events, [])] == ["ab", "cd", "ef", "gh"]


def test_anchor_matches_only_preceding_substring_and_retains_ambiguity():
    phrase = "a sufficiently long neutral example sentence"
    events = keys("prefix " + phrase + " suffix") + keys(phrase, 100) + keys(phrase, 400)
    fragments = reconstruct_fragments(events, [], max_gap_s=30)
    matches = match_anchors([dict(id="message", timestamp=(BASE + timedelta(seconds=300)).isoformat(), source_ref="human-message", text=phrase)], fragments)[0]
    assert len(matches["matches"]) == 2
    assert matches["ambiguous"]
    assert matches["matches"][0]["start"] == (BASE + timedelta(seconds=7)).isoformat()
    assert "text" not in matches["matches"][0]


def test_short_common_prefix_is_not_evidence():
    fragments = reconstruct_fragments(keys("hello world"), [])
    result = match_anchors([dict(id="a", source_ref="human-message", timestamp=(BASE + timedelta(seconds=100)).isoformat(), text="hello world another message")], fragments)
    assert result[0]["matches"] == []


def test_recorder_unchanged_space_is_a_flagged_physical_candidate():
    events = [replace(e, changed=False) if e.keycode == "KEY_SPACE" else e for e in keys("neutral draft")]
    fragment = reconstruct_fragments(events, [])[0]
    assert fragment["text"] == "neutral draft"
    assert "recorder_unchanged_space_candidate" in fragment["flags"]
    modified = [replace(e, modifiers=("CTRL",), modifier_state_known=True) if e.keycode == "KEY_SPACE" else e for e in events]
    assert [f["text"] for f in reconstruct_fragments(modified, [])] == ["neutral", "draft"]
