#!/usr/bin/env python3

import json
import re
import threading
import time
from collections.abc import Iterable, Iterator
from collections import Counter
from contextlib import ExitStack
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import typer

from lynchpin.system import life_timeline as lt


app = typer.Typer(add_completion=False, no_args_is_help=True)


_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_existing_cache_ids(cache_path: Path) -> set[str]:
    if not cache_path.exists():
        return set()
    out: set[str] = set()
    with cache_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            vid = obj.get("video_id")
            if isinstance(vid, str) and vid:
                out.add(vid)
    return out


def _iter_video_ids_from_life_json(
    life_json_path: Path,
    *,
    start_month: str | None,
    end_month: str | None,
) -> Counter[str]:
    obj = json.loads(life_json_path.read_text(encoding="utf-8"))
    months = obj.get("months")
    if not isinstance(months, dict):
        raise ValueError("Expected top-level 'months' dict in life JSON")

    counts: Counter[str] = Counter()
    for month, payload in months.items():
        if not isinstance(month, str):
            continue
        if start_month and month < start_month:
            continue
        if end_month and month > end_month:
            continue
        if not isinstance(payload, dict):
            continue
        intake = payload.get("intake")
        if not isinstance(intake, dict):
            continue

        top_video_ids = intake.get("youtube_watch_history_top_video_ids") or []
        if isinstance(top_video_ids, list):
            for row in top_video_ids:
                if not isinstance(row, (list, tuple)) or len(row) != 2:
                    continue
                vid, cnt = row
                if isinstance(vid, str) and _YOUTUBE_ID_RE.match(vid) and isinstance(cnt, int) and cnt > 0:
                    counts[vid] += cnt

        # Backwards-compat fallback: older JSON stores IDs in `youtube_watch_history_top_titles`.
        top_titles = intake.get("youtube_watch_history_top_titles") or []
        if isinstance(top_titles, list):
            for row in top_titles:
                if not isinstance(row, (list, tuple)) or len(row) != 2:
                    continue
                label, cnt = row
                if isinstance(label, str) and _YOUTUBE_ID_RE.match(label) and isinstance(cnt, int) and cnt > 0:
                    counts[label] += cnt

    return counts


def _iter_takeout_seed_paths_from_life_json(life_json_path: Path) -> list[Path]:
    obj = json.loads(life_json_path.read_text(encoding="utf-8"))
    sources = obj.get("sources")
    if not isinstance(sources, dict):
        return []
    raw = sources.get("google_takeouts")
    if not isinstance(raw, list):
        return []
    out: list[Path] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(Path(item.strip()))
    return out


def _expand_takeout_parts(paths: Iterable[Path]) -> list[Path]:
    """Expand `...-001.tgz` siblings into all parts (e.g. `...-002.tgz`)."""
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        if path.suffix != ".tgz":
            resolved = str(path.resolve())
            if resolved not in seen:
                out.append(path)
                seen.add(resolved)
            continue

        stem = path.name[: -len(".tgz")]
        m = re.match(r"^(?P<prefix>.+)-(?P<part>\d{3})$", stem)
        if not m:
            resolved = str(path.resolve())
            if resolved not in seen:
                out.append(path)
                seen.add(resolved)
            continue

        prefix = m.group("prefix")
        for candidate in sorted(path.parent.glob(f"{prefix}-*.tgz")):
            if not candidate.exists():
                continue
            resolved = str(candidate.resolve())
            if resolved in seen:
                continue
            out.append(candidate)
            seen.add(resolved)
    return out


def _iter_video_ids_from_takeouts(
    takeout_paths: list[Path],
    *,
    start_month: str,
    end_month: str,
) -> Counter[str]:
    TarReader = lt.TarReader
    parse_youtube_watch_history_from_takeouts = lt.parse_youtube_watch_history_from_takeouts

    expanded = _expand_takeout_parts(takeout_paths)
    if not expanded:
        raise FileNotFoundError("No takeout archives found (expected .tgz paths).")

    with ExitStack() as stack:
        takeouts = [stack.enter_context(TarReader(path)) for path in expanded]
        _, per_month, _, _ = parse_youtube_watch_history_from_takeouts(
            takeouts=takeouts,
            start_month=start_month,
            end_month=end_month,
        )
    counts: Counter[str] = Counter()
    for month in sorted(per_month.keys()):
        counts.update(per_month[month])
    # Keep only canonical video IDs.
    return Counter({vid: cnt for vid, cnt in counts.items() if _YOUTUBE_ID_RE.match(vid) and cnt > 0})


