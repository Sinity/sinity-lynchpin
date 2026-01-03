#!/usr/bin/env python3
from __future__ import annotations

import math
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
import typer
from lxml import etree, html


app = typer.Typer(add_completion=False, no_args_is_help=True)

WYKOP_BASE = "https://wykop.pl"
WYKOP_API_BASE = "https://wykop.pl/api/v3"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _open_jsonl_tmp(path: Path) -> tuple[Path, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fh = tmp.open("w", encoding="utf-8")
    return tmp, fh


def _read_last_jsonl_obj(path: Path, *, max_tail_bytes: int = 1024 * 1024) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, size - max_tail_bytes))
            tail = fh.read()
    except OSError:
        return None
    for raw in reversed(tail.splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _get(
    session: requests.Session,
    url: str,
    *,
    retries: int = 6,
    timeout_s: int = 60,
    allow_statuses: set[int] | None = None,
) -> requests.Response:
    for attempt in range(1, retries + 1):
        resp = session.get(url, timeout=timeout_s)
        if resp.status_code in {429, 500, 502, 503, 504}:
            if attempt == retries:
                resp.raise_for_status()
            backoff = min(60.0, 2.0 ** (attempt - 1))
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    backoff = max(backoff, float(retry_after))
                except ValueError:
                    pass
            typer.echo(f"[wykop] {resp.status_code} for {url} (attempt {attempt}/{retries}); sleeping {backoff:.1f}s", err=True)
            time.sleep(backoff)
            continue
        if allow_statuses is not None and resp.status_code in allow_statuses:
            return resp
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Unreachable: retries loop exhausted for {url}")


def _request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
    retries: int = 6,
    timeout_s: int = 60,
    allow_statuses: set[int] | None = None,
) -> requests.Response:
    for attempt in range(1, retries + 1):
        resp = session.request(method, url, json=json_body, params=params, timeout=timeout_s)
        if resp.status_code in {429, 500, 502, 503, 504}:
            if attempt == retries:
                resp.raise_for_status()
            backoff = min(60.0, 2.0 ** (attempt - 1))
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    backoff = max(backoff, float(retry_after))
                except ValueError:
                    pass
            typer.echo(
                f"[wykop] {resp.status_code} for {method} {url} (attempt {attempt}/{retries}); sleeping {backoff:.1f}s",
                err=True,
            )
            time.sleep(backoff)
            continue
        if allow_statuses is not None and resp.status_code in allow_statuses:
            return resp
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Unreachable: retries loop exhausted for {method} {url}")


def _page_has_prerender(html_text: str) -> bool:
    return "prerender" in html_text


def _resolve_max_page(session: requests.Session, coll: "Collection", username: str, max_page_hint: int) -> int:
    def exists(page: int) -> bool:
        resp = _get(session, coll.page_url(username, page), allow_statuses={404})
        return resp.status_code == 200 and _page_has_prerender(resp.text)

    if max_page_hint <= 1:
        if not exists(2):
            return 1

        low_good = 2
        high_bad = 4
        cap = 20000
        while high_bad <= cap and exists(high_bad):
            low_good = high_bad
            high_bad *= 2

        lo, hi = low_good, min(high_bad - 1, cap)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if exists(mid):
                lo = mid
            else:
                hi = mid - 1
        return lo

    if exists(max_page_hint):
        return max_page_hint

    low, high = 1, max_page_hint - 1
    while low < high:
        mid = (low + high + 1) // 2
        if exists(mid):
            low = mid
        else:
            high = mid - 1
    return low


def _abs_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"{WYKOP_BASE}{href}"
    return f"{WYKOP_BASE}/{href}"


