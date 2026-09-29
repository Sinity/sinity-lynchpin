"""Sync TheMotte messages and notifications from an authenticated session.

The cookie method reads the operator's Chrome cookie store; the CDP method
drives the live Chrome session through ``sinnix-chrome-control``. Both walk the
same paginated notification streams and share one acquisition route:

- Every page must be an authenticated, recognized notification page before its
  rows count. A login form, a redirect away from the requested path, a missing
  session marker, or comment markup the parser cannot read raises
  ``TheMotteAcquisitionError`` and leaves the retained raw files untouched.
- A page budget bounds the work of one attempt, not the history a pass can
  reach. Progress (visited pages, rows, continuation URL) is saved after each
  validated page in ``sync_state.json``; the next attempt resumes there.
- Raw files keep one row per native comment ID. A row observed again with
  different content keeps its previous content under ``revisions``. Only a
  complete pass (no continuation left) updates current membership: retained
  rows absent from it stay in the file with ``current: false``. An incomplete
  pass adds and updates observed rows and never infers absence.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..sources.themotte import (
    MESSAGE_FILENAME,
    NOTIFICATION_FILENAME,
    SYNC_MANIFEST_FILENAME,
    SYNC_STATE_FILENAME,
    profile_root,
)
from ._manifest import atomic_write_ndjson, atomic_write_text, write_manifest

THEMOTTE_BASE_URL = "https://www.themotte.org"
THEMOTTE_SYNC_SCHEMA_VERSION = 2
THEMOTTE_SYNC_STATE_VERSION = 1


@dataclass(frozen=True)
class _Stream:
    name: str
    filename: str
    path: str
    # Fields that identify a revision; volatile display fields (relative time,
    # unread flag, page position) change without the content changing.
    content_fields: tuple[str, ...]


_STREAMS = (
    _Stream(
        name="messages",
        filename=MESSAGE_FILENAME,
        path="/notifications/messages",
        content_fields=("author", "recipient", "peer", "body", "created_at", "created_epoch", "url"),
    ),
    _Stream(
        name="notifications",
        filename=NOTIFICATION_FILENAME,
        path="/notifications",
        content_fields=("kind", "actor", "title", "text", "url", "created_at", "created_epoch"),
    ),
)

PageFetcher = Callable[[str, _Stream], dict[str, Any]]


class TheMotteAcquisitionError(MaterializationError):
    """A page could not be established as an authenticated, recognized observation."""

    def __init__(self, reason: str) -> None:
        super().__init__("themotte.raw_sync", reason=reason)


def sync_themotte(
    *,
    username: str | None = None,
    root: Path | None = None,
    method: str = "cookie",
    target: str = "live",
    cookie_db: Path | None = None,
    max_message_pages: int = 20,
    max_notification_pages: int = 5,
) -> dict[str, Any]:
    if max_message_pages < 1 or max_notification_pages < 1:
        raise ValueError("TheMotte page budgets must be at least 1")
    cfg = get_config()
    user = username or cfg.themotte_username
    out_dir = profile_root(root=root, username=user)
    out_dir.mkdir(parents=True, exist_ok=True)
    budgets = {"messages": max_message_pages, "notifications": max_notification_pages}

    if method == "cookie":
        cookie_header = _themotte_cookie_header(cookie_db or _default_cookie_db())
        parsers = {"messages": _extract_messages_html, "notifications": _extract_notifications_html}

        def fetch_http(url: str, stream: _Stream) -> dict[str, Any]:
            text, location = _fetch_html(url, cookie_header=cookie_header)
            return parsers[stream.name](text, page_url=location, username=user)

        return _acquire_and_publish(out_dir, user=user, fetch=fetch_http, budgets=budgets, sync_method=method)
    if method == "cdp":
        page_id = _new_tab(target, f"{THEMOTTE_BASE_URL}/notifications/messages")
        extractors = {"messages": _MESSAGE_EXTRACTOR_JS, "notifications": _NOTIFICATION_EXTRACTOR_JS}

        def fetch_cdp(url: str, stream: _Stream) -> dict[str, Any]:
            return _cdp_page(target, page_id, url, _with_operator(extractors[stream.name], user))

        try:
            return _acquire_and_publish(
                out_dir,
                user=user,
                fetch=fetch_cdp,
                budgets=budgets,
                sync_method=f"sinnix-chrome-control --target {target}",
            )
        finally:
            _chrome(target, "close", page_id, check=False)
    raise ValueError(f"unsupported TheMotte sync method: {method}")


def _acquire_and_publish(
    out_dir: Path,
    *,
    user: str,
    fetch: PageFetcher,
    budgets: dict[str, int],
    sync_method: str,
) -> dict[str, Any]:
    state_path = out_dir / SYNC_STATE_FILENAME
    manifest: dict[str, Any] = {}
    for stream in _STREAMS:
        progress = _acquire_stream(stream, fetch=fetch, max_pages=budgets[stream.name], state_path=state_path)
        manifest = _publish_stream(
            out_dir,
            stream,
            progress,
            user=user,
            sync_method=sync_method,
            max_pages=budgets[stream.name],
        )
        if progress["next_url"] is None:
            _clear_stream_state(state_path, stream.name)
    return manifest


def _acquire_stream(stream: _Stream, *, fetch: PageFetcher, max_pages: int, state_path: Path) -> dict[str, Any]:
    """Walk up to ``max_pages`` validated pages, resuming a retained pass."""
    first_url = f"{THEMOTTE_BASE_URL}{stream.path}"
    state = _load_state(state_path)
    progress = state["streams"].get(stream.name)
    if not isinstance(progress, dict) or progress.get("first_url") != first_url:
        progress = {
            "first_url": first_url,
            "pass_started_at": _now_iso(),
            "next_url": first_url,
            "pages_visited": 0,
            "attempts": 0,
            "rows": {},
        }
    progress["attempts"] += 1
    state["streams"][stream.name] = progress
    _save_state(state_path, state)
    for _ in range(max_pages):
        url = progress["next_url"]
        if url is None:
            break
        payload = fetch(url, stream)
        next_url = _validate_page(payload, requested_url=url, stream=stream)
        observed_at = _now_iso()
        page = progress["pages_visited"] + 1
        for row in payload["rows"]:
            progress["rows"][str(row["id"])] = {**row, "page": page, "observed_at": observed_at}
        progress["pages_visited"] = page
        progress["next_url"] = next_url
        _save_state(state_path, state)
    return progress


def _validate_page(payload: object, *, requested_url: str, stream: _Stream) -> str | None:
    """Accept only an authenticated page of the requested stream; return its continuation."""
    where = f"TheMotte {stream.name} page {requested_url}"
    if not isinstance(payload, dict):
        raise TheMotteAcquisitionError(f"{where}: extractor returned no page payload")
    if payload.get("login_form"):
        raise TheMotteAcquisitionError(f"{where}: response is a login form, not an authenticated page")
    if payload.get("authenticated") is not True:
        raise TheMotteAcquisitionError(f"{where}: no authenticated session marker (logout control or own profile link)")
    location = payload.get("location")
    if not isinstance(location, str) or not _same_path(location, requested_url):
        raise TheMotteAcquisitionError(f"{where}: served {location!r} instead of the requested page")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not all(isinstance(row, dict) and row.get("id") for row in rows):
        raise TheMotteAcquisitionError(f"{where}: rows are not a list of identified records")
    candidates = payload.get("candidates")
    if not isinstance(candidates, int):
        raise TheMotteAcquisitionError(f"{where}: extractor did not report its comment candidates")
    if candidates and not rows:
        raise TheMotteAcquisitionError(f"{where}: {candidates} comment blocks but none parsed; markup unrecognized")
    next_url = payload.get("next_url")
    if next_url is None:
        return None
    if not isinstance(next_url, str) or not _same_path(next_url, requested_url) or next_url == requested_url:
        raise TheMotteAcquisitionError(f"{where}: continuation {next_url!r} does not continue this stream")
    return next_url


def _same_path(url: str, reference: str) -> bool:
    left = urllib.parse.urlsplit(url)
    right = urllib.parse.urlsplit(reference)
    return (left.netloc, left.path.rstrip("/")) == (right.netloc, right.path.rstrip("/"))


def _publish_stream(
    out_dir: Path,
    stream: _Stream,
    progress: dict[str, Any],
    *,
    user: str,
    sync_method: str,
    max_pages: int,
) -> dict[str, Any]:
    """Merge a pass into the retained rows, then publish the file and manifest."""
    path = out_dir / stream.filename
    merged = _load_retained(path)
    observed: dict[str, dict[str, Any]] = progress["rows"]
    for row_id, row in observed.items():
        merged[row_id] = _merge_observation(merged.get(row_id), row, stream.content_fields)
    complete = progress["next_url"] is None
    published_at = _now_iso()
    if complete:
        for row_id, row in merged.items():
            if row_id not in observed and row.get("current", True):
                merged[row_id] = {**row, "current": False, "absent_since": published_at}
    atomic_write_ndjson(path, [merged[row_id] for row_id in sorted(merged)])

    manifest_path = out_dir / SYNC_MANIFEST_FILENAME
    previous = _read_json(manifest_path)
    streams = previous.get("streams") if previous.get("schema_version") == THEMOTTE_SYNC_SCHEMA_VERSION else None
    streams = dict(streams) if isinstance(streams, dict) else {}
    prior = streams.get(stream.name) if isinstance(streams.get(stream.name), dict) else {}
    streams[stream.name] = {
        "pass_complete": complete,
        "pass_started_at": progress["pass_started_at"],
        "pages_visited": progress["pages_visited"],
        "attempts": progress["attempts"],
        "continuation_url": progress["next_url"],
        "max_pages_per_attempt": max_pages,
        "observed_in_pass": len(observed),
        "last_complete_pass_at": published_at if complete else prior.get("last_complete_pass_at"),
        "published_at": published_at,
    }
    files = [out_dir / item.filename for item in _STREAMS]
    for item, file_path in zip(_STREAMS, files):
        entry = streams.setdefault(item.name, {"pass_complete": None, "last_complete_pass_at": None})
        entry.update(_file_summary(file_path))
    manifest = {
        "dataset": "themotte.raw_sync",
        "schema_version": THEMOTTE_SYNC_SCHEMA_VERSION,
        "username": user,
        "source": THEMOTTE_BASE_URL,
        "sync_method": sync_method,
        "message_count": streams["messages"]["row_count"],
        "notification_count": streams["notifications"]["row_count"],
        "streams": streams,
        "materialized_path": str(out_dir),
        "files": [str(file_path) for file_path in files],
        "input_latest_mtime": latest_mtime_iso(tuple(file_path for file_path in files if file_path.exists())),
        "materialized_at": published_at,
    }
    write_manifest(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def _merge_observation(
    previous: dict[str, Any] | None,
    observation: dict[str, Any],
    content_fields: tuple[str, ...],
) -> dict[str, Any]:
    row = {key: value for key, value in observation.items() if key != "observed_at"}
    row["last_observed_at"] = observation["observed_at"]
    row["current"] = True
    if previous is None:
        row["first_observed_at"] = observation["observed_at"]
        row["revisions"] = []
        return row
    revisions = list(previous.get("revisions") or [])
    # A legacy row without a field has not been observed with a different value.
    if any(field in previous and previous[field] != observation.get(field) for field in content_fields):
        revisions.append(
            {
                **{field: previous[field] for field in content_fields if field in previous},
                "last_observed_at": previous.get("last_observed_at"),
            }
        )
    row["first_observed_at"] = previous.get("first_observed_at")
    row["revisions"] = revisions
    return row


def _load_retained(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TheMotteAcquisitionError(f"retained {path} line {number} is not JSON; refusing to rewrite it") from exc
            if isinstance(row, dict) and row.get("id"):
                rows[str(row["id"])] = row
    return rows


def _file_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "row_count": 0, "current_count": 0, "sha256": None}
    data = path.read_bytes()
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    return {
        "path": str(path),
        "row_count": len(rows),
        "current_count": sum(1 for row in rows if isinstance(row, dict) and row.get("current", True)),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": THEMOTTE_SYNC_STATE_VERSION, "streams": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TheMotteAcquisitionError(f"acquisition state {path} is not JSON; inspect it before resuming") from exc
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != THEMOTTE_SYNC_STATE_VERSION
        or not isinstance(state.get("streams"), dict)
    ):
        raise TheMotteAcquisitionError(f"acquisition state {path} has an unrecognized shape")
    return state


def _save_state(path: Path, state: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _clear_stream_state(path: Path, stream: str) -> None:
    state = _load_state(path)
    state["streams"].pop(stream, None)
    if state["streams"]:
        _save_state(path, state)
    else:
        path.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fetch_html(url: str, *, cookie_header: str) -> tuple[str, str]:
    """Return the response body and the URL that actually served it."""
    request = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie_header,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/149 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace"), response.geturl()
    except urllib.error.HTTPError as exc:
        raise TheMotteAcquisitionError(f"fetch failed for {url}: HTTP {exc.code}") from exc


def _extract_messages_html(text: str, *, page_url: str, username: str) -> dict[str, Any]:
    soup = _soup(text)
    rows = []
    candidates = soup.select('div.anchor.comment[id^="comment-"]')
    for comment in candidates:
        comment_id = comment.get("id", "").replace("comment-", "")
        info = _own_info(comment)
        if info is None:
            continue
        author = _user_name(info)
        timestamp = _timestamp(info)
        body_node = soup.select_one(f"#comment-text-{comment_id}")
        body = _node_text(body_node)
        sent = _sent_to_for(comment)
        recipient = sent if author == username else username
        peer = sent if author == username else author
        if not comment_id or not body or timestamp is None:
            continue
        rows.append(
            {
                "id": comment_id,
                "author": author,
                "recipient": recipient,
                "peer": peer,
                "body": body,
                "created_at": _iso(timestamp),
                "created_epoch": timestamp,
                "relative_time": _relative_time(info),
                "url": f"{THEMOTTE_BASE_URL}/comment/{comment_id}",
            }
        )
    return {"rows": rows, "next_url": _next_url(soup, page_url), **_page_markers(soup, page_url, username, candidates)}


def _extract_notifications_html(text: str, *, page_url: str, username: str) -> dict[str, Any]:
    soup = _soup(text)
    title_node = soup.select_one(".notifs .font-weight-bold")
    title = _node_text(title_node) or "notification"
    rows = []
    candidates = soup.select('div.anchor.comment[id^="comment-"]')
    for comment in candidates:
        comment_id = comment.get("id", "").replace("comment-", "")
        info = _own_info(comment)
        if info is None:
            continue
        timestamp = _timestamp(info)
        body_node = soup.select_one(f"#comment-text-{comment_id}")
        body = _node_text(body_node)
        first_link = body_node.select_one("a[href]") if body_node else None
        if not comment_id or not body:
            continue
        rows.append(
            {
                "id": comment_id,
                "kind": title,
                "actor": _user_name(info),
                "title": title,
                "text": body,
                "url": _absolute_url(first_link.get("href") if first_link else f"/comment/{comment_id}"),
                "created_at": _iso(timestamp) if timestamp is not None else None,
                "created_epoch": timestamp,
                "relative_time": _relative_time(info),
                "unread": "unread" in (comment.get("class") or ()),
            }
        )
    return {"rows": rows, "next_url": _next_url(soup, page_url), **_page_markers(soup, page_url, username, candidates)}


def _page_markers(soup: Any, page_url: str, username: str, candidates: list[Any]) -> dict[str, Any]:
    """Report the evidence ``_validate_page`` needs to accept a page."""
    profile_paths = {f"/@{username}", f"{THEMOTTE_BASE_URL}/@{username}"}
    authenticated = False
    for node in soup.select("[href], [action], [onclick]"):
        if any("/logout" in (node.get(attr) or "") for attr in ("href", "action", "onclick")):
            authenticated = True
            break
        if node.get("href") in profile_paths:
            authenticated = True
            break
    return {
        "location": page_url,
        "authenticated": authenticated,
        "login_form": soup.select_one('input[type="password"]') is not None,
        "candidates": len(candidates),
    }


def _soup(text: str) -> Any:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("TheMotte cookie sync requires beautifulsoup4 in the dev environment") from exc
    return BeautifulSoup(text, "html.parser")


def _own_info(comment: Any) -> Any | None:
    for child in comment.find_all(recursive=False):
        classes = child.get("class") or ()
        if "comment-user-info" in classes:
            return child
    return None


def _user_name(info: Any) -> str:
    node = info.select_one(".user-name span")
    return _node_text(node)


def _timestamp(info: Any) -> int | None:
    node = info.select_one(".time-stamp")
    raw = node.get("onmouseover", "") if node else ""
    match = re.search(r"'([0-9]{10})'", raw)
    return int(match.group(1)) if match else None


def _relative_time(info: Any) -> str:
    node = info.select_one(".time-stamp")
    return _node_text(node)


def _sent_to_for(comment: Any) -> str:
    top = comment
    parent = top.find_parent("div", class_="anchor")
    while parent is not None and "comment" in (parent.get("class") or ()):
        top = parent
        parent = top.find_parent("div", class_="anchor")
    node = top.previous_sibling
    while node is not None:
        text = node.get_text(" ", strip=True) if hasattr(node, "get_text") else str(node)
        match = re.search(r"Sent to @([A-Za-z0-9_-]+)", text)
        if match:
            return match.group(1)
        node = node.previous_sibling
    return ""


def _next_url(soup: Any, page_url: str) -> str | None:
    for link in soup.select("a.page-link"):
        if _node_text(link) == "Next" and not _has_disabled_parent(link):
            return _absolute_url(link.get("href"), base=page_url)
    return None


def _has_disabled_parent(node: Any) -> bool:
    parent = node.parent
    while parent is not None:
        if "disabled" in (parent.get("class") or ()):
            return True
        parent = parent.parent
    return False


def _node_text(node: Any) -> str:
    if node is None:
        return ""
    return html.unescape(node.get_text("\n", strip=True)).replace("\n\n\n", "\n\n").strip()


def _absolute_url(href: str | None, *, base: str = THEMOTTE_BASE_URL) -> str:
    if not href:
        return ""
    return urllib.parse.urljoin(base, href)


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _default_cookie_db() -> Path:
    profile = Path("/home/sinity/.config/chrome-ws/Default/Cookies")
    if profile.exists():
        return profile
    raise FileNotFoundError("Chrome cookie DB not found; pass --cookie-db")


def _themotte_cookie_header(cookie_db: Path) -> str:
    rows = _read_cookie_rows(cookie_db)
    cookies = []
    for host, name, value, encrypted in rows:
        if "themotte.org" not in host:
            continue
        cookie_value = value or _decrypt_chrome_cookie(host, encrypted)
        if cookie_value:
            cookies.append(f"{name}={cookie_value}")
    if not cookies:
        raise RuntimeError(f"no TheMotte cookies found in {cookie_db}")
    return "; ".join(cookies)


def _read_cookie_rows(cookie_db: Path) -> list[tuple[str, str, str, bytes]]:
    with tempfile.NamedTemporaryFile(prefix="themotte-cookies-", suffix=".sqlite", dir="/realm/tmp", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(cookie_db, tmp_path)
        conn = sqlite3.connect(tmp_path)
        try:
            return [
                (str(host), str(name), str(value or ""), bytes(encrypted or b""))
                for host, name, value, encrypted in conn.execute(
                    "select host_key, name, value, encrypted_value from cookies where host_key like ?",
                    ("%themotte.org%",),
                )
            ]
        finally:
            conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)


def _decrypt_chrome_cookie(host: str, encrypted: bytes) -> str:
    if not encrypted:
        return ""
    if not encrypted.startswith(b"v10"):
        return encrypted.decode("utf-8", errors="replace")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
    cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
    decryptor = cipher.decryptor()
    plain = decryptor.update(encrypted[3:]) + decryptor.finalize()
    pad = plain[-1]
    plain = plain[:-pad]
    # Newer Chrome Linux cookies prefix SHA256(host_key) before the value.
    if len(plain) > 32:
        plain = plain[32:]
    return plain.decode("utf-8", errors="replace")


def _cdp_page(target: str, page_id: str, url: str, extractor_js: str) -> dict[str, Any]:
    _chrome(target, "navigate", page_id, "--url", url)
    _chrome(
        target,
        "await",
        page_id,
        "--timeout-sec",
        "30",
        "--js",
        'document.readyState === "complete" && document.body && document.body.innerText.length > 20',
    )
    return _evaluate(target, page_id, extractor_js)


def _new_tab(target: str, url: str) -> str:
    payload = json.loads(_chrome(target, "new-tab", "--url", url))
    return str(payload["id"])


def _evaluate(target: str, page_id: str, js: str) -> dict[str, Any]:
    payload = json.loads(_chrome(target, "evaluate", page_id, "--js", js))
    value = payload.get("result", {}).get("result", {}).get("value")
    if value is None and "rows" in payload:
        value = payload
    return value if isinstance(value, dict) else {}


def _chrome(target: str, *args: str, check: bool = True) -> str:
    cmd = ["sinnix-chrome-control", "--target", target, *args]
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr or proc.stdout}")
    return proc.stdout


def _with_operator(extractor_js: str, username: str) -> str:
    return extractor_js.replace("__OPERATOR__", json.dumps(username))


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync TheMotte private messages and notifications")
    parser.add_argument("--username", default=None)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--method", default="cookie", choices=("cookie", "cdp"))
    parser.add_argument("--cookie-db", type=Path, default=None)
    parser.add_argument("--target", default="live", choices=("live", "private", "private-visible"))
    parser.add_argument("--max-message-pages", type=int, default=20, help="page budget per attempt; a pass resumes")
    parser.add_argument("--max-notification-pages", type=int, default=5, help="page budget per attempt; a pass resumes")
    args = parser.parse_args(argv)
    report = sync_themotte(
        username=args.username,
        root=args.root,
        method=args.method,
        target=args.target,
        cookie_db=args.cookie_db,
        max_message_pages=args.max_message_pages,
        max_notification_pages=args.max_notification_pages,
    )
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


# Shared page evidence for both CDP extractors; mirrors ``_page_markers``.
_PAGE_MARKERS_JS = r"""
  const operator = __OPERATOR__;
  const profilePaths = new Set([`/@${operator}`, `${location.origin}/@${operator}`]);
  const authenticated = Array.from(document.querySelectorAll("[href], [action], [onclick]")).some((node) =>
    ["href", "action", "onclick"].some((attr) => (node.getAttribute(attr) || "").includes("/logout"))
    || profilePaths.has(node.getAttribute("href") || "")
  );
  const markers = {
    location: location.href,
    authenticated,
    login_form: !!document.querySelector('input[type="password"]'),
    candidates: document.querySelectorAll('div.anchor.comment[id^="comment-"]').length,
  };
