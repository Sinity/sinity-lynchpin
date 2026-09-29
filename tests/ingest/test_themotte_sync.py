from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lynchpin.ingest import themotte_sync
from lynchpin.sources import themotte

BASE = themotte_sync.THEMOTTE_BASE_URL
MESSAGES_URL = f"{BASE}/notifications/messages"
NOTIFICATIONS_URL = f"{BASE}/notifications"
USER = "operator"


def _comment(comment_id: str, author: str, body: str, epoch: int) -> str:
    return (
        f'<div class="anchor comment" id="comment-{comment_id}">'
        f'<div class="comment-user-info"><a class="user-name" href="/@{author}"><span>{author}</span></a>'
        f"<span class=\"time-stamp\" onmouseover=\"timestamp(this, '{epoch}')\">1h ago</span></div>"
        f'<div id="comment-text-{comment_id}"><p>{body}</p></div></div>'
    )


def _page(comments: list[str], *, next_href: str | None, authenticated: bool = True, title: str = "") -> str:
    header = f'<nav><a href="/@{USER}">me</a></nav>' if authenticated else "<nav><a href=\"/login\">Log in</a></nav>"
    pager = (
        f'<ul class="pagination"><li class="page-item"><a class="page-link" href="{next_href}">Next</a></li></ul>'
        if next_href
        else '<ul class="pagination"><li class="page-item disabled"><a class="page-link" href="#">Next</a></li></ul>'
    )
    heading = f'<span class="font-weight-bold">{title}</span>' if title else ""
    return f"<html><body>{header}<div class=\"notifs\">{heading}{''.join(comments)}</div>{pager}</body></html>"


LOGIN_PAGE = (
    '<html><body><form action="/login" method="post"><input name="username">'
    '<input type="password" name="password"><button>Sign in</button></form></body></html>'
)


def _message_pages(count: int, *, start_id: int = 100) -> dict[str, str]:
    pages = {}
    for number in range(1, count + 1):
        url = MESSAGES_URL if number == 1 else f"{MESSAGES_URL}?page={number}"
        next_href = f"/notifications/messages?page={number + 1}" if number < count else None
        comment_id = str(start_id + number)
        pages[url] = _page(
            [f"<p>Sent to @peer_{number}</p>", _comment(comment_id, USER, f"message {number}", 1767261600 + number * 60)],
            next_href=next_href,
        )
    return pages