def _normalise_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _same_username(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower()


def _element_to_markdown(node: etree._Element) -> str:
    def render(el: etree._Element) -> str:
        if not isinstance(el.tag, str):
            return ""

        tag = el.tag.lower()
        classes = set((el.get("class") or "").split())

        if tag in {"script", "style"}:
            return ""
        if tag == "button" and ("more" in classes or el.get("data-action") is not None):
            return ""
        if tag == "section" and "new-line" in classes:
            return "\n"
        if tag == "br":
            return "\n"
        if tag == "img":
            return ""

        if tag == "blockquote":
            inner = _normalise_ws(_render_children(el))
            if not inner:
                return ""
            lines = []
            for line in inner.splitlines():
                lines.append(f"> {line}" if line else ">")
            return "\n".join(lines)

        if tag == "a":
            href = el.get("href") or ""
            label = _normalise_ws("".join(el.itertext()))
            if not label:
                return ""

            if href.startswith("/tag/") or href.startswith("/ludzie/"):
                return label

            href_abs = _abs_url(href) if href.startswith("/") else href
            if label == href_abs or label == href:
                return href_abs
            return f"[{label}]({href_abs})"

        out = []
        if el.text:
            out.append(el.text)
        for child in el:
            out.append(render(child))
            if child.tail:
                out.append(child.tail)
        return "".join(out)

    def _render_children(el: etree._Element) -> str:
        parts = []
        if el.text:
            parts.append(el.text)
        for child in el:
            parts.append(render(child))
            if child.tail:
                parts.append(child.tail)
        return "".join(parts)

    return _normalise_ws(render(node))


_USERKEEP_RE = re.compile(rb"userKeep.{0,200}?([0-9a-f]{64})")


def _extract_refresh_token_from_leveldb(leveldb_dir: Path) -> str | None:
    if not leveldb_dir.exists():
        return None
    files = sorted(leveldb_dir.glob("*.ldb")) + sorted(leveldb_dir.glob("*.log"))
    for path in files:
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if b"userKeep" not in blob:
            continue
        if b"wykop" not in blob and b"Wykop" not in blob:
            continue
        match = _USERKEEP_RE.search(blob)
        if not match:
            continue
        try:
            return match.group(1).decode("ascii")
        except UnicodeDecodeError:
            continue
    return None


def _candidate_chrome_leveldb_dirs() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    for browser in ("google-chrome", "chromium", "BraveSoftware/Brave-Browser", "vivaldi"):
        root = home / ".config" / browser
        if not root.exists():
            continue
        profiles: list[Path] = []
        default_profile = root / "Default"
        if default_profile.exists():
            profiles.append(default_profile)
        profiles.extend(sorted(p for p in root.glob("Profile *") if p.is_dir()))
        for profile in profiles:
            leveldb = profile / "Local Storage" / "leveldb"
            if leveldb.is_dir():
                candidates.append(leveldb)
    return candidates


def _extract_refresh_token_from_chrome(leveldb_dir: Path | None) -> str | None:
    if leveldb_dir is not None:
        return _extract_refresh_token_from_leveldb(leveldb_dir)
    for candidate in _candidate_chrome_leveldb_dirs():
        token = _extract_refresh_token_from_leveldb(candidate)
        if token:
            return token
    return None


def _iter_api_items(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        yield from data
        return
    if isinstance(data, dict):
        keys = list(data.keys())
        if keys and all(isinstance(k, str) and k.isdigit() for k in keys):
            for k in sorted(keys, key=lambda v: int(v)):
                item = data.get(k)
                if isinstance(item, dict):
                    yield item
            return
        for item in data.values():
            if isinstance(item, dict):
                yield item


def _votes_score(votes: Any) -> int | None:
    if not isinstance(votes, dict):
        return None
    up = votes.get("up")
    down = votes.get("down")
    if isinstance(up, int) and isinstance(down, int):
        return up - down
    return None


def _api_link_url(link_id: int, slug: str | None) -> str:
    if slug:
        return f"{WYKOP_BASE}/link/{link_id}/{slug}"
    return f"{WYKOP_BASE}/link/{link_id}"


def _api_entry_url(entry_id: int, slug: str | None) -> str:
    if slug:
        return f"{WYKOP_BASE}/wpis/{entry_id}/{slug}"
    return f"{WYKOP_BASE}/wpis/{entry_id}"


def _api_media_photo_url(media: Any) -> str | None:
    if media is None:
        return None
    if isinstance(media, dict):
        photo = media.get("photo")
        if isinstance(photo, str):
            return photo
        if isinstance(photo, dict):
            for key in ("url", "original", "full", "raw", "large", "medium", "small"):
                val = photo.get(key)
                if isinstance(val, str) and val:
                    return val
    return None


def _api_link_meta(link_obj: dict[str, Any]) -> dict[str, Any] | None:
    link_id = link_obj.get("id")
    if not isinstance(link_id, int):
        return None
    slug = link_obj.get("slug")
    if not isinstance(slug, str):
        slug = None
    title = link_obj.get("title")
    if not isinstance(title, str):
        title = None
    created_at = link_obj.get("created_at")
    if not isinstance(created_at, str):
        created_at = None
    tags = link_obj.get("tags")
    if not isinstance(tags, list):
        tags = []
    tags_norm = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
    return {
        "link_id": link_id,
        "link_title": title,
        "link_slug": slug,
        "link_url": _api_link_url(link_id, slug),
        "link_created_at": created_at,
        "link_tags": tags_norm,
    }


def _api_entry_meta(entry_obj: dict[str, Any]) -> dict[str, Any] | None:
    entry_id = entry_obj.get("id")
    if not isinstance(entry_id, int):
        return None
    slug = entry_obj.get("slug")
    if not isinstance(slug, str):
        slug = None
    created_at = entry_obj.get("created_at")
    if not isinstance(created_at, str):
        created_at = None
    author = entry_obj.get("author") or {}
    author_username = author.get("username") if isinstance(author, dict) else None
    if not isinstance(author_username, str):
        author_username = None
    return {
        "entry_id": entry_id,
        "entry_url": _api_entry_url(entry_id, slug),
        "entry_created_at": created_at,
        "entry_author": author_username,
    }


def _parse_api_links(payload: dict[str, Any], page: int, username: str, *, kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for link_obj in _iter_api_items(payload):
        meta = _api_link_meta(link_obj)
        if meta is None:
            continue
        out.append({"platform": "wykop", "kind": kind, "username": username, "page": page, **meta})
    return out


def _parse_api_link_comments(payload: dict[str, Any], page: int, username: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for link_obj in _iter_api_items(payload):
        link_meta = _api_link_meta(link_obj)
        if link_meta is None:
            continue
        comments = (link_obj.get("comments") or {}).get("items")
        if not isinstance(comments, list):
            continue
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            author = comment.get("author") or {}
            author_username = author.get("username") if isinstance(author, dict) else None
            if not _same_username(author_username, username):
                continue
            comment_id = comment.get("id")
            if not isinstance(comment_id, int):
                continue
            created_at = comment.get("created_at")
            if not isinstance(created_at, str):
                created_at = None
            content = comment.get("content")
            if not isinstance(content, str):
                content = ""
            comment_url = f"{link_meta['link_url']}#comment-{comment_id}"
            out.append(
                {
                    "platform": "wykop",
                    "kind": "link_comment",
                    "username": username,
                    "page": page,
                    "comment_id": comment_id,
                    "comment_created_at": created_at,
                    "comment_url": comment_url,
                    "comment_content": content.strip(),
                    "comment_photo_url": _api_media_photo_url(comment.get("media")),
                    "comment_rating": _votes_score(comment.get("votes")),
                    **link_meta,
                }
            )
    return out


def _parse_api_entries(payload: dict[str, Any], page: int, username: str, *, kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry_obj in _iter_api_items(payload):
        meta = _api_entry_meta(entry_obj)
        if meta is None:
            continue
        tags = entry_obj.get("tags")
        if not isinstance(tags, list):
            tags = []
        tags_norm = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
        content = entry_obj.get("content")
        if not isinstance(content, str):
            content = ""
        votes = entry_obj.get("votes")
        votes_up = votes.get("up") if isinstance(votes, dict) else None
        votes_down = votes.get("down") if isinstance(votes, dict) else None
        out.append(
            {
                "platform": "wykop",
                "kind": kind,
                "username": username,
                "page": page,
                **meta,
                "entry_content": content.strip(),
                "entry_tags": tags_norm,
                "entry_photo_url": _api_media_photo_url(entry_obj.get("media")),
                "votes_score": _votes_score(votes),
                "votes_up": votes_up if isinstance(votes_up, int) else None,
                "votes_down": votes_down if isinstance(votes_down, int) else None,
            }
        )
    return out


def _parse_api_entry_comments(payload: dict[str, Any], page: int, username: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry_obj in _iter_api_items(payload):
        entry_meta = _api_entry_meta(entry_obj)
        if entry_meta is None:
            continue
        comments = (entry_obj.get("comments") or {}).get("items")
        if not isinstance(comments, list):
            continue
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            author = comment.get("author") or {}
            author_username = author.get("username") if isinstance(author, dict) else None
            if not _same_username(author_username, username):
                continue
            comment_id = comment.get("id")
            if not isinstance(comment_id, int):
                continue
            created_at = comment.get("created_at")
            if not isinstance(created_at, str):
                created_at = None
            content = comment.get("content")
            if not isinstance(content, str):
                content = ""
            out.append(
                {
                    "platform": "wykop",
                    "kind": "entry_comment",
                    "username": username,
                    "page": page,
                    "comment_id": comment_id,
                    "comment_created_at": created_at,
                    "comment_content": content.strip(),
                    "comment_photo_url": _api_media_photo_url(comment.get("media")),
                    "comment_rating": _votes_score(comment.get("votes")),
                    **entry_meta,
                }
            )
    return out


class WykopApiClient:
    def __init__(self, session: requests.Session, *, refresh_token: str, api_base: str = WYKOP_API_BASE) -> None:
        self._session = session
        self._api_base = api_base.rstrip("/")
        self.refresh_token = refresh_token
        self._access_token: str | None = None
        self.refresh()

    def refresh(self) -> None:
        resp = _request(
            self._session,
            "POST",
            f"{self._api_base}/refresh-token",
            json_body={"data": {"refresh_token": self.refresh_token}},
        )
        data = resp.json().get("data") if resp.headers.get("Content-Type", "").startswith("application/json") else None
        if not isinstance(data, dict):
            raise RuntimeError("Wykop refresh-token: unexpected response shape")
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Wykop refresh-token: missing access token")
        self._access_token = token
        new_refresh = data.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh:
            self.refresh_token = new_refresh
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._api_base}/{path.lstrip('/')}"
        resp = _request(self._session, "GET", url, params=params, allow_statuses={403})
        if resp.status_code == 403:
            self.refresh()
            resp = _request(self._session, "GET", url, params=params, allow_statuses={403})
        resp.raise_for_status()
        return resp.json()


def _api_iter_pages(
    api_client: WykopApiClient,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    max_pages: int | None = None,
) -> Iterable[tuple[int, dict[str, Any]]]:
    page = 1
    while True:
        merged_params = dict(params or {})
        merged_params["page"] = page
        payload = api_client.get(endpoint, params=merged_params)
        yield page, payload

        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            data_is_empty = not data
        elif isinstance(data, list):
            data_is_empty = len(data) == 0
        else:
            data_is_empty = True
        if data_is_empty:
            break

        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        total = pagination.get("total") if isinstance(pagination, dict) else None
        per_page = pagination.get("per_page") if isinstance(pagination, dict) else None
        if isinstance(total, int) and isinstance(per_page, int) and per_page > 0:
            last_page = max(1, math.ceil(total / per_page))
            if page >= last_page:
                break

        if max_pages is not None and page >= max_pages:
            break
        page += 1


def _api_iter_cursor_pages(
    api_client: WykopApiClient,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    max_pages: int | None = None,
) -> Iterable[tuple[str | None, dict[str, Any]]]:
    """Iterate endpoints whose pagination uses `pagination.next` / `pagination.prev` cursor tokens.

    Empirically, Wykop passes the cursor token in the `page` query param.
    """
    token: str | None = None
    seen: set[str] = set()
    page_idx = 0
    while True:
        merged_params = dict(params or {})
        if token is not None:
            merged_params["page"] = token
        payload = api_client.get(endpoint, params=merged_params or None)
        yield token, payload

        page_idx += 1
        if max_pages is not None and page_idx >= max_pages:
            break

        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        next_token = pagination.get("next") if isinstance(pagination, dict) else None
        if not isinstance(next_token, str) or not next_token:
            break
        if next_token in seen:
            break
        seen.add(next_token)
        token = next_token


def _scrape_api_extras(
    *,
    api_client: WykopApiClient,
    username: str,
    auth_username: str | None,
    user_dir: Path,
    delay_seconds: float,
    max_pages: int | None,
) -> dict[str, Any]:
    extras: dict[str, Any] = {"completed_at": None, "items": {}}

    def record(key: str, value: Any) -> None:
        extras["items"][key] = value

    def dump_json(
        key: str,
        endpoint: str,
        filename: str,
        *,
        params: dict[str, Any] | None = None,
        allow_statuses: set[int] | None = None,
    ) -> None:
        out_path = user_dir / filename
        try:
            payload = api_client.get(endpoint, params=params)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if allow_statuses is not None and status in allow_statuses:
                _write_json(
                    out_path,
                    {
                        "skipped": True,
                        "endpoint": endpoint,
                        "status": status,
                        "scraped_at": _now_iso(),
                        "error": str(e),
                    },
                )
                record(
                    key,
                    {
                        "output": str(out_path),
                        "endpoint": endpoint,
                        "ok": True,
                        "skipped": True,
                        "status": status,
                    },
                )
                return
            record(key, {"output": str(out_path), "endpoint": endpoint, "ok": False, "status": status, "error": str(e)})
            return
        _write_json(out_path, payload)
        record(key, {"output": str(out_path), "endpoint": endpoint, "ok": True})
        time.sleep(delay_seconds)

    include_self = auth_username is not None and _same_username(username, auth_username)
    record(
        "extras_scope",
        {
            "target_username": username,
            "auth_username": auth_username,
            "include_self_endpoints": include_self,
        },
    )

    profile_path = user_dir / "wykop_profile.json"
    try:
        profile = api_client.get(f"profile/users/{username}")
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("profile", {"output": str(profile_path), "ok": False, "status": status, "error": str(e)})
    else:
        _write_json(profile_path, profile)
        record("profile", {"output": str(profile_path), "ok": True})
        time.sleep(delay_seconds)

    badges_path = user_dir / "wykop_badges.json"
    try:
        badges = api_client.get(f"profile/users/{username}/badges")
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("badges", {"output": str(badges_path), "ok": False, "status": status, "error": str(e)})
    else:
        _write_json(badges_path, badges)
        record("badges", {"output": str(badges_path), "ok": True})
        time.sleep(delay_seconds)

    tags_path = user_dir / "wykop_tags.json"
    tags_rows: list[dict[str, Any]] = []
    pages = 0
    try:
        for page, payload in _api_iter_pages(
            api_client,
            f"profile/users/{username}/tags",
            params={},
            max_pages=max_pages,
        ):
            pages = page
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        tags_rows.append({"page": page, **item})
            elif isinstance(data, dict) and data:
                for item in data.values():
                    if isinstance(item, dict):
                        tags_rows.append({"page": page, **item})
            time.sleep(delay_seconds)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("tags", {"output": str(tags_path), "ok": False, "status": status, "error": str(e)})
    else:
        _write_json(
            tags_path,
            {
                "username": username,
                "scraped_at": _now_iso(),
                "endpoint": "profile/users/{username}/tags",
                "pages": pages,
                "items": tags_rows,
            },
        )
        record("tags", {"output": str(tags_path), "ok": True, "items": len(tags_rows), "pages": pages})

    observed_tags_path = user_dir / "wykop_observed_tags.json"
    observed_tags: list[Any] = []
    pages = 0
    try:
        for page, payload in _api_iter_pages(
            api_client,
            f"profile/users/{username}/observed/tags",
            params={},
            max_pages=max_pages,
        ):
            pages = page
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                observed_tags.extend(data)
            elif isinstance(data, dict) and data:
                # Some endpoints return a dict keyed by id.
                observed_tags.extend(data.values())
            time.sleep(delay_seconds)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("observed_tags", {"output": str(observed_tags_path), "ok": False, "status": status, "error": str(e)})
    else:
        _write_json(
            observed_tags_path,
            {
                "username": username,
                "scraped_at": _now_iso(),
                "endpoint": "profile/users/{username}/observed/tags",
                "pages": pages,
                "items": observed_tags,
            },
        )
        record("observed_tags", {"output": str(observed_tags_path), "ok": True, "items": len(observed_tags), "pages": pages})

    actions_path = user_dir / "wykop_actions.jsonl"
    actions_rows: list[dict[str, Any]] = []
    pages = 0
    try:
        for page, payload in _api_iter_pages(
            api_client,
            f"profile/users/{username}/actions",
            params={},
            max_pages=max_pages,
        ):
            pages = page
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        actions_rows.append(
                            {
                                "platform": "wykop",
                                "kind": "action",
                                "username": username,
                                "page": page,
                                **item,
                            }
                        )
            time.sleep(delay_seconds)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("actions", {"output": str(actions_path), "ok": False, "status": status, "error": str(e)})
    else:
        _write_jsonl(actions_path, actions_rows)
        record("actions", {"output": str(actions_path), "ok": True, "items": len(actions_rows), "pages": pages})

    followers_path = user_dir / "wykop_followers.json"
    followers: list[Any] = []
    pages = 0
    try:
        for page, payload in _api_iter_pages(
            api_client,
            f"profile/users/{username}/observed/users/followers",
            params={},
            max_pages=max_pages,
        ):
            pages = page
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                followers.extend(data)
            time.sleep(delay_seconds)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("followers", {"output": str(followers_path), "ok": False, "status": status, "error": str(e)})
    else:
        _write_json(
            followers_path,
            {
                "username": username,
                "scraped_at": _now_iso(),
                "endpoint": "profile/users/{username}/observed/users/followers",
                "pages": pages,
                "items": followers,
            },
        )
        record("followers", {"output": str(followers_path), "ok": True, "items": len(followers), "pages": pages})

    following_path = user_dir / "wykop_following.json"
    following: list[Any] = []
    pages = 0
    try:
        for page, payload in _api_iter_pages(
            api_client,
            f"profile/users/{username}/observed/users/following",
            params={},
            max_pages=max_pages,
        ):
            pages = page
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                following.extend(data)
            time.sleep(delay_seconds)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("following", {"output": str(following_path), "ok": False, "status": status, "error": str(e)})
    else:
        _write_json(
            following_path,
            {
                "username": username,
                "scraped_at": _now_iso(),
                "endpoint": "profile/users/{username}/observed/users/following",
                "pages": pages,
                "items": following,
            },
        )
        record("following", {"output": str(following_path), "ok": True, "items": len(following), "pages": pages})

    if not include_self:
        extras["completed_at"] = _now_iso()
        return extras

    dump_json("config", "config", "wykop_config.json")
    dump_json("profile_self", "profile", "wykop_profile_self.json")
    dump_json("profile_short", "profile/short", "wykop_profile_short.json")
    dump_json("pinned_tags", "pinned-tags", "wykop_pinned_tags.json")
    dump_json("saved_search", "saved-search", "wykop_saved_search.json")
    dump_json("notes_self", f"notes/{username}", "wykop_notes_self.json")

    dump_json("settings_general", "settings/general", "wykop_settings_general.json")
    dump_json("settings_2fa_status", "settings/2fa/status", "wykop_settings_2fa_status.json")
    dump_json("settings_email", "settings/email", "wykop_settings_email.json")
    dump_json("settings_phone", "settings/phone", "wykop_settings_phone.json")
    dump_json(
        "settings_changephone",
        "settings/changephone",
        "wykop_settings_changephone.json",
        allow_statuses={404},
    )
    dump_json("settings_applications", "settings/applications", "wykop_settings_applications.json")
    dump_json("settings_sessions", "settings/session", "wykop_settings_sessions.json")
    dump_json("settings_blacklists_stats", "settings/blacklists/stats", "wykop_settings_blacklists_stats.json")

    for key, endpoint, filename in [
        ("settings_blacklists_domains", "settings/blacklists/domains", "wykop_settings_blacklists_domains.jsonl"),
        ("settings_blacklists_tags", "settings/blacklists/tags", "wykop_settings_blacklists_tags.jsonl"),
        ("settings_blacklists_users", "settings/blacklists/users", "wykop_settings_blacklists_users.jsonl"),
    ]:
        out_path = user_dir / filename
        rows: list[dict[str, Any]] = []
        pages = 0
        try:
            for page, payload in _api_iter_pages(api_client, endpoint, params={}, max_pages=max_pages):
                pages = page
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            rows.append(
                                {"platform": "wykop", "kind": "setting_item", "endpoint": endpoint, "page": page, **item}
                            )
                time.sleep(delay_seconds)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            record(key, {"output": str(out_path), "endpoint": endpoint, "ok": False, "status": status, "error": str(e)})
            continue
        _write_jsonl(out_path, rows)
        record(key, {"output": str(out_path), "endpoint": endpoint, "ok": True, "items": len(rows), "pages": pages})

    for key, endpoint, filename in [
        ("observed_all", "observed/all", "wykop_observed_all.jsonl"),
        ("observed_users", "observed/users", "wykop_observed_users.jsonl"),
        ("observed_tags_stream", "observed/tags/stream", "wykop_observed_tags_stream.jsonl"),
    ]:
        out_path = user_dir / filename
        rows_written = 0
        pages = 0
        start_token: str | None = None
        token: str | None = None
        mode = "fresh"
        try:
            existing = out_path.exists() and out_path.stat().st_size > 0
            if existing:
                last_obj = _read_last_jsonl_obj(out_path)
                last_token = last_obj.get("page_token") if isinstance(last_obj, dict) else None
                params = {"page": last_token} if isinstance(last_token, str) and last_token else None
                probe = api_client.get(endpoint, params=params)
                pagination = probe.get("pagination") if isinstance(probe, dict) else None
                next_token = pagination.get("next") if isinstance(pagination, dict) else None
                if isinstance(next_token, str) and next_token:
                    start_token = next_token
                else:
                    record(key, {"output": str(out_path), "endpoint": endpoint, "ok": True, "complete": True, "appended_items": 0, "appended_pages": 0})
                    continue

            seen: set[str] = set()
            token = start_token
            mode = "resume" if existing else "fresh"
            typer.echo(f"[wykop] extras: {endpoint} ({mode}) starting_token={token!r}", err=True)
            with out_path.open("a", encoding="utf-8") as fh:
                page_idx = 0
                while True:
                    payload = api_client.get(endpoint, params={"page": token} if token is not None else None)
                    page_idx += 1
                    pages += 1
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                fh.write(
                                    json.dumps(
                                        {
                                            "platform": "wykop",
                                            "kind": "observed_item",
                                            "endpoint": endpoint,
                                            "page_token": token,
                                            **item,
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n"
                                )
                                rows_written += 1

                    if page_idx == 1 or page_idx % 200 == 0:
                        typer.echo(f"[wykop] extras: {endpoint}: pages+={page_idx}, items+={rows_written}", err=True)

                    if max_pages is not None and page_idx >= max_pages:
                        break

                    pagination = payload.get("pagination") if isinstance(payload, dict) else None
                    next_token = pagination.get("next") if isinstance(pagination, dict) else None
                    if not isinstance(next_token, str) or not next_token:
                        break
                    if next_token in seen:
                        break
                    seen.add(next_token)
                    token = next_token
                    time.sleep(delay_seconds)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            item = {
                "output": str(out_path),
                "endpoint": endpoint,
                "ok": False,
                "status": status,
                "error": str(e),
                "mode": mode,
                "start_token": start_token,
                "failing_token": token,
                "appended_items": rows_written,
                "appended_pages": pages,
            }
            if status == 500 and token is not None and (out_path.exists() and out_path.stat().st_size > 0):
                item["ok"] = True
                item["complete_due_to_retention_limit"] = True
            record(key, item)
            continue
        record(key, {"output": str(out_path), "endpoint": endpoint, "ok": True, "appended_items": rows_written, "appended_pages": pages})

    observed_discussions_path = user_dir / "wykop_observed_discussions.jsonl"
    observed_discussions_written = 0
    pages = 0
    start_token: str | None = None
    token: str | None = None
    mode = "fresh"
    try:
        existing = observed_discussions_path.exists() and observed_discussions_path.stat().st_size > 0
        if existing:
            last_obj = _read_last_jsonl_obj(observed_discussions_path)
            last_token = last_obj.get("page_token") if isinstance(last_obj, dict) else None
            params = {"page": last_token} if isinstance(last_token, str) and last_token else None
            probe = api_client.get("observed/discussions", params=params)
            pagination = probe.get("pagination") if isinstance(probe, dict) else None
            next_token = pagination.get("next") if isinstance(pagination, dict) else None
            if isinstance(next_token, str) and next_token:
                start_token = next_token
            else:
                record(
                    "observed_discussions",
                    {"output": str(observed_discussions_path), "endpoint": "observed/discussions", "ok": True, "complete": True, "appended_items": 0, "appended_pages": 0},
                )
                start_token = None
                raise StopIteration

        seen: set[str] = set()
        token = start_token
        mode = "resume" if existing else "fresh"
        typer.echo(f"[wykop] extras: observed/discussions ({mode}) starting_token={token!r}", err=True)
        with observed_discussions_path.open("a", encoding="utf-8") as fh:
            page_idx = 0
            while True:
                payload = api_client.get("observed/discussions", params={"page": token} if token is not None else None)
                page_idx += 1
                pages += 1
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            fh.write(
                                json.dumps(
                                    {
                                        "platform": "wykop",
                                        "kind": "observed_discussion",
                                        "endpoint": "observed/discussions",
                                        "page_token": token,
                                        **item,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                            observed_discussions_written += 1

                if page_idx == 1 or page_idx % 200 == 0:
                    typer.echo(f"[wykop] extras: observed/discussions: pages+={page_idx}, items+={observed_discussions_written}", err=True)

                if max_pages is not None and page_idx >= max_pages:
                    break

                pagination = payload.get("pagination") if isinstance(payload, dict) else None
                next_token = pagination.get("next") if isinstance(pagination, dict) else None
                if not isinstance(next_token, str) or not next_token:
                    break
                if next_token in seen:
                    break
                seen.add(next_token)
                token = next_token
                time.sleep(delay_seconds)
    except StopIteration:
        pass
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        item = {
            "output": str(observed_discussions_path),
            "endpoint": "observed/discussions",
            "ok": False,
            "status": status,
            "error": str(e),
            "mode": mode,
            "start_token": start_token,
            "failing_token": token,
            "appended_items": observed_discussions_written,
            "appended_pages": pages,
        }
        if status == 500 and token is not None and (observed_discussions_path.exists() and observed_discussions_path.stat().st_size > 0):
            item["ok"] = True
            item["complete_due_to_retention_limit"] = True
        record("observed_discussions", item)
    else:
        record(
            "observed_discussions",
            {
                "output": str(observed_discussions_path),
                "endpoint": "observed/discussions",
                "ok": True,
                "appended_items": observed_discussions_written,
                "appended_pages": pages,
            },
        )

    notifications_status_path = user_dir / "wykop_notifications_status.json"
    try:
        notifications_status = api_client.get("notifications/status")
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("notifications_status", {"output": str(notifications_status_path), "ok": False, "status": status, "error": str(e)})
    else:
        _write_json(notifications_status_path, notifications_status)
        record("notifications_status", {"output": str(notifications_status_path), "ok": True})
        time.sleep(delay_seconds)

    notification_group_ids: set[str] = set()
    for scope in ["pm", "entries", "tags", "observed-discussions"]:
        endpoint = f"notifications/{scope}"
        out_path = user_dir / f"wykop_notifications_{scope}.jsonl"
        rows: list[dict[str, Any]] = []
        pages = 0
        try:
            for page, payload in _api_iter_pages(api_client, endpoint, params={}, max_pages=max_pages):
                pages = page
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            gid = item.get("group_id")
                            if isinstance(gid, str) and gid:
                                notification_group_ids.add(gid)
                            rows.append(
                                {
                                    "platform": "wykop",
                                    "kind": "notification",
                                    "scope": scope,
                                    "endpoint": endpoint,
                                    "page": page,
                                    **item,
                                }
                            )
                time.sleep(delay_seconds)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            record(f"notifications_{scope}", {"output": str(out_path), "endpoint": endpoint, "ok": False, "status": status, "error": str(e)})
            continue
        _write_jsonl(out_path, rows)
        record(
            f"notifications_{scope}",
            {"output": str(out_path), "endpoint": endpoint, "ok": True, "items": len(rows), "pages": pages},
        )

    groups_out_path = user_dir / "wykop_notification_groups.jsonl"
    group_rows: list[dict[str, Any]] = []
    try:
        for gid in sorted(notification_group_ids):
            endpoint = f"notifications/groups/{gid}"
            for page, payload in _api_iter_pages(api_client, endpoint, params={}, max_pages=max_pages):
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            group_rows.append(
                                {
                                    "platform": "wykop",
                                    "kind": "notification_group_item",
                                    "group_id": gid,
                                    "endpoint": endpoint,
                                    "page": page,
                                    **item,
                                }
                            )
                time.sleep(delay_seconds)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("notification_groups", {"output": str(groups_out_path), "ok": False, "status": status, "error": str(e)})
    else:
        _write_jsonl(groups_out_path, group_rows)
        record(
            "notification_groups",
            {"output": str(groups_out_path), "ok": True, "groups": len(notification_group_ids), "items": len(group_rows)},
        )

    pm_dir = user_dir / "pm"
    pm_dir.mkdir(parents=True, exist_ok=True)

    pm_convs_out = user_dir / "wykop_pm_conversations.json"
    try:
        pm_convs_payload = api_client.get("pm/conversations", params={"page": 1})
        pm_pagination = pm_convs_payload.get("pagination") if isinstance(pm_convs_payload, dict) else None
        pm_total = pm_pagination.get("total") if isinstance(pm_pagination, dict) else None
        pm_per_page = pm_pagination.get("per_page") if isinstance(pm_pagination, dict) else None
        pm_pages = (
            max(1, math.ceil(pm_total / pm_per_page))
            if isinstance(pm_total, int) and isinstance(pm_per_page, int) and pm_per_page > 0
            else 1
        )
        pm_conversations: list[dict[str, Any]] = []
        for page in range(1, (min(pm_pages, max_pages) if max_pages else pm_pages) + 1):
            payload = api_client.get("pm/conversations", params={"page": page})
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        pm_conversations.append({"page": page, **item})
            time.sleep(delay_seconds)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        record("pm_conversations", {"output": str(pm_convs_out), "ok": False, "status": status, "error": str(e)})
        pm_conversations = []
    else:
        _write_json(
            pm_convs_out,
            {
                "username": username,
                "scraped_at": _now_iso(),
                "endpoint": "pm/conversations",
                "items": pm_conversations,
            },
        )
        record(
            "pm_conversations",
            {"output": str(pm_convs_out), "ok": True, "items": len(pm_conversations), "pages": pm_pages},
        )

    # Per-conversation message dumps (best-effort; API appears to return the latest slice for each thread).
    thread_usernames = []
    for row in pm_conversations:
        other = row.get("user") if isinstance(row, dict) else None
        u = other.get("username") if isinstance(other, dict) else None
        if isinstance(u, str) and u and u not in thread_usernames:
            thread_usernames.append(u)
    pm_threads_ok = 0
    for other_username in thread_usernames:
        out_path = pm_dir / f"{other_username}.json"
        try:
            thread_payload = api_client.get(f"pm/conversations/{other_username}")
        except requests.HTTPError:
            continue
        _write_json(out_path, thread_payload)
        pm_threads_ok += 1
        time.sleep(delay_seconds)
    record("pm_threads", {"output_dir": str(pm_dir), "ok": True, "threads": len(thread_usernames), "threads_written": pm_threads_ok})

    extras["completed_at"] = _now_iso()
    return extras


def _extract_rating(section: etree._Element) -> int | None:
    raw = section.xpath(
        ".//section[contains(@class,'rating-box')]//ul//li[1]/text() | "
        ".//section[contains(@class,'rating-box')]//li[1]/text()"
    )
    if not raw:
        return None
    text = "".join(raw).strip()
    try:
        return int(text.replace("\xa0", "").replace("−", "-"))
    except ValueError:
        return None


def _extract_username(section: etree._Element) -> str | None:
    username = section.xpath(".//a[contains(@class,'username')][1]//text()")
    if not username:
        return None
    return "".join(username).strip()


def _parse_max_page(root: etree._Element, username: str, section_path: str) -> int:
    pattern = re.compile(rf"^/ludzie/{re.escape(username)}/{re.escape(section_path)}/strona/(\d+)", re.I)
    max_page = 1
    for href in root.xpath("//a/@href"):
        m = pattern.match(href)
        if not m:
            continue
        try:
            max_page = max(max_page, int(m.group(1)))
        except ValueError:
            continue
    return max_page


def _parse_link_block(link_section: etree._Element) -> dict[str, Any] | None:
    link_id_raw = link_section.get("id", "")
    if not link_id_raw.startswith("link-"):
        return None
    try:
        link_id = int(link_id_raw.removeprefix("link-"))
    except ValueError:
        return None

    hrefs = link_section.xpath(".//h2[contains(@class,'heading')]//a[1]/@href")
    if not hrefs:
        return None
    link_href = hrefs[0]
    link_url = _abs_url(link_href)
    slug = link_href.strip("/").split("/")[-1] if link_href else None

    title = "".join(link_section.xpath(".//h2[contains(@class,'heading')]//a[1]//text()")).strip()
    created_at = link_section.xpath(".//section[contains(@class,'info')]//time[@title][1]/@title")
    created_at = created_at[0] if created_at else None
    tags = [t.strip() for t in link_section.xpath(".//li[contains(@class,'tag')]//a/text()") if t.strip()]

    return {
        "link_id": link_id,
        "link_title": title or None,
        "link_slug": slug,
        "link_url": link_url,
        "link_created_at": created_at,
        "link_tags": tags,
    }


def _parse_link_comments_page(root: etree._Element, page: int, username: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for comment in root.xpath("//section[starts-with(@id,'comment-') and contains(@class,'entry')]"):
        author = _extract_username(comment)
        if not _same_username(author, username):
            continue

        comment_id_raw = comment.get("id", "")
        try:
            comment_id = int(comment_id_raw.removeprefix("comment-"))
        except ValueError:
            continue

        created_at = comment.xpath(".//time[@title][1]/@title")
        created_at = created_at[0] if created_at else None

        comment_href = comment.xpath(".//a[contains(@href,'/komentarz/')][1]/@href")
        comment_url = _abs_url(comment_href[0]) if comment_href else None

        rating = _extract_rating(comment)

        wrapper = comment.xpath(".//section[contains(@class,'entry-content')]//div[contains(@class,'wrapper')][1]")
        wrapper_el = wrapper[0] if wrapper else None
        content = _element_to_markdown(wrapper_el) if wrapper_el is not None else ""

        photo = comment.xpath(".//section[contains(@class,'entry-content')]//img[not(contains(@src,'q80'))][1]/@src")
        photo_url = photo[0] if photo else None

        link_ancestor = comment.xpath("ancestor::section[starts-with(@id,'link-')][1]")
        link_meta = _parse_link_block(link_ancestor[0]) if link_ancestor else None
        if link_meta is None:
            continue

        out.append(
            {
                "platform": "wykop",
                "kind": "link_comment",
                "username": username,
                "page": page,
                "comment_id": comment_id,
                "comment_created_at": created_at,
                "comment_url": comment_url,
                "comment_content": content,
                "comment_photo_url": photo_url,
                "comment_rating": rating,
                **link_meta,
            }
        )
    return out


def _parse_links_page(root: etree._Element, page: int, username: str, kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for link in root.xpath("//section[starts-with(@id,'link-') and contains(@class,'link-block')]"):
        meta = _parse_link_block(link)
        if meta is None:
            continue
        out.append(
            {
                "platform": "wykop",
                "kind": kind,
                "username": username,
                "page": page,
                **meta,
            }
        )
    return out


def _parse_entries_page(root: etree._Element, page: int, username: str, kind: str, authored_only: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_entry_ids: set[int] = set()

    for section in root.xpath("//section[starts-with(@id,'comment-') and contains(@class,'entry')]"):
        hrefs = section.xpath(".//a[starts-with(@href,'/wpis/')][1]/@href")
        if not hrefs:
            continue
        href = hrefs[0]
        if "#" in href:
            continue

        entry_id_raw = section.get("id", "")
        try:
            entry_id = int(entry_id_raw.removeprefix("comment-"))
        except ValueError:
            continue
        if entry_id in seen_entry_ids:
            continue
        seen_entry_ids.add(entry_id)

        author = _extract_username(section)
        if authored_only and not _same_username(author, username):
            continue

        created_at = section.xpath(
            ".//a[starts-with(@href,'/wpis/') and not(contains(@href,'#'))][1]//time[@title][1]/@title"
        )
        created_at = created_at[0] if created_at else None

        entry_url = _abs_url(href)
        rating = _extract_rating(section)
        votes_up = max(rating or 0, 0)
        votes_down = abs(min(rating or 0, 0))

        wrapper = section.xpath(".//section[contains(@class,'entry-content')]//div[contains(@class,'wrapper')][1]")
        wrapper_el = wrapper[0] if wrapper else None
        content = _element_to_markdown(wrapper_el) if wrapper_el is not None else ""

        tags = []
        if wrapper_el is not None:
            for a in wrapper_el.xpath(".//a[starts-with(@href,'/tag/')]"):
                label = _normalise_ws("".join(a.itertext()))
                if label:
                    tags.append(label)

        photo = section.xpath(".//section[contains(@class,'entry-content')]//img[not(contains(@src,'q80'))][1]/@src")
        photo_url = photo[0] if photo else None

        out.append(
            {
                "platform": "wykop",
                "kind": kind,
                "username": username,
                "page": page,
                "entry_id": entry_id,
                "entry_url": entry_url,
                "entry_created_at": created_at,
                "entry_author": author,
                "entry_content": content,
                "entry_tags": tags,
                "entry_photo_url": photo_url,
                "votes_score": rating,
                "votes_up": votes_up,
                "votes_down": votes_down,
            }
        )
    return out


def _parse_entry_comments_page(root: etree._Element, page: int, username: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for section in root.xpath("//section[starts-with(@id,'comment-') and contains(@class,'entry')]"):
        hrefs = section.xpath(".//a[starts-with(@href,'/wpis/')][1]/@href")
        if not hrefs:
            continue
        href = hrefs[0]
        if "#" not in href:
            continue

        author = _extract_username(section)
        if not _same_username(author, username):
            continue

        comment_id_raw = section.get("id", "")
        try:
            comment_id = int(comment_id_raw.removeprefix("comment-"))
        except ValueError:
            continue

        created_at = section.xpath(".//time[@title][1]/@title")
        created_at = created_at[0] if created_at else None

        rating = _extract_rating(section)

        wrapper = section.xpath(".//section[contains(@class,'entry-content')]//div[contains(@class,'wrapper')][1]")
        wrapper_el = wrapper[0] if wrapper else None
        content = _element_to_markdown(wrapper_el) if wrapper_el is not None else ""

        photo = section.xpath(".//section[contains(@class,'entry-content')]//img[not(contains(@src,'q80'))][1]/@src")
        photo_url = photo[0] if photo else None

        entry_ancestor = None
        for anc in section.xpath("ancestor::section[starts-with(@id,'comment-')]"):
            anc_href = anc.xpath(".//a[starts-with(@href,'/wpis/')][1]/@href")
            if anc_href and "#" not in anc_href[0]:
                entry_ancestor = anc
                break

        entry_id = None
        entry_url = None
        entry_created_at = None
        entry_author = None
        if entry_ancestor is not None:
            try:
                entry_id = int((entry_ancestor.get("id") or "").removeprefix("comment-"))
            except ValueError:
                entry_id = None
            entry_href = entry_ancestor.xpath(".//a[starts-with(@href,'/wpis/') and not(contains(@href,'#'))][1]/@href")
            if entry_href:
                entry_url = _abs_url(entry_href[0])
            entry_created_at = entry_ancestor.xpath(
                ".//a[starts-with(@href,'/wpis/') and not(contains(@href,'#'))][1]//time[@title][1]/@title"
            )
            entry_created_at = entry_created_at[0] if entry_created_at else None
            entry_author = _extract_username(entry_ancestor)

        out.append(
            {
                "platform": "wykop",
                "kind": "entry_comment",
                "username": username,
                "page": page,
                "comment_id": comment_id,
                "comment_created_at": created_at,
                "comment_content": content,
                "comment_photo_url": photo_url,
                "comment_rating": rating,
                "entry_id": entry_id,
                "entry_url": entry_url,
                "entry_created_at": entry_created_at,
                "entry_author": entry_author,
            }
        )
    return out


@dataclass(frozen=True)
class Collection:
    key: str
    section_path: str
    output_name: str
    parse_page: Any
    html_enabled: bool = True

    def page_url(self, username: str, page: int) -> str:
        if page == 1:
            return f"{WYKOP_BASE}/ludzie/{username}/{self.section_path}/"
        return f"{WYKOP_BASE}/ludzie/{username}/{self.section_path}/strona/{page}/"


COLLECTIONS: dict[str, Collection] = {
    "znaleziska_komentowane": Collection(
        key="znaleziska_komentowane",
        section_path="znaleziska/komentowane",
        output_name="wykop_links_commented.jsonl",
        parse_page=_parse_link_comments_page,
    ),
    "znaleziska_dodane": Collection(
        key="znaleziska_dodane",
        section_path="znaleziska/dodane",
        output_name="wykop_links_added.jsonl",
        parse_page=lambda root, page, username: _parse_links_page(root, page, username, kind="link_added"),
    ),
    "znaleziska_wykopane": Collection(
        key="znaleziska_wykopane",
        section_path="znaleziska/wykopane",
        output_name="wykop_links_wykopane.jsonl",
        parse_page=lambda root, page, username: _parse_links_page(root, page, username, kind="link_wykopane"),
    ),
    "znaleziska_zakopane": Collection(
        key="znaleziska_zakopane",
        section_path="znaleziska/zakopane",
        output_name="wykop_links_zakopane.jsonl",
        parse_page=lambda root, page, username: _parse_links_page(root, page, username, kind="link_zakopane"),
        html_enabled=False,
    ),
    "znaleziska_opublikowane": Collection(
        key="znaleziska_opublikowane",
        section_path="znaleziska/opublikowane",
        output_name="wykop_links_opublikowane.jsonl",
        parse_page=lambda root, page, username: _parse_links_page(root, page, username, kind="link_opublikowane"),
    ),
    "znaleziska_powiazane": Collection(
        key="znaleziska_powiazane",
        section_path="znaleziska/powiazane",
        output_name="wykop_links_powiazane.jsonl",
        parse_page=lambda root, page, username: _parse_links_page(root, page, username, kind="link_powiazane"),
    ),
    "wpisy_dodane": Collection(
        key="wpisy_dodane",
        section_path="wpisy/dodane",
        output_name="wykop_entries_added.jsonl",
        parse_page=lambda root, page, username: _parse_entries_page(
            root, page, username, kind="entry", authored_only=True
        ),
    ),
    "wpisy_komentowane": Collection(
        key="wpisy_komentowane",
        section_path="wpisy/komentowane",
        output_name="wykop_entry_comments.jsonl",
        parse_page=_parse_entry_comments_page,
    ),
    "wpisy_plusowane": Collection(
        key="wpisy_plusowane",
        section_path="wpisy/plusowane",
        output_name="wykop_entries_plusowane.jsonl",
        parse_page=lambda root, page, username: _parse_entries_page(
            root, page, username, kind="entry_plusowane", authored_only=False
        ),
    ),
}

_API_SPECS: dict[str, tuple[str, Callable[[dict[str, Any], int, str], list[dict[str, Any]]]]] = {
    "znaleziska_komentowane": ("profile/users/{username}/links/commented", _parse_api_link_comments),
    "znaleziska_dodane": (
        "profile/users/{username}/links/added",
        lambda payload, page, username: _parse_api_links(payload, page, username, kind="link_added"),
    ),
    "znaleziska_wykopane": (
        "profile/users/{username}/links/up",
        lambda payload, page, username: _parse_api_links(payload, page, username, kind="link_wykopane"),
    ),
    "znaleziska_zakopane": (
        "profile/users/{username}/links/down",
        lambda payload, page, username: _parse_api_links(payload, page, username, kind="link_zakopane"),
    ),
    "znaleziska_opublikowane": (
        "profile/users/{username}/links/published",
        lambda payload, page, username: _parse_api_links(payload, page, username, kind="link_opublikowane"),
    ),
    "znaleziska_powiazane": (
        "profile/users/{username}/links/related",
        lambda payload, page, username: _parse_api_links(payload, page, username, kind="link_powiazane"),
    ),
    "wpisy_dodane": (
        "profile/users/{username}/entries/added",
        lambda payload, page, username: _parse_api_entries(payload, page, username, kind="entry"),
    ),
    "wpisy_plusowane": (
        "profile/users/{username}/entries/voted",
        lambda payload, page, username: _parse_api_entries(payload, page, username, kind="entry_plusowane"),
    ),
    "wpisy_komentowane": ("profile/users/{username}/entries/commented", _parse_api_entry_comments),
}


def _iter_existing_ids(path: Path, id_key: str) -> Iterable[int]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw = obj.get(id_key)
            if isinstance(raw, int):
                yield raw


@app.command()
def scrape(
    username: str = typer.Option("Sinity", help="Wykop username to scrape."),
    out_dir: Path = typer.Option(
        Path("/realm/data/wykop"),
        help="Destination folder under /realm/data.",
    ),
    backend: str = typer.Option(
        "auto",
        help="Scrape backend: auto (prefer API), api (requires auth), or html (public prerender, limited to ~page 49).",
    ),
    refresh_token: str | None = typer.Option(
        None,
        help="Wykop refresh token (localStorage userKeep). If omitted, tries state then Chrome Local Storage.",
    ),
    chrome_leveldb_dir: Path | None = typer.Option(
        None,
        help="Optional Chrome/Chromium 'Local Storage/leveldb' dir to scan for userKeep.",
    ),
    collections: list[str] = typer.Option(
        [],
        "--collection",
        help=f"Limit to specific collections. Options: {', '.join(sorted(COLLECTIONS))}.",
    ),
    delay_seconds: float = typer.Option(0.25, help="Sleep between HTTP requests."),
    max_pages: int | None = typer.Option(None, help="Optional cap for pages per collection (debug)."),
    user_agent: str = typer.Option(
        "Mozilla/5.0 (compatible; sinity-lynchpin/wykop-scraper; +https://wykop.pl)",
        help="HTTP User-Agent.",
    ),
    extras: bool = typer.Option(
        True,
        help="When using the API backend, also export extra account metadata endpoints (profile/badges/observed-tags/actions).",
    ),
) -> None:
    """Scrape public Wykop profile activity into JSONL (resumable).

    Outputs are stored under `out_dir/<username>/` with a `scrape_state.json` checkpoint.
    """

    selected = [c for c in collections if c]
    unknown = [c for c in selected if c not in COLLECTIONS]
    if unknown:
        raise typer.BadParameter(f"Unknown collections: {', '.join(unknown)}")
    if not selected:
        selected = sorted(COLLECTIONS)

    user_dir = out_dir / username
    user_dir.mkdir(parents=True, exist_ok=True)

    state_path = user_dir / "scrape_state.json"
    manifest_path = user_dir / "scrape_manifest.json"

    state = _read_json(state_path) or {
        "username": username,
        "started_at": _now_iso(),
        "auth": {},
        "collections": {},
    }

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    backend = backend.strip().lower()
    if backend not in {"auto", "api", "html"}:
        raise typer.BadParameter("backend must be one of: auto, api, html")

    api_client: WykopApiClient | None = None
    refresh_token_source: str | None = None
    auth_username: str | None = None
    if backend in {"auto", "api"}:
        token = refresh_token
        if token:
            refresh_token_source = "cli"
        if token is None:
            auth = state.get("auth") or {}
            token = auth.get("refresh_token") if isinstance(auth, dict) else None
            if isinstance(token, str) and token:
                refresh_token_source = "state"
            else:
                token = None
        if token is None:
            token = _extract_refresh_token_from_chrome(chrome_leveldb_dir)
            if token:
                refresh_token_source = "chrome"

        if token is None and backend == "api":
            raise typer.BadParameter(
                "backend=api requires --refresh-token or a discoverable Chrome Local Storage userKeep token"
            )

        if token:
            session.headers.update({"Accept": "application/json"})
            api_client = WykopApiClient(session, refresh_token=token)
            state.setdefault("auth", {})
            state["auth"].update(
                {
                    "refresh_token": api_client.refresh_token,
                    "updated_at": _now_iso(),
                    "source": refresh_token_source,
                }
            )
            _write_json(state_path, state)

            try:
                short = api_client.get("profile/short")
            except requests.HTTPError:
                short = None
            if isinstance(short, dict):
                data = short.get("data")
                u = data.get("username") if isinstance(data, dict) else None
                if isinstance(u, str) and u:
                    auth_username = u
                    state["auth"].update({"username": auth_username, "username_updated_at": _now_iso()})
                    _write_json(state_path, state)

    manifest: dict[str, Any] = {
        "username": username,
        "run_started_at": _now_iso(),
        "backend": backend,
        "api_enabled": api_client is not None,
        "auth_username": auth_username,
        "collections": {},
        "extras": {},
    }

    for key in selected:
        coll = COLLECTIONS[key]
        out_path = user_dir / coll.output_name

        id_key = "comment_id" if "comment" in coll.output_name else "entry_id" if "entries" in coll.output_name else "link_id"
        if coll.output_name == "wykop_entry_comments.jsonl":
            id_key = "comment_id"

        seen_ids = set(_iter_existing_ids(out_path, id_key=id_key))

        use_api = api_client is not None and backend in {"auto", "api"}

        max_page_detected: int
        max_page_final: int
        api_endpoint: str | None = None
        parse_api: Callable[[dict[str, Any], int, str], list[dict[str, Any]]] | None = None
        api_error: dict[str, Any] | None = None

        if use_api:
            spec = _API_SPECS.get(key)
            if spec is None:
                typer.echo(f"[wykop] {key}: no API spec; falling back to HTML", err=True)
                use_api = False
            else:
                api_endpoint, parse_api = spec
                try:
                    page1 = api_client.get(api_endpoint.format(username=username), params={"page": 1})
                except requests.HTTPError as e:
                    status = e.response.status_code if e.response is not None else None
                    api_error = {"status": status, "error": str(e)}
                    typer.echo(
                        f"[wykop] {key}: API endpoint {api_endpoint.format(username=username)} failed ({status}); falling back to HTML",
                        err=True,
                    )
                    use_api = False
                else:
                    pagination = page1.get("pagination") if isinstance(page1, dict) else None
                    total = pagination.get("total") if isinstance(pagination, dict) else None
                    per_page = pagination.get("per_page") if isinstance(pagination, dict) else None
                    if isinstance(total, int) and isinstance(per_page, int) and per_page > 0:
                        max_page_detected = max(1, math.ceil(total / per_page))
                    else:
                        max_page_detected = 1
                    max_page_final = min(max_page_detected, max_pages) if max_pages else max_page_detected
        if not use_api:
            if not coll.html_enabled:
                typer.echo(f"[wykop] {key}: HTML disabled (API-only); skipping", err=True)
                manifest["collections"][key] = {
                    "section_path": coll.section_path,
                    "output": str(out_path),
                    "backend": "skipped",
                    "skipped": True,
                    "reason": "api-only",
                    "api_endpoint": api_endpoint,
                    "api_error": api_error,
                }
                continue
            root_url = coll.page_url(username, 1)
            resp = _get(session, root_url)
            root = html.fromstring(resp.text)
            max_page_detected = _parse_max_page(root, username, coll.section_path)
            max_page_detected = _resolve_max_page(session, coll, username, max_page_detected)
            max_page_final = min(max_page_detected, max_pages) if max_pages else max_page_detected

        coll_state = state["collections"].get(key, {})
        previous_backend = coll_state.get("backend")
        if use_api and previous_backend == "api":
            start_page = int(coll_state.get("last_page", 0)) + 1
        elif (not use_api) and previous_backend == "html":
            start_page = int(coll_state.get("last_page", 0)) + 1
        else:
            start_page = 1
        typer.echo(
            f"[wykop] {key}: backend={'api' if use_api else 'html'} pages 1..{max_page_final} (detected {max_page_detected}); resuming at page {start_page}; ids={len(seen_ids)}"
        )

        manifest["collections"][key] = {
            "section_path": coll.section_path,
            "output": str(out_path),
            "backend": "api" if use_api else "html",
            "api_endpoint": api_endpoint,
            "api_error": api_error,
            "max_page_detected": max_page_detected,
            "max_page_final": max_page_final,
            "start_page": start_page,
            "existing_ids": len(seen_ids),
        }

        wrote = 0
        scrape_error: dict[str, Any] | None = None
        for page in range(start_page, max_page_final + 1):
            if use_api and api_client is not None and api_endpoint is not None and parse_api is not None:
                try:
                    payload = api_client.get(api_endpoint.format(username=username), params={"page": page})
                except requests.HTTPError as e:
                    status = e.response.status_code if e.response is not None else None
                    scrape_error = {"status": status, "error": str(e), "failed_at_page": page}
                    typer.echo(f"[wykop] {key}: API error at page {page} ({status}); stopping", err=True)
                    max_page_final = page - 1
                    break
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict):
                    data_is_empty = not data
                elif isinstance(data, list):
                    data_is_empty = len(data) == 0
                else:
                    data_is_empty = True
                if data_is_empty:
                    typer.echo(f"[wykop] {key}: got empty API page at {page}; stopping early at {page - 1}", err=True)
                    max_page_final = page - 1
                    break
                items = parse_api(payload, page, username)
            else:
                url = coll.page_url(username, page)
                resp = _get(session, url, allow_statuses={404})
                if resp.status_code == 404:
                    typer.echo(f"[wykop] {key}: got 404 at page {page}; stopping early at {page - 1}", err=True)
                    max_page_final = page - 1
                    break
                if not _page_has_prerender(resp.text):
                    typer.echo(
                        f"[wykop] {key}: page {page} returned JS shell (no prerender); stop. Use backend=api for full history.",
                        err=True,
                    )
                    max_page_final = page - 1
                    break
                root = html.fromstring(resp.text)
                items = coll.parse_page(root, page, username)

            wrote_this_page = 0
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("a", encoding="utf-8") as f:
                for item in items:
                    item_id = item.get(id_key)
                    if not isinstance(item_id, int):
                        continue
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                    wrote += 1
                    wrote_this_page += 1

            state["collections"].setdefault(key, {})
            state["collections"][key].update(
                {
                    "section_path": coll.section_path,
                    "output": str(out_path),
                    "backend": "api" if use_api else "html",
                    "last_page": page,
                    "last_updated_at": _now_iso(),
                    "seen_ids": len(seen_ids),
                }
            )
            _write_json(state_path, state)

            if wrote_this_page or page == start_page or page == max_page_final or page % 25 == 0:
                typer.echo(f"[wykop] {key}: page {page}/{max_page_final} (+{wrote_this_page}, total +{wrote})")
            time.sleep(delay_seconds)

        manifest["collections"][key].update(
            {
                "completed_at": _now_iso(),
                "items_written": wrote,
                "total_ids_now": len(seen_ids),
                "ok": scrape_error is None,
                "error": scrape_error,
            }
        )

    if extras and api_client is not None and backend in {"auto", "api"}:
        typer.echo("[wykop] extras: scraping additional API endpoints")
        manifest["extras"] = _scrape_api_extras(
            api_client=api_client,
            username=username,
            auth_username=auth_username,
            user_dir=user_dir,
            delay_seconds=delay_seconds,
            max_pages=max_pages,
        )

    _write_json(manifest_path, manifest)


if __name__ == "__main__":
    app()