"""

_MESSAGE_EXTRACTOR_JS = (
    r"""
(() => {"""
    + _PAGE_MARKERS_JS
    + r"""
  const abs = (href) => {
    if (!href) return "";
    try { return new URL(href, location.origin).href; } catch { return href; }
  };
  const epoch = (node) => {
    const raw = node?.getAttribute("onmouseover") || "";
    const match = raw.match(/'([0-9]{10})'/);
    return match ? Number(match[1]) : null;
  };
  const iso = (seconds) => seconds ? new Date(seconds * 1000).toISOString() : null;
  const bodyText = (node) => (node?.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
  const topCommentFor = (comment) => {
    let top = comment;
    while (top.parentElement) {
      const parent = top.parentElement.closest('div.anchor.comment[id^="comment-"]');
      if (!parent) break;
      top = parent;
    }
    return top;
  };
  const sentToFor = (comment) => {
    let node = topCommentFor(comment).previousElementSibling;
    while (node) {
      const match = node.innerText?.match(/Sent to @([A-Za-z0-9_-]+)/);
      if (match) return match[1];
      node = node.previousElementSibling;
    }
    return "";
  };
  const rows = Array.from(document.querySelectorAll('div.anchor.comment[id^="comment-"]')).map((comment) => {
    const id = comment.id.replace("comment-", "");
    const info = Array.from(comment.children).find((child) => child.classList?.contains("comment-user-info"));
    const author = info?.querySelector(".user-name span")?.innerText?.trim() || "";
    const ts = info?.querySelector(".time-stamp");
    const sent = sentToFor(comment);
    const createdEpoch = epoch(ts);
    const text = bodyText(document.querySelector(`#comment-text-${CSS.escape(id)}`));
    const peer = author === operator ? sent : author;
    const recipient = author === operator ? sent : operator;
    return {
      id,
      author,
      recipient,
      peer,
      body: text,
      created_at: iso(createdEpoch),
      created_epoch: createdEpoch,
      relative_time: ts?.innerText?.trim() || "",
      url: abs(`/comment/${id}`),
    };
  }).filter((row) => row.id && row.body && row.created_at);
  const next = Array.from(document.querySelectorAll("a.page-link")).find((a) => a.innerText.trim() === "Next" && !a.closest(".disabled"));
  return {rows, next_url: next ? abs(next.getAttribute("href")) : null, ...markers};
})()
"""
)

_NOTIFICATION_EXTRACTOR_JS = (
    r"""
(() => {"""
    + _PAGE_MARKERS_JS
    + r"""
  const abs = (href) => {
    if (!href) return "";
    try { return new URL(href, location.origin).href; } catch { return href; }
  };
  const epoch = (node) => {
    const raw = node?.getAttribute("onmouseover") || "";
    const match = raw.match(/'([0-9]{10})'/);
    return match ? Number(match[1]) : null;
  };
  const iso = (seconds) => seconds ? new Date(seconds * 1000).toISOString() : null;
  const rows = Array.from(document.querySelectorAll('div.anchor.comment[id^="comment-"]')).map((comment) => {
    const id = comment.id.replace("comment-", "");
    const info = Array.from(comment.children).find((child) => child.classList?.contains("comment-user-info"));
    const actor = info?.querySelector(".user-name span")?.innerText?.trim() || "";
    const ts = info?.querySelector(".time-stamp");
    const createdEpoch = epoch(ts);
    const text = (document.querySelector(`#comment-text-${CSS.escape(id)}`)?.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
    const title = document.querySelector(".notifs .font-weight-bold")?.innerText?.trim() || document.title || "";
    const link = comment.querySelector(`#comment-text-${CSS.escape(id)} a[href]`)?.getAttribute("href") || `/comment/${id}`;
    return {
      id,
      kind: title || "notification",
      actor,
      title,
      text,
      url: abs(link),
      created_at: iso(createdEpoch),
      created_epoch: createdEpoch,
      relative_time: ts?.innerText?.trim() || "",
      unread: comment.classList.contains("unread") || !!comment.querySelector(".unread"),
    };
  }).filter((row) => row.id && row.text);
  const next = Array.from(document.querySelectorAll("a.page-link")).find((a) => a.innerText.trim() === "Next" && !a.closest(".disabled"));
  return {rows, next_url: next ? abs(next.getAttribute("href")) : null, ...markers};
})()
"""
)


def main() -> int:
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
