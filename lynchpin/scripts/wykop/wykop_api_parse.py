from __future__ import annotations

from typing import Any, Callable, Iterable

from .wykop_common import same_username
from .wykop_http import WYKOP_BASE

ApiParseFn = Callable[[dict[str, Any], int, str], list[dict[str, Any]]]


def iter_api_items(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        yield from data
        return
    if isinstance(data, dict):
        keys = list(data.keys())
        if keys and all(isinstance(key, str) and key.isdigit() for key in keys):
            for key in sorted(keys, key=lambda value: int(value)):
                item = data.get(key)
                if isinstance(item, dict):
                    yield item
            return
        for item in data.values():
            if isinstance(item, dict):
                yield item


def votes_score(votes: Any) -> int | None:
    if not isinstance(votes, dict):
        return None
    up = votes.get("up")
    down = votes.get("down")
    if isinstance(up, int) and isinstance(down, int):
        return up - down
    return None


def api_link_url(link_id: int, slug: str | None) -> str:
    if slug:
        return f"{WYKOP_BASE}/link/{link_id}/{slug}"
    return f"{WYKOP_BASE}/link/{link_id}"


def api_entry_url(entry_id: int, slug: str | None) -> str:
    if slug:
        return f"{WYKOP_BASE}/wpis/{entry_id}/{slug}"
    return f"{WYKOP_BASE}/wpis/{entry_id}"


def api_media_photo_url(media: Any) -> str | None:
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


def api_link_meta(link_obj: dict[str, Any]) -> dict[str, Any] | None:
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
    tags_norm = [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]
    return {
        "link_id": link_id,
        "link_title": title,
        "link_slug": slug,
        "link_url": api_link_url(link_id, slug),
        "link_created_at": created_at,
        "link_tags": tags_norm,
    }


def api_entry_meta(entry_obj: dict[str, Any]) -> dict[str, Any] | None:
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
        "entry_url": api_entry_url(entry_id, slug),
        "entry_created_at": created_at,
        "entry_author": author_username,
    }


def parse_api_links(payload: dict[str, Any], page: int, username: str, *, kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for link_obj in iter_api_items(payload):
        meta = api_link_meta(link_obj)
        if meta is None:
            continue
        rows.append({"platform": "wykop", "kind": kind, "username": username, "page": page, **meta})
    return rows


def parse_api_link_comments(payload: dict[str, Any], page: int, username: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for link_obj in iter_api_items(payload):
        link_meta = api_link_meta(link_obj)
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
            if not same_username(author_username, username):
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
            rows.append(
                {
                    "platform": "wykop",
                    "kind": "link_comment",
                    "username": username,
                    "page": page,
                    "comment_id": comment_id,
                    "comment_created_at": created_at,
                    "comment_url": f"{link_meta['link_url']}#comment-{comment_id}",
                    "comment_content": content.strip(),
                    "comment_photo_url": api_media_photo_url(comment.get("media")),
                    "comment_rating": votes_score(comment.get("votes")),
                    **link_meta,
                }
            )
    return rows


def parse_api_entries(payload: dict[str, Any], page: int, username: str, *, kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry_obj in iter_api_items(payload):
        meta = api_entry_meta(entry_obj)
        if meta is None:
            continue
        tags = entry_obj.get("tags")
        if not isinstance(tags, list):
            tags = []
        tags_norm = [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]
        content = entry_obj.get("content")
        if not isinstance(content, str):
            content = ""
        votes = entry_obj.get("votes")
        votes_up = votes.get("up") if isinstance(votes, dict) else None
        votes_down = votes.get("down") if isinstance(votes, dict) else None
        rows.append(
            {
                "platform": "wykop",
                "kind": kind,
                "username": username,
                "page": page,
                **meta,
                "entry_content": content.strip(),
                "entry_tags": tags_norm,
                "entry_photo_url": api_media_photo_url(entry_obj.get("media")),
                "votes_score": votes_score(votes),
                "votes_up": votes_up if isinstance(votes_up, int) else None,
                "votes_down": votes_down if isinstance(votes_down, int) else None,
            }
        )
    return rows


def parse_api_entry_comments(payload: dict[str, Any], page: int, username: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry_obj in iter_api_items(payload):
        entry_meta = api_entry_meta(entry_obj)
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
            if not same_username(author_username, username):
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
            rows.append(
                {
                    "platform": "wykop",
                    "kind": "entry_comment",
                    "username": username,
                    "page": page,
                    "comment_id": comment_id,
                    "comment_created_at": created_at,
                    "comment_content": content.strip(),
                    "comment_photo_url": api_media_photo_url(comment.get("media")),
                    "comment_rating": votes_score(comment.get("votes")),
                    **entry_meta,
                }
            )
    return rows


API_SPECS: dict[str, tuple[str, ApiParseFn]] = {
    "znaleziska_komentowane": ("profile/users/{username}/links/commented", parse_api_link_comments),
    "znaleziska_dodane": (
        "profile/users/{username}/links/added",
        lambda payload, page, username: parse_api_links(payload, page, username, kind="link_added"),
    ),
    "znaleziska_wykopane": (
        "profile/users/{username}/links/up",
        lambda payload, page, username: parse_api_links(payload, page, username, kind="link_wykopane"),
    ),
    "znaleziska_zakopane": (
        "profile/users/{username}/links/down",
        lambda payload, page, username: parse_api_links(payload, page, username, kind="link_zakopane"),
    ),
    "znaleziska_opublikowane": (
        "profile/users/{username}/links/published",
        lambda payload, page, username: parse_api_links(payload, page, username, kind="link_opublikowane"),
    ),
    "znaleziska_powiazane": (
        "profile/users/{username}/links/related",
        lambda payload, page, username: parse_api_links(payload, page, username, kind="link_powiazane"),
    ),
    "wpisy_dodane": (
        "profile/users/{username}/entries/added",
        lambda payload, page, username: parse_api_entries(payload, page, username, kind="entry"),
    ),
    "wpisy_plusowane": (
        "profile/users/{username}/entries/voted",
        lambda payload, page, username: parse_api_entries(payload, page, username, kind="entry_plusowane"),
    ),
    "wpisy_komentowane": ("profile/users/{username}/entries/commented", parse_api_entry_comments),
}
