from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest

from lynchpin.core.input_snapshot import input_snapshot, pinned_version, read_input_path
from lynchpin.core.cache import file_signature


def test_replacement_keeps_bytes_and_original_identity(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text("old\n")
    before = source.stat()
    signature = file_signature(source)
    with input_snapshot() as snapshot:
        snapshot.capture((source,))
        copied = read_input_path(source)
        replacement = tmp_path / "replacement"
        replacement.write_text("new\n")
        replacement.replace(source)
        assert read_input_path(source).read_text() == "old\n"
        assert file_signature(source) == signature
        assert pinned_version(source) == (True, [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns])
        assert copied.stat().st_ino != before.st_ino
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(copy_context().run, lambda: read_input_path(source).read_text()).result() == "old\n"
    assert not copied.exists()
    assert read_input_path(source).read_text() == "new\n"


def test_absent_input_stays_absent_until_next_operation(tmp_path):
    source = tmp_path / "absent"
    with input_snapshot() as snapshot:
        snapshot.capture((source,))
        source.write_text("arrived")
        assert not read_input_path(source).exists()
        assert pinned_version(source) == (True, None)
    assert read_input_path(source).read_text() == "arrived"


def test_changed_copy_refuses_read(tmp_path):
    source = tmp_path / "input"
    source.write_text("old")
    with input_snapshot() as snapshot:
        snapshot.capture((source,))
        read_input_path(source).write_text("tampered")
        with pytest.raises(RuntimeError, match="snapshot changed"):
            read_input_path(source)


def test_change_during_capture_does_not_publish_partial_set(tmp_path, monkeypatch):
    from lynchpin.core import input_snapshot as module

    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("first")
    second.write_text("second")
    copy = module._copy_file

    def changing(source, target):
        copy(source, target)
        if source == second:
            source.write_text("changed")

    monkeypatch.setattr(module, "_copy_file", changing)
    with input_snapshot() as snapshot:
        with pytest.raises(RuntimeError, match="changed during capture"):
            snapshot.capture((first, second))
        assert snapshot.files == {}
        assert read_input_path(first) == first


def test_new_collection_member_remains_missing_for_direct_reads(tmp_path):
    root = tmp_path / "keyboard"
    root.mkdir()
    added = root / "2020-01-02.jsonl"
    with input_snapshot() as snapshot:
        snapshot.capture_collection(root, ())
        added.write_text("new")
        assert not read_input_path(added).exists()
        assert pinned_version(added) == (True, None)
    assert read_input_path(added).read_text() == "new"
