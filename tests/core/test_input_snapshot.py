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
        snapshot.pin_collection(root, ())
        added.write_text("new")
        assert not read_input_path(added).exists()
        assert pinned_version(added) == (True, None)
    assert read_input_path(added).read_text() == "new"


def test_open_capture_pins_prefix_while_append_continues(tmp_path, monkeypatch):
    from lynchpin.core import input_snapshot as module
    source = tmp_path / "open.jsonl"
    source.write_bytes(b"old\n")
    before = source.stat()
    copy = module._copy_prefix
    def append(source, target, length):
        copy(source, target, length)
        with source.open("ab") as stream:
            stream.write(b"new\n")
    monkeypatch.setattr(module, "_copy_prefix", append)
    with input_snapshot() as snapshot:
        snapshot.pin_collection(tmp_path, (source,), append_only=(source,))
        assert read_input_path(source).read_bytes() == b"old\n"
        assert pinned_version(source)[1][2:] == [before.st_size, before.st_mtime_ns, before.st_ctime_ns]
    assert source.read_bytes() == b"old\nnew\n"


@pytest.mark.parametrize("mutation", ["rewrite", "truncate", "replace"])
def test_open_capture_refuses_non_append_changes(tmp_path, monkeypatch, mutation):
    from lynchpin.core import input_snapshot as module
    source = tmp_path / "open.jsonl"
    source.write_bytes(b"old\n")
    copy = module._copy_prefix
    def change(source, target, length):
        copy(source, target, length)
        if mutation == "replace":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"old\nnew\n")
            replacement.replace(source)
        else:
            source.write_bytes(b"new\nmore\n" if mutation == "rewrite" else b"")
    monkeypatch.setattr(module, "_copy_prefix", change)
    with input_snapshot() as snapshot:
        with pytest.raises(RuntimeError, match="changed during capture"):
            snapshot.capture((source,), append_only=(source,))
        assert snapshot.files == {}


def test_collection_members_are_copied_only_when_read(tmp_path):
    root = tmp_path / "days"
    root.mkdir()
    old, new = root / "2020-01-01.jsonl", root / "2020-01-02.jsonl"
    old.write_text("old\n")
    new.write_text("new\n")
    before = old.stat()
    with input_snapshot() as snapshot:
        snapshot.pin_collection(root, (old, new))
        assert pinned_version(old) == (True, [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns])
        assert snapshot.files == {}
        assert read_input_path(new).read_text() == "new\n"
        assert set(snapshot.files) == {new}


def test_closed_member_changed_before_first_read_refuses(tmp_path):
    root = tmp_path / "days"
    root.mkdir()
    member = root / "2020-01-01.jsonl"
    member.write_text("old\n")
    with input_snapshot() as snapshot:
        snapshot.pin_collection(root, (member,))
        member.write_text("rewritten\n")
        with pytest.raises(RuntimeError, match="member changed before capture"):
            read_input_path(member)


def test_open_member_copies_pinned_prefix_after_growth(tmp_path):
    root = tmp_path / "days"
    root.mkdir()
    member = root / "2020-01-01.jsonl"
    member.write_text("old\n")
    with input_snapshot() as snapshot:
        snapshot.pin_collection(root, (member,), append_only=(member,))
        with member.open("a") as stream:
            stream.write("new\n")
        assert read_input_path(member).read_text() == "old\n"
