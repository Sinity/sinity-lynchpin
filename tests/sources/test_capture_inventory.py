"""Tests for the shallow capture-inventory catalog."""

from lynchpin.sources.capture_inventory import capture_inventory, capture_inventory_item


def test_capture_inventory_reports_missing_root_without_raising(tmp_path):
    items = capture_inventory(tmp_path)

    by_id = {item.id: item for item in items}
    assert "audio" in by_id
    audio = by_id["audio"]
    assert audio.exists is False
    assert audio.file_count == 0
    assert audio.total_bytes == 0
    assert audio.earliest is None
    assert audio.latest is None


def test_capture_inventory_counts_files_and_bounds_mtimes(tmp_path):
    audio_dir = tmp_path / "audio" / "mic"
    audio_dir.mkdir(parents=True)
    (audio_dir / "a.opus").write_bytes(b"0" * 10)
    (audio_dir / "b.opus").write_bytes(b"0" * 20)

    item = capture_inventory_item("audio", tmp_path)

    assert item.exists is True
    assert item.file_count == 2
    assert item.total_bytes == 30
    assert item.earliest is not None
    assert item.latest is not None
    assert item.earliest <= item.latest


def test_capture_inventory_flags_reserved_empty_roots(tmp_path):
    (tmp_path / "a11y").mkdir()

    item = capture_inventory_item("a11y", tmp_path)

    assert item.kind == "reserved_empty"
    assert item.exists is True
    assert item.file_count == 0


def test_capture_inventory_item_raises_on_unknown_id(tmp_path):
    try:
        capture_inventory_item("does-not-exist", tmp_path)
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown capture id")


def test_capture_inventory_registry_ids_are_unique():
    ids = [item.id for item in capture_inventory()]
    assert len(ids) == len(set(ids))
