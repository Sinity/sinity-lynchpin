from __future__ import annotations

import gzip
import io
import json
import os
import pytest
from pathlib import Path

from lynchpin.ingest import raindrop_archive as mod


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _complete_snapshot(root, name, title="old", note="old note"):
    path = root / name
    path.mkdir()
    for filename in ("collections-root.json", "collections-children.json", "tags.json"):
        (path / filename).write_text('{}')
    _write_jsonl(path / "raindrops-all.jsonl", [{"_id": 17, "title": title}])
    _write_jsonl(path / "raindrops-trash.jsonl", [])
    _write_jsonl(path / "highlights.jsonl", [{"raindropId": 17, "text": note}])
    (path / "EXPORT-MANIFEST.json").write_text(json.dumps({"active": 1, "trash": 0, "highlights": 1}))
    return path


def test_iter_cache_ready_ids_filters_and_survives_bad_lines(tmp_path):
    src = tmp_path / "raindrops-all.jsonl"
    src.write_text(
        "\n".join(
            [
                json.dumps({"_id": 1, "cache": {"status": "ready"}}),
                json.dumps({"_id": 2, "cache": {"status": "failed"}}),
                "{torn",
                json.dumps({"_id": 3}),
                json.dumps({"_id": 4, "cache": {"status": "ready"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert list(mod.iter_cache_ready_ids([src, tmp_path / "missing.jsonl"])) == [1, 4]


def test_retained_metadata_reads_active_trash_and_highlights(tmp_path):
    snapshot = tmp_path / "2026-09-28-api-export"
    snapshot.mkdir()
    _write_jsonl(snapshot / "raindrops-all.jsonl", [{"_id": 17, "created": "2026-01-01T00:00:00Z"}])
    _write_jsonl(snapshot / "raindrops-trash.jsonl", [{"_id": 18, "lastUpdate": "2026-02-01T00:00:00Z"}])
    _write_jsonl(snapshot / "highlights.jsonl", [{"_id": 91, "raindropId": 17, "text": "quoted"}])
    for filename in ("collections-root.json", "collections-children.json", "tags.json"):
        (snapshot / filename).write_text('{}')
    (snapshot / "EXPORT-MANIFEST.json").write_text(json.dumps({"active": 1, "trash": 1, "highlights": 1}))
    from lynchpin.sources.raindrop_live import iter_retained_bookmarks

    rows = list(iter_retained_bookmarks(raw_root=tmp_path))
    assert [(row.id, row.state) for row in rows] == [(17, "active"), (18, "trash")]
    assert rows[0].created is not None and rows[0].created.year == 2026
    assert rows[0].highlights[0]["text"] == "quoted"


def test_download_is_resumable_and_records_errors(tmp_path, monkeypatch):
    src = tmp_path / "all.jsonl"
    _write_jsonl(src, [{"_id": i, "cache": {"status": "ready"}} for i in (1, 2, 3)])
    out = tmp_path / "copies"
    out.mkdir()
    (out / "1.html.gz").write_bytes(b"already-here")

    def fake_request(url, token, **kwargs):
        rid = int(url.rsplit("/", 2)[-2])
        if rid == 3:
            raise RuntimeError("giving up: IncompleteRead")
        return b"payload-" + str(rid).encode()

    monkeypatch.setattr(mod, "_request_bytes", fake_request)
    summary = mod.download_permanent_copies(
        [src], out, "tok", delay_s=0.0, log=open("/dev/null", "w")
    )
    assert summary == {"eligible": 3, "downloaded": 1, "skipped": 1, "errors": 1}
    assert (out / "2.bin").read_bytes() == b"payload-2"
    errors = [json.loads(line) for line in (out / "errors.jsonl").read_text().splitlines()]
    assert errors[0]["id"] == 3 and "IncompleteRead" in errors[0]["err"]


def test_only_ids_restricts_the_run(tmp_path, monkeypatch):
    src = tmp_path / "all.jsonl"
    _write_jsonl(src, [{"_id": i, "cache": {"status": "ready"}} for i in (7, 8, 9)])
    out = tmp_path / "copies"
    fetched: list[int] = []

    def fake_request(url, token, **kwargs):
        fetched.append(int(url.rsplit("/", 2)[-2]))
        return b"x"

    monkeypatch.setattr(mod, "_request_bytes", fake_request)
    summary = mod.download_permanent_copies(
        [src], out, "tok", only_ids={8}, delay_s=0.0, log=open("/dev/null", "w")
    )
    assert fetched == [8]
    assert summary["eligible"] == 1 and summary["downloaded"] == 1


def test_request_bytes_retries_transient_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"ok"

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionResetError("boom")
        return FakeResponse()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    assert mod._request_bytes("https://x/y", "tok") == b"ok"
    assert calls["n"] == 3


def test_sniff_extension_covers_observed_corpus_types():
    assert mod.sniff_extension(b"\x1f\x8b\x08") == ".html.gz"
    assert mod.sniff_extension(b"%PDF-1.7") == ".pdf"
    assert mod.sniff_extension(b"<!DOCTYPE html>") == ".html"
    assert mod.sniff_extension(b"  <html>") == ".html"
    assert mod.sniff_extension(b"\xff\xd8\xff\xe0") == ".jpg"
    assert mod.sniff_extension(b"\x89PNG\r\n") == ".png"
    assert mod.sniff_extension(b"RIFF....") == ".webp"
    assert mod.sniff_extension(b"\x00\x01") == ".bin"


def test_download_names_by_sniffed_type_and_resumes_across_extensions(tmp_path, monkeypatch):
    src = tmp_path / "all.jsonl"
    _write_jsonl(src, [{"_id": i, "cache": {"status": "ready"}} for i in (1, 2)])
    out = tmp_path / "copies"
    out.mkdir()
    (out / "1.pdf").write_bytes(b"%PDF-existing")  # resumable under non-gz name

    monkeypatch.setattr(mod, "_request_bytes", lambda url, token, **kw: b"<!DOCTYPE html><p>hi")
    summary = mod.download_permanent_copies(
        [src], out, "tok", delay_s=0.0, log=open("/dev/null", "w")
    )
    assert summary == {"eligible": 2, "downloaded": 1, "skipped": 1, "errors": 0}
    assert (out / "2.html").exists() and not (out / "2.html.gz").exists()


def test_rename_by_content_repairs_mislabeled_corpus(tmp_path):
    (tmp_path / "10.html.gz").write_bytes(b"\x1f\x8b\x08 real gzip")
    (tmp_path / "11.html.gz").write_bytes(b"<!DOCTYPE html><p>plain")
    (tmp_path / "12.html.gz").write_bytes(b"%PDF-1.4 ...")
    renamed = mod.rename_by_content(tmp_path)
    assert renamed == {".html": 1, ".pdf": 1}
    assert (tmp_path / "10.html.gz").exists()
    assert (tmp_path / "11.html").exists()
    assert (tmp_path / "12.pdf").exists()


def test_failed_copy_write_remains_retryable(tmp_path, monkeypatch):
    src = tmp_path / "all.jsonl"
    _write_jsonl(src, [{"_id": 7, "cache": {"status": "ready"}}])
    out = tmp_path / "copies"
    monkeypatch.setattr(mod, "_request_bytes", lambda *args: b"<html>complete</html>")
    original = Path.write_bytes
    failed = False

    def fail_once(path, data):
        nonlocal failed
        if not failed:
            failed = True
            original(path, data[:5])
            raise OSError("injected interrupted write")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_once)
    first = mod.download_permanent_copies([src], out, "fixture", delay_s=0, log=io.StringIO())
    assert first["errors"] == 1
    assert mod.existing_copy(out, 7) is None
    second = mod.download_permanent_copies([src], out, "fixture", delay_s=0, log=io.StringIO())
    assert second["downloaded"] == 1 and second["skipped"] == 0
    assert (out / "7.html").read_bytes() == b"<html>complete</html>"


def test_verify_copies_checks_entire_gzip_and_continues(tmp_path):
    payload = gzip.compress(b"fixture" * 100_000)
    (tmp_path / "7.html.gz").write_bytes(payload[:-8])
    (tmp_path / "8.html.gz").write_bytes(payload)
    assert mod.verify_copies(tmp_path, [7, 8]) == [7]


def test_retained_reader_ignores_incomplete_newest_export(tmp_path):
    old = _complete_snapshot(tmp_path, "2026-08-01-api-export")
    incomplete = _complete_snapshot(tmp_path, "2026-08-02-api-export", "unfinished")
    (incomplete / "EXPORT-MANIFEST.json").unlink()
    rows = list(mod.iter_retained_metadata(tmp_path))
    assert rows[0]["title"] == "old" and rows[0]["_lynchpin_snapshot"] == str(old)


def test_retained_join_resolves_snapshot_once(tmp_path, monkeypatch):
    from lynchpin.sources import raindrop_live
    _complete_snapshot(tmp_path, "2026-08-01-api-export")
    original = raindrop_live.iter_retained_highlights

    def publish_between_reads(*args, **kwargs):
        yield from original(*args, **kwargs)
        _complete_snapshot(tmp_path, "2026-08-02-api-export", "new", "new note")

    monkeypatch.setattr(raindrop_live, "iter_retained_highlights", publish_between_reads)
    row = list(raindrop_live.iter_retained_bookmarks(raw_root=tmp_path))[0]
    assert (row.metadata["title"], row.highlights[0]["text"]) == ("old", "old note")


def test_failed_reexport_preserves_completed_snapshot(tmp_path, monkeypatch):
    old = _complete_snapshot(tmp_path, "2026-08-01-api-export")
    before = {p.name: p.read_bytes() for p in old.iterdir()}

    def fail_after_first_response(path, token):
        if path == "/collections/childrens":
            raise OSError("injected export interruption")
        return {"items": [{"title": "new"}]}

    monkeypatch.setattr(mod, "_get_json", fail_after_first_response)
    with pytest.raises(OSError, match="export interruption"):
        mod.export_metadata(old, "fixture")
    assert {p.name: p.read_bytes() for p in old.iterdir()} == before
    assert list(mod.iter_retained_metadata(tmp_path))[0]["title"] == "old"


def test_old_marker_cannot_validate_partially_rewritten_export(tmp_path):
    old = _complete_snapshot(tmp_path, "2026-08-01-api-export")
    newer = _complete_snapshot(tmp_path, "2026-08-02-api-export", "new")
    rewritten = newer / "collections-root.json"
    published_ns = (newer / "EXPORT-MANIFEST.json").stat().st_mtime_ns
    rewritten.write_text('{"items": ["partial replacement"]}')
    os.utime(rewritten, ns=(published_ns + 1, published_ns + 1))
    assert mod.select_retained_snapshot(tmp_path) == old


def test_successful_reexport_publishes_distinct_validated_bundle(tmp_path, monkeypatch):
    old = _complete_snapshot(tmp_path, "2026-08-01-api-export")
    before = {p.name: p.read_bytes() for p in old.iterdir()}

    def response(path, token):
        if "page=" in path:
            if path.endswith("page=0"):
                if path.startswith("/raindrops/0?"):
                    return {"items": [{"_id": 17, "title": "new"}]}
                if path.startswith("/highlights?"):
                    return {"items": [{"raindropId": 17, "text": "new note"}]}
            return {"items": []}
        return {"items": []}

    monkeypatch.setattr(mod, "_get_json", response)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    published, counts = mod.export_metadata(old, "fixture")
    assert published != old
    assert counts == {"active": 1, "trash": 0, "highlights": 1}
    assert {p.name: p.read_bytes() for p in old.iterdir()} == before
    assert mod.select_retained_snapshot(tmp_path) == published
    from lynchpin.sources.raindrop_live import iter_retained_bookmarks
    row = list(iter_retained_bookmarks(raw_root=tmp_path))[0]
    assert (row.metadata["title"], row.highlights[0]["text"]) == ("new", "new note")
    # A payload edited while preserving its timestamp still fails its digest.
    payload = published / "tags.json"
    stamp = payload.stat()
    payload.write_text('{"items": ["changed"]}')
    os.utime(payload, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert mod.select_retained_snapshot(tmp_path) == old


def test_unmarked_export_remains_retained_and_explicitly_unavailable(tmp_path):
    from lynchpin.core.errors import SourceUnavailableError
    path = _complete_snapshot(tmp_path, "2026-08-01-api-export")
    (path / "EXPORT-MANIFEST.json").unlink()
    with pytest.raises(SourceUnavailableError, match="unmarked packages remain retained"):
        list(mod.iter_retained_metadata(tmp_path))
    assert path.is_dir() and (path / "raindrops-all.jsonl").is_file()


def test_cli_copy_stage_uses_newly_published_metadata_bundle(tmp_path, monkeypatch):
    published = _complete_snapshot(tmp_path, "2026-08-01-generation-api-export")
    observed = []
    monkeypatch.setattr(mod, "raindrop_archive_token", lambda: "fixture")
    monkeypatch.setattr(mod, "export_metadata", lambda *args: (published, {"active": 1, "trash": 0, "highlights": 1}))

    def copies(sources, *args, **kwargs):
        observed.extend(sources)
        return {"errors": 0}

    monkeypatch.setattr(mod, "download_permanent_copies", copies)
    assert mod.main(["--raw-root", str(tmp_path), "--snapshot-date", "2026-08-01", "--metadata", "--copies"]) == 0
    assert observed == [published / "raindrops-all.jsonl", published / "raindrops-trash.jsonl"]


def test_retained_highlights_use_bookmark_reference_not_highlight_id(tmp_path):
    from lynchpin.sources.raindrop_live import iter_retained_bookmarks
    path = _complete_snapshot(tmp_path, "2026-08-01-api-export")
    _write_jsonl(path / "highlights.jsonl", [{"_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "raindropRef": 17, "text": "fixture quote"}])
    marker = path / "EXPORT-MANIFEST.json"
    marker.write_text(marker.read_text())
    row = list(iter_retained_bookmarks(raw_root=tmp_path))[0]
    assert row.id == 17 and row.highlights == ({"_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "raindropRef": 17, "text": "fixture quote"},)


def test_highlight_id_alone_does_not_invent_bookmark_join(tmp_path):
    from lynchpin.core.errors import SourceUnavailableError
    from lynchpin.sources.raindrop_live import iter_retained_bookmarks
    path = _complete_snapshot(tmp_path, "2026-08-01-api-export")
    _write_jsonl(path / "highlights.jsonl", [{"_id": 17, "text": "unattributed"}])
    marker = path / "EXPORT-MANIFEST.json"
    marker.write_text(marker.read_text())
    with pytest.raises(SourceUnavailableError, match="parent bookmark reference"):
        list(iter_retained_bookmarks(raw_root=tmp_path))


def test_copied_older_marker_does_not_win_over_newer_export_date(tmp_path):
    old = _complete_snapshot(tmp_path, "2026-08-01-api-export")
    newer = _complete_snapshot(tmp_path, "2026-08-02-api-export", "new")
    marker = old / "EXPORT-MANIFEST.json"
    stamp = (newer / "EXPORT-MANIFEST.json").stat().st_mtime_ns + 10_000
    os.utime(marker, ns=(stamp, stamp))
    assert mod.select_retained_snapshot(tmp_path) == newer


def test_malformed_api_page_cannot_publish_an_empty_export(tmp_path, monkeypatch):
    old = _complete_snapshot(tmp_path, "2026-08-01-api-export")
    monkeypatch.setattr(mod, "_get_json", lambda *args: {"result": True})
    with pytest.raises(RuntimeError, match="no items array"):
        mod.export_metadata(old, "fixture")
    assert mod.select_retained_snapshot(tmp_path) == old


def test_false_api_result_is_failure(monkeypatch):
    monkeypatch.setattr(mod, "_request_bytes", lambda *args: b'{"result": false}')
    with pytest.raises(RuntimeError, match="rejected request"):
        mod._get_json("/collections", "fixture")