class FakeSite:
    """Serve synthetic HTML through the cookie transport; ``overrides`` maps URL to (body, served URL)."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = dict(pages)
        self.overrides: dict[str, tuple[str, str]] = {}
        self.fail_on: set[str] = set()
        self.fetched: list[str] = []

    def fetch(self, url: str, *, cookie_header: str) -> tuple[str, str]:
        self.fetched.append(url)
        if url in self.fail_on:
            raise KeyboardInterrupt
        if url in self.overrides:
            return self.overrides[url]
        return self.pages[url], url


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> FakeSite:
    fake = FakeSite({NOTIFICATIONS_URL: _page([], next_href=None, title="Notifications")})
    monkeypatch.setattr(themotte_sync, "_fetch_html", fake.fetch)
    monkeypatch.setattr(themotte_sync, "_themotte_cookie_header", lambda _db: "session=synthetic")
    return fake


def _sync(root: Path, *, message_pages: int = 20) -> dict:
    return themotte_sync.sync_themotte(
        username=USER,
        root=root,
        method="cookie",
        cookie_db=root / "Cookies",
        max_message_pages=message_pages,
        max_notification_pages=5,
    )


def _messages(root: Path) -> dict[str, themotte.TheMotteMessage]:
    return {row.id: row for row in themotte.iter_messages(root, username=USER)}


def _raw_rows(root: Path, filename: str) -> dict[str, dict]:
    lines = (root / USER / filename).read_text(encoding="utf-8").splitlines()
    return {row["id"]: row for row in map(json.loads, lines)}


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted((root / USER).iterdir())}


def test_valid_pages_publish_rows_manifest_and_reader_agree(tmp_path: Path, site: FakeSite) -> None:
    site.pages.update(_message_pages(2))
    site.pages[NOTIFICATIONS_URL] = _page(
        [_comment("7", "peer_x", '<a href="/post/9">mentioned @operator</a>', 1767265200)],
        next_href=None,
        title="Username Mention",
    )

    report = _sync(tmp_path)

    messages = _messages(tmp_path)
    assert sorted(messages) == ["101", "102"]
    assert messages["101"].author == USER and messages["101"].peer == "peer_1" and messages["101"].current
    notifications = list(themotte.iter_notifications(tmp_path, username=USER))
    assert [(row.id, row.actor, row.kind, row.url) for row in notifications] == [
        ("7", "peer_x", "Username Mention", f"{BASE}/post/9")
    ]
    manifest = json.loads((tmp_path / USER / themotte.SYNC_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest == {key: value for key, value in report.items() if key != "manifest_path"}
    assert manifest["message_count"] == 2 and manifest["notification_count"] == 1
    assert manifest["streams"]["messages"]["pass_complete"] is True
    assert manifest["streams"]["messages"]["continuation_url"] is None
    raw = (tmp_path / USER / themotte.MESSAGE_FILENAME).read_bytes()
    assert manifest["streams"]["messages"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert not (tmp_path / USER / themotte.SYNC_STATE_FILENAME).exists()


def test_verified_empty_pass_ends_membership_without_erasing_history(tmp_path: Path, site: FakeSite) -> None:
    site.pages.update(_message_pages(1))
    _sync(tmp_path)

    site.pages[MESSAGES_URL] = _page([], next_href=None)
    report = _sync(tmp_path)

    rows = _raw_rows(tmp_path, themotte.MESSAGE_FILENAME)
    assert rows["101"]["current"] is False and rows["101"]["absent_since"]
    assert _messages(tmp_path)["101"].current is False
    assert report["streams"]["messages"]["current_count"] == 0
    assert report["streams"]["messages"]["row_count"] == 1


def test_first_verified_empty_pass_publishes_empty_collection(tmp_path: Path, site: FakeSite) -> None:
    site.pages[MESSAGES_URL] = _page([], next_href=None)

    report = _sync(tmp_path)

    assert (tmp_path / USER / themotte.MESSAGE_FILENAME).read_text(encoding="utf-8") == ""
    assert report["message_count"] == 0 and report["streams"]["messages"]["pass_complete"] is True


@pytest.mark.parametrize(
    "response",
    [
        pytest.param((LOGIN_PAGE, MESSAGES_URL), id="login-form-200"),
        pytest.param((_page([], next_href=None, authenticated=False), MESSAGES_URL), id="no-session-marker"),
        pytest.param((_page([], next_href=None), f"{BASE}/login?redirect=/notifications/messages"), id="redirected"),
        pytest.param((_page(['<div class="anchor comment" id="comment-5"><p>x</p></div>'], next_href=None), MESSAGES_URL),
                     id="unrecognized-markup"),
    ],
)
def test_unestablished_page_preserves_last_good_raw_files(
    tmp_path: Path, site: FakeSite, response: tuple[str, str]
) -> None:
    site.pages.update(_message_pages(1))
    _sync(tmp_path)
    before = _snapshot(tmp_path)

    site.overrides[MESSAGES_URL] = response
    with pytest.raises(themotte_sync.TheMotteAcquisitionError):
        _sync(tmp_path)

    after = _snapshot(tmp_path)
    after.pop(themotte.SYNC_STATE_FILENAME, None)
    assert after == before
    assert _messages(tmp_path)["101"].current is True


def test_page_budget_keeps_continuation_and_history_until_pass_completes(tmp_path: Path, site: FakeSite) -> None:
    # History from an earlier sync that now sits beyond the visited pages.
    site.pages.update(_message_pages(1, start_id=0))
    _sync(tmp_path)
    site.pages = {NOTIFICATIONS_URL: site.pages[NOTIFICATIONS_URL], **_message_pages(21)}

    first = _sync(tmp_path)

    assert first["streams"]["messages"]["pass_complete"] is False
    assert first["streams"]["messages"]["continuation_url"] == f"{MESSAGES_URL}?page=21"
    messages = _messages(tmp_path)
    assert len(messages) == 21 and "121" not in messages
    assert messages["1"].current is True, "an unvisited page is not evidence of absence"

    site.fetched.clear()
    second = _sync(tmp_path)

    assert site.fetched[0] == f"{MESSAGES_URL}?page=21"
    assert second["streams"]["messages"]["pass_complete"] is True
    assert second["streams"]["messages"]["attempts"] == 2
    messages = _messages(tmp_path)
    assert len(messages) == 22 and messages["121"].current is True
    assert messages["1"].current is False
    assert all(messages[str(100 + n)].current for n in range(1, 22))


def test_interrupted_attempt_resumes_from_retained_progress(tmp_path: Path, site: FakeSite) -> None:
    site.pages.update(_message_pages(4))
    site.fail_on.add(f"{MESSAGES_URL}?page=3")

    with pytest.raises(KeyboardInterrupt):
        _sync(tmp_path)

    assert not (tmp_path / USER / themotte.MESSAGE_FILENAME).exists()
    state = json.loads((tmp_path / USER / themotte.SYNC_STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["streams"]["messages"]["next_url"] == f"{MESSAGES_URL}?page=3"
    assert sorted(state["streams"]["messages"]["rows"]) == ["101", "102"]

    site.fail_on.clear()
    site.fetched.clear()
    report = _sync(tmp_path)

    assert site.fetched[:2] == [f"{MESSAGES_URL}?page=3", f"{MESSAGES_URL}?page=4"]
    assert sorted(_messages(tmp_path)) == ["101", "102", "103", "104"]
    assert report["streams"]["messages"]["pass_complete"] is True


def test_changed_content_keeps_previous_revision(tmp_path: Path, site: FakeSite) -> None:
    site.pages.update(_message_pages(1))
    _sync(tmp_path)
    site.pages[MESSAGES_URL] = _page(
        ["<p>Sent to @peer_1</p>", _comment("101", USER, "edited message", 1767261660)], next_href=None
    )

    _sync(tmp_path)

    row = _raw_rows(tmp_path, themotte.MESSAGE_FILENAME)["101"]
    assert row["body"] == "edited message"
    assert [revision["body"] for revision in row["revisions"]] == ["message 1"]
    assert _messages(tmp_path)["101"].body == "edited message"


def test_cdp_route_validates_extractor_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    location = {"url": ""}

    def fake_chrome(target: str, *args: str, check: bool = True) -> str:
        calls.append(args)
        if args[0] == "new-tab":
            return json.dumps({"id": "page-1"})
        if args[0] == "navigate":
            location["url"] = args[-1]
        if args[0] == "evaluate":
            js = args[-1]
            assert '"operator"' in js and "__OPERATOR__" not in js
            rows = (
                [{"id": "1", "author": USER, "recipient": "peer", "peer": "peer", "body": "hello",
                  "created_at": "2026-02-01T10:00:00.000Z", "url": f"{BASE}/comment/1"}]
                if "recipient" in js
                else []
            )
            value = {"rows": rows, "next_url": None, "location": location["url"], "authenticated": True,
                     "login_form": False, "candidates": len(rows)}
            return json.dumps({"result": {"result": {"value": value}}})
        return "true"

    monkeypatch.setattr(themotte_sync, "_chrome", fake_chrome)
    report = themotte_sync.sync_themotte(username=USER, root=tmp_path, method="cdp")

    assert report["message_count"] == 1 and report["notification_count"] == 0
    assert _messages(tmp_path)["1"].peer == "peer"
    assert any(call[0] == "close" for call in calls)


def test_cdp_route_rejects_empty_evaluation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chrome(target: str, *args: str, check: bool = True) -> str:
        if args[0] == "new-tab":
            return json.dumps({"id": "page-1"})
        if args[0] == "evaluate":
            return json.dumps({"result": {"result": {}}})
        return "true"

    monkeypatch.setattr(themotte_sync, "_chrome", fake_chrome)
    with pytest.raises(themotte_sync.TheMotteAcquisitionError):
        themotte_sync.sync_themotte(username=USER, root=tmp_path, method="cdp")
    assert not (tmp_path / USER / themotte.MESSAGE_FILENAME).exists()