class _RateLimiter:
    def __init__(self, *, qps: float) -> None:
        self._min_interval = 1.0 / max(qps, 0.0001)
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                sleep_s = self._next - now
                self._next += self._min_interval
            else:
                sleep_s = 0.0
                self._next = now + self._min_interval
        if sleep_s > 0:
            time.sleep(sleep_s)


def _get_session(user_agent: str) -> requests.Session:
    local = getattr(_get_session, "_local", None)
    if local is None:
        local = threading.local()
        setattr(_get_session, "_local", local)
    session = getattr(local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": user_agent})
        setattr(local, "session", session)
    return session


def _fetch_oembed(
    video_id: str,
    *,
    timeout_s: int,
    user_agent: str,
    limiter: _RateLimiter | None,
    max_retries: int,
) -> tuple[bool, dict[str, Any]]:
    url = "https://www.youtube.com/oembed"
    params = {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}

    retryable = {429, 500, 502, 503, 504}
    last_error: dict[str, Any] = {}
    for attempt in range(max(0, max_retries) + 1):
        if limiter is not None:
            limiter.wait()
        session = _get_session(user_agent)
        try:
            resp = session.get(url, params=params, timeout=timeout_s)
        except requests.RequestException as e:
            last_error = {"error": "request_exception", "detail": type(e).__name__}
            resp = None
        if resp is None:
            if attempt < max_retries:
                time.sleep(min(60.0, 2.0 * (2**attempt)))
                continue
            return False, last_error
        status = resp.status_code
        if status != 200:
            last_error = {"status": status}
            if status in retryable and attempt < max_retries:
                time.sleep(min(120.0, 2.0 * (2**attempt)))
                continue
            return False, last_error
        try:
            payload = resp.json()
        except ValueError:
            last_error = {"status": status, "error": "invalid_json"}
            if attempt < max_retries:
                time.sleep(min(120.0, 2.0 * (2**attempt)))
                continue
            return False, last_error
        if not isinstance(payload, dict):
            return False, {"status": status, "error": "unexpected_shape"}
        return True, payload
    return False, last_error


@app.command()
def enrich(
    life_json: Path = typer.Option(
        Path("artefacts/lifelog/life-timeline/monthly_life_2020-04_to_2023-04.json"),
        help="Derived life timeline JSON (used to discover takeouts and/or video IDs).",
    ),
    cache: Path = typer.Option(
        Path("artefacts/lifelog/life-timeline/youtube_oembed_cache.jsonl"),
        help="Output JSONL cache (appended; resumable).",
    ),
    start: str | None = typer.Option(None, help="Optional start month filter (YYYY-MM)."),
    end: str | None = typer.Option(None, help="Optional end month filter (YYYY-MM)."),
    max_items: int | None = typer.Option(None, help="Optional cap on number of new video IDs to fetch."),
    from_takeout_watch_history: bool = typer.Option(
        True,
        help="Discover all watched video IDs by parsing Takeout watch-history.html (not just top IDs from life JSON).",
    ),
    takeout: list[Path] = typer.Option(
        [],
        help="Optional explicit Takeout .tgz path(s). When omitted, uses `sources.google_takeouts` from life JSON.",
    ),
    qps: float = typer.Option(10.0, help="Global request rate limit (requests/second)."),
    workers: int = typer.Option(8, help="Concurrent HTTP workers."),
    max_retries: int = typer.Option(6, help="Retries for 429/5xx/network errors."),
    timeout_s: int = typer.Option(30, help="HTTP timeout per request (seconds)."),
    user_agent: str = typer.Option(
        "Mozilla/5.0 (compatible; sinity-lynchpin/youtube-oembed; +https://www.youtube.com/oembed)",
        help="HTTP User-Agent.",
    ),
    force: bool = typer.Option(False, help="Re-fetch even if video_id is already present in cache."),
) -> None:
    """Enrich YouTube video IDs into titles/channels via the public oEmbed endpoint (cached JSONL)."""

    if not life_json.exists():
        raise FileNotFoundError(str(life_json))
    if from_takeout_watch_history and (start is None or end is None):
        life_obj = json.loads(life_json.read_text(encoding="utf-8"))
        rng = life_obj.get("range") if isinstance(life_obj, dict) else None
        if isinstance(rng, dict):
            start = start or (rng.get("start_month") if isinstance(rng.get("start_month"), str) else None)
            end = end or (rng.get("end_month") if isinstance(rng.get("end_month"), str) else None)
    if from_takeout_watch_history and (start is None or end is None):
        raise ValueError("--start and --end are required (or must be present in life JSON range) when parsing Takeout.")

    cache.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_existing_cache_ids(cache) if not force else set()

    if from_takeout_watch_history:
        takeout_paths = takeout or _iter_takeout_seed_paths_from_life_json(life_json)
        id_counts = _iter_video_ids_from_takeouts(takeout_paths, start_month=start, end_month=end)
    else:
        id_counts = _iter_video_ids_from_life_json(life_json, start_month=start, end_month=end)
    wanted = [vid for vid, _ in id_counts.most_common() if force or vid not in existing]
    if max_items is not None:
        wanted = wanted[: max(0, max_items)]

    if not wanted:
        typer.echo("[youtube-oembed] nothing to do (cache already contains all discovered IDs)")
        return

    limiter = _RateLimiter(qps=qps) if qps > 0 else None
    ok_n = 0
    err_n = 0
    done_n = 0
    with cache.open("a", encoding="utf-8") as fh:
        wanted_iter: Iterator[str] = iter(wanted)

        def submit_initial(executor: ThreadPoolExecutor, pending: dict[Future[dict[str, Any]], str], n: int) -> None:
            for _ in range(n):
                video_id = next(wanted_iter, None)
                if video_id is None:
                    return
                pending[executor.submit(fetch_one, video_id)] = video_id

        def fetch_one(video_id: str) -> dict[str, Any]:
            ok, payload = _fetch_oembed(
                video_id,
                timeout_s=timeout_s,
                user_agent=user_agent,
                limiter=limiter,
                max_retries=max_retries,
            )
            row: dict[str, Any] = {
                "video_id": video_id,
                "ok": ok,
                "fetched_at": _now_iso(),
            }
            if ok:
                row.update(
                    {
                        "title": payload.get("title"),
                        "author_name": payload.get("author_name"),
                        "author_url": payload.get("author_url"),
                        "thumbnail_url": payload.get("thumbnail_url"),
                        "provider_name": payload.get("provider_name"),
                        "provider_url": payload.get("provider_url"),
                    }
                )
            else:
                row.update(payload)
            return row

        pending: dict[Future[dict[str, Any]], str] = {}
        prefetch = max(1, workers * 4)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            submit_initial(executor, pending, prefetch)
            while pending:
                done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                for fut in done:
                    video_id = pending.pop(fut)
                    try:
                        row = fut.result()
                    except Exception as e:
                        row = {"video_id": video_id, "ok": False, "fetched_at": _now_iso(), "error": type(e).__name__}
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    done_n += 1
                    if row.get("ok") is True:
                        ok_n += 1
                    else:
                        err_n += 1
                    if done_n == 1 or done_n % 250 == 0 or done_n == len(wanted):
                        typer.echo(f"[youtube-oembed] {done_n}/{len(wanted)} (ok={ok_n}, err={err_n})")
                    next_vid = next(wanted_iter, None)
                    if next_vid is not None:
                        pending[executor.submit(fetch_one, next_vid)] = next_vid


if __name__ == "__main__":
    app()
