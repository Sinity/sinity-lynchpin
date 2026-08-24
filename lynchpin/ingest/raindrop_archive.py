"""Archival acquisition for Raindrop.io.

Two operations, both writing dated snapshot directories under the
archival lake (``exports/raindrop/raw/``):

- **metadata export**: collections, tags, all raindrops (active + trash,
  paginated), highlights — JSON/JSONL plus an ``EXPORT-MANIFEST.json``.
- **permanent copies**: bulk-download of Raindrop's cached page snapshots
  (``cache.status == "ready"``) as ``{id}.html.gz``. Resumable (existing
  non-empty files are skipped), failures append to ``errors.jsonl``, and
  ``--only-ids`` retries specific items.

Folded from the 2026-08-02 one-off scripts (sinnix-0h2). This module is
acquisition-only; ``sources.raindrop_live`` stays the live-API read/analysis
surface and ``sources.exports_raindrop`` reads the CSV exports.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ._manifest import atomic_write_ndjson, atomic_write_text

RAINDROP_API_BASE = "https://api.raindrop.io/rest/v1"
_AGENIX_TOKEN_PATH = Path("/run/agenix/raindrop-token")


def raindrop_archive_token() -> str | None:
    for env_name in ("RAINDROP_API_TOKEN", "RAINDROP_TOKEN"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    candidates = [_AGENIX_TOKEN_PATH, get_config().local_root / "raindrop_token"]
    for path in candidates:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return None


def _request_bytes(url: str, token: str, *, timeout: float = 60.0, attempts: int = 5) -> bytes:
    """GET with retry/backoff. Retries 429, transient errors, and short reads.

    The 2026-08-02 bulk run's only 7 failures were IncompleteRead on larger
    payloads; a fresh request with backoff recovers those, so truncation is
    retried like any transient failure instead of being terminal.
    """
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(30 * (attempt + 1))
                last_error = exc
                continue
            raise
        except Exception as exc:  # noqa: BLE001 - transient network faults retried uniformly
            last_error = exc
            time.sleep(min(2**attempt * 2, 30))
    raise RuntimeError(f"giving up on {url} after {attempts} attempts: {last_error}")


def _get_json(path: str, token: str) -> dict[str, Any]:
    payload = json.loads(_request_bytes(RAINDROP_API_BASE + path, token))
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected non-object response from {path}")
    return payload


def _dump_paginated(path_template: str, out_path: Path, token: str, *, delay_s: float = 0.15) -> int:
    count = 0

    def rows() -> Iterator[dict[str, Any]]:
        nonlocal count
        page = 0
        while True:
            data = _get_json(path_template.format(page=page), token)
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                yield item
            count += len(items)
            page += 1
            time.sleep(delay_s)

    atomic_write_ndjson(out_path, rows(), dumps=lambda row: json.dumps(row, ensure_ascii=False))
    return count


def export_metadata(out_dir: Path, token: str) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out_dir / "collections-root.json", json.dumps(_get_json("/collections", token)))
    atomic_write_text(
        out_dir / "collections-children.json",
        json.dumps(_get_json("/collections/childrens", token)),
    )
    atomic_write_text(out_dir / "tags.json", json.dumps(_get_json("/tags/0", token)))
    counts = {
        "active": _dump_paginated(
            "/raindrops/0?perpage=50&page={page}", out_dir / "raindrops-all.jsonl", token
        ),
        "trash": _dump_paginated(
            "/raindrops/-99?perpage=50&page={page}", out_dir / "raindrops-trash.jsonl", token
        ),
        "highlights": _dump_paginated(
            "/highlights?perpage=50&page={page}", out_dir / "highlights.jsonl", token
        ),
    }
    manifest = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **counts,
    }
    atomic_write_text(out_dir / "EXPORT-MANIFEST.json", json.dumps(manifest, indent=1))
    return counts


def iter_cache_ready_ids(jsonl_paths: Iterable[Path]) -> Iterator[int]:
    for path in jsonl_paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (record.get("cache") or {}).get("status") == "ready":
                    yield int(record["_id"])


_MAGIC_EXTENSIONS: tuple[tuple[bytes, str], ...] = (
    (b"\x1f\x8b", ".html.gz"),
    (b"%PDF", ".pdf"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG", ".png"),
    (b"RIFF", ".webp"),
)
_KNOWN_EXTENSIONS = tuple(dict.fromkeys(ext for _, ext in _MAGIC_EXTENSIONS)) + (".html", ".bin")


def sniff_extension(head: bytes) -> str:
    """Extension for cache payload bytes as served by the Raindrop API.

    The API returns the cached document verbatim: the 2026-08-02 corpus was
    6,228 gzip / 7,751 plain HTML / 225 PDF / 9 images, so a fixed
    ``.html.gz`` name (the original script's assumption) mislabels most of
    the archive.
    """
    for magic, ext in _MAGIC_EXTENSIONS:
        if head.startswith(magic):
            return ext
    if head.lstrip()[:1] == b"<":
        return ".html"
    return ".bin"


def existing_copy(out_dir: Path, rid: int) -> Path | None:
    for ext in _KNOWN_EXTENSIONS:
        candidate = out_dir / f"{rid}{ext}"
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def download_permanent_copies(
    source_jsonls: Iterable[Path],
    out_dir: Path,
    token: str,
    *,
    only_ids: set[int] | None = None,
    delay_s: float = 0.45,
    progress_every: int = 200,
    log: Any = sys.stdout,
) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = [rid for rid in iter_cache_ready_ids(source_jsonls) if only_ids is None or rid in only_ids]
    print(f"{len(ids)} copies to fetch", file=log, flush=True)
    done = skipped = errors = 0
    with (out_dir / "errors.jsonl").open("a", encoding="utf-8") as errlog:
        for rid in ids:
            if existing_copy(out_dir, rid) is not None:
                skipped += 1
                continue
            try:
                data = _request_bytes(f"{RAINDROP_API_BASE}/raindrop/{rid}/cache", token)
                (out_dir / f"{rid}{sniff_extension(data[:16])}").write_bytes(data)
                done += 1
            except urllib.error.HTTPError as exc:
                errlog.write(json.dumps({"id": rid, "code": exc.code}) + "\n")
                errlog.flush()
                errors += 1
            except Exception as exc:  # noqa: BLE001 - recorded per item, run continues
                errlog.write(json.dumps({"id": rid, "err": str(exc)[:200]}) + "\n")
                errlog.flush()
                errors += 1
            if progress_every and (done + skipped + errors) % progress_every == 0:
                print(
                    f"{done + skipped + errors}/{len(ids)} "
                    f"(new {done}, skip {skipped}, err {errors})",
                    file=log,
                    flush=True,
                )
            time.sleep(delay_s)
    summary = {"eligible": len(ids), "downloaded": done, "skipped": skipped, "errors": errors}
    print(
        f"FINISHED: {done} downloaded, {skipped} skipped, {errors} errors of {len(ids)}",
        file=log,
        flush=True,
    )
    return summary


def verify_copies(out_dir: Path, ids: Iterable[int]) -> list[int]:
    """Return ids whose downloaded copy is missing, empty, or bad gzip."""
    bad: list[int] = []
    for rid in ids:
        path = existing_copy(out_dir, rid)
        if path is None:
            bad.append(rid)
            continue
        if path.suffix == ".gz":
            try:
                with gzip.open(path, "rb") as handle:
                    handle.read(64)
            except OSError:
                bad.append(rid)
    return bad


def rename_by_content(out_dir: Path) -> dict[str, int]:
    """One-time repair: re-extension mislabeled ``{id}.html.gz`` files by magic."""
    renamed: dict[str, int] = {}
    for path in sorted(out_dir.glob("*.html.gz")):
        with path.open("rb") as handle:
            ext = sniff_extension(handle.read(16))
        if ext == ".html.gz":
            continue
        target = path.with_name(path.name.removesuffix(".html.gz") + ext)
        if not target.exists():
            path.rename(target)
            renamed[ext] = renamed.get(ext, 0) + 1
    return renamed


def _default_raw_root() -> Path:
    return get_config().accounts_root / "raindrop" / "raw"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive Raindrop.io metadata and permanent copies")
    parser.add_argument("--raw-root", type=Path, default=None, help="exports/raindrop/raw override")
    parser.add_argument("--snapshot-date", default=date.today().isoformat())
    parser.add_argument("--metadata", action="store_true", help="run the API metadata export")
    parser.add_argument("--copies", action="store_true", help="download permanent copies")
    parser.add_argument("--no-trash", action="store_true", help="copies: skip trash items")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="copies: existing api-export dir (default <raw>/<date>-api-export)",
    )
    parser.add_argument(
        "--copies-dir",
        type=Path,
        default=None,
        help="copies: output dir (default <raw>/<date>-permanent-copies)",
    )
    parser.add_argument("--only-ids", default=None, help="copies: comma-separated raindrop ids")
    args = parser.parse_args(argv)
    if not args.metadata and not args.copies:
        parser.error("nothing to do: pass --metadata and/or --copies")

    token = raindrop_archive_token()
    if not token:
        print("no Raindrop token (RAINDROP_API_TOKEN / agenix raindrop-token)", file=sys.stderr)
        return 2

    raw_root = args.raw_root or _default_raw_root()
    api_dir = args.source_dir or raw_root / f"{args.snapshot_date}-api-export"
    if args.metadata:
        counts = export_metadata(api_dir, token)
        print(f"metadata export: {counts} -> {api_dir}")
    if args.copies:
        sources = [api_dir / "raindrops-all.jsonl"]
        if not args.no_trash:
            sources.append(api_dir / "raindrops-trash.jsonl")
        only = (
            {int(part) for part in args.only_ids.split(",") if part.strip()}
            if args.only_ids
            else None
        )
        copies_dir = args.copies_dir or raw_root / f"{args.snapshot_date}-permanent-copies"
        summary = download_permanent_copies(sources, copies_dir, token, only_ids=only)
        if summary["errors"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
