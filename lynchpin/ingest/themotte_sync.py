"""Sync TheMotte messages and notifications through the live Chrome session.

This command uses the local CDP helper instead of persisting browser cookies.
It requires the operator to be logged into themotte.org in the live Chrome
profile exposed on the Sinnix CDP port.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.io import latest_mtime_iso
from ..sources.themotte import MESSAGE_FILENAME, NOTIFICATION_FILENAME, SYNC_MANIFEST_FILENAME, profile_root
from ._manifest import write_manifest

THEMOTTE_BASE_URL = "https://www.themotte.org"
THEMOTTE_SYNC_SCHEMA_VERSION = 1


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
    cfg = get_config()
    user = username or cfg.themotte_username
    out_dir = profile_root(root=root, username=user)
    out_dir.mkdir(parents=True, exist_ok=True)

    if method == "cookie":
        cookie_header = _themotte_cookie_header(cookie_db or _default_cookie_db())
        messages = _sync_http_pages(
            first_url=f"{THEMOTTE_BASE_URL}/notifications/messages",
            max_pages=max_message_pages,
            cookie_header=cookie_header,
            parser=_extract_messages_html,
        )
        notifications = _sync_http_pages(
            first_url=f"{THEMOTTE_BASE_URL}/notifications",
            max_pages=max_notification_pages,
            cookie_header=cookie_header,
            parser=_extract_notifications_html,
        )
    elif method == "cdp":
        page_id = _new_tab(target, f"{THEMOTTE_BASE_URL}/notifications/messages")
        try:
            messages = _sync_pages(
                target=target,
                page_id=page_id,
                first_url=f"{THEMOTTE_BASE_URL}/notifications/messages",
                max_pages=max_message_pages,
                extractor_js=_MESSAGE_EXTRACTOR_JS,
            )
            notifications = _sync_pages(
                target=target,
                page_id=page_id,
                first_url=f"{THEMOTTE_BASE_URL}/notifications",
                max_pages=max_notification_pages,
                extractor_js=_NOTIFICATION_EXTRACTOR_JS,
            )
        finally:
            _chrome(target, "close", page_id, check=False)
    else:
        raise ValueError(f"unsupported TheMotte sync method: {method}")

    message_path = out_dir / MESSAGE_FILENAME
    notification_path = out_dir / NOTIFICATION_FILENAME
    messages = _dedupe(messages, "id")
    notifications = _dedupe(notifications, "id")
    _write_jsonl(message_path, messages)
    _write_jsonl(notification_path, notifications)

    manifest_path = out_dir / SYNC_MANIFEST_FILENAME
    manifest = {
        "dataset": "themotte.raw_sync",
        "schema_version": THEMOTTE_SYNC_SCHEMA_VERSION,
        "username": user,
        "source": THEMOTTE_BASE_URL,
        "sync_method": method if method == "cookie" else f"sinnix-chrome-control --target {target}",
        "message_count": len(messages),
        "notification_count": len(notifications),
        "max_message_pages": max_message_pages,
        "max_notification_pages": max_notification_pages,
        "materialized_path": str(out_dir),
        "files": [str(message_path), str(notification_path)],
        "input_latest_mtime": latest_mtime_iso((message_path, notification_path)),
    }
    write_manifest(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def _sync_http_pages(
    *,
    first_url: str,
    max_pages: int,
    cookie_header: str,
    parser: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    next_url: str | None = first_url
    for page in range(1, max_pages + 1):
        if next_url is None:
            break
        text = _fetch_html(next_url, cookie_header=cookie_header)
        payload = parser(text, page_url=next_url)
        for row in payload.get("rows", []):
            if isinstance(row, dict):
                row["page"] = page
                rows.append(row)
        next_url = payload.get("next_url") if isinstance(payload.get("next_url"), str) else None
    return rows


def _fetch_html(url: str, *, cookie_header: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie_header,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/149 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"TheMotte fetch failed for {url}: HTTP {exc.code}") from exc


def _extract_messages_html(text: str, *, page_url: str) -> dict[str, Any]:
    soup = _soup(text)
    rows = []
    for comment in soup.select('div.anchor.comment[id^="comment-"]'):
        comment_id = comment.get("id", "").replace("comment-", "")
        info = _own_info(comment)
        if info is None:
            continue
        author = _user_name(info)
        timestamp = _timestamp(info)
        body_node = soup.select_one(f"#comment-text-{comment_id}")
        body = _node_text(body_node)
        sent = _sent_to_for(comment)
        recipient = sent if author == "Sinity" else "Sinity"
        peer = sent if author == "Sinity" else author
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
    return {"rows": rows, "next_url": _next_url(soup, page_url)}


def _extract_notifications_html(text: str, *, page_url: str) -> dict[str, Any]:
    soup = _soup(text)
    title_node = soup.select_one(".notifs .font-weight-bold")
    title = _node_text(title_node) or "notification"
    rows = []
    for comment in soup.select('div.anchor.comment[id^="comment-"]'):
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
    return {"rows": rows, "next_url": _next_url(soup, page_url)}


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
    import re

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
    import re

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


def _sync_pages(
    *,
    target: str,
    page_id: str,
    first_url: str,
    max_pages: int,
    extractor_js: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    next_url: str | None = first_url
    for page in range(1, max_pages + 1):
        if next_url is None:
            break
        _chrome(target, "navigate", page_id, "--url", next_url)
        _chrome(
            target,
            "await",
            page_id,
            "--timeout-sec",
            "30",
            "--js",
            'document.readyState === "complete" && document.body && document.body.innerText.length > 20',
        )
        payload = _evaluate(target, page_id, extractor_js)
        for row in payload.get("rows", []):
            if isinstance(row, dict):
                row["page"] = page
                rows.append(row)
        next_url = payload.get("next_url") if isinstance(payload.get("next_url"), str) else None
    return rows


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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _dedupe(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_key = str(row.get(key) or "")
        if row_key:
            deduped[row_key] = row
    return [deduped[key] for key in sorted(deduped)]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync TheMotte private messages and notifications")
    parser.add_argument("--username", default=None)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--method", default="cookie", choices=("cookie", "cdp"))
    parser.add_argument("--cookie-db", type=Path, default=None)
    parser.add_argument("--target", default="live", choices=("live", "private", "private-visible"))
    parser.add_argument("--max-message-pages", type=int, default=20)
    parser.add_argument("--max-notification-pages", type=int, default=5)
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


_MESSAGE_EXTRACTOR_JS = r"""
(() => {
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
    const peer = author === "Sinity" ? sent : author;
    const recipient = author === "Sinity" ? sent : "Sinity";
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
  return {rows, next_url: next ? abs(next.getAttribute("href")) : null};
})()
"""

_NOTIFICATION_EXTRACTOR_JS = r"""
(() => {
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
  return {rows, next_url: next ? abs(next.getAttribute("href")) : null};
})()
"""


def main() -> int:
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
