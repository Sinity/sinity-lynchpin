from __future__ import annotations

import json

from lynchpin.ingest import raindrop_archive as mod


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


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
