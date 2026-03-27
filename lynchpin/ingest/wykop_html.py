from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import requests

from .wykop_common import same_username
from .wykop_http import WYKOP_BASE, get

if TYPE_CHECKING:
    from lxml import etree


def page_has_prerender(html_text: str) -> bool:
    return "prerender" in html_text


def resolve_max_page(session: requests.Session, coll: "Collection", username: str, max_page_hint: int) -> int:
    def exists(page: int) -> bool:
        resp = get(session, coll.page_url(username, page), allow_statuses={404})
        return resp.status_code == 200 and page_has_prerender(resp.text)

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


def abs_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"{WYKOP_BASE}{href}"
    return f"{WYKOP_BASE}/{href}"


def normalise_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def element_to_markdown(node: etree._Element) -> str:
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
            inner = normalise_ws(render_children(el))
            if not inner:
                return ""
            lines: list[str] = []
            for line in inner.splitlines():
                lines.append(f"> {line}" if line else ">")
            return "\n".join(lines)

        if tag == "a":
            href = el.get("href") or ""
            label = normalise_ws("".join(el.itertext()))
            if not label:
                return ""
            if href.startswith("/tag/") or href.startswith("/ludzie/"):
                return label
            href_abs = abs_url(href) if href.startswith("/") else href
            if label == href_abs or label == href:
                return href_abs
            return f"[{label}]({href_abs})"

        out: list[str] = []
        if el.text:
            out.append(el.text)
        for child in el:
            out.append(render(child))
            if child.tail:
                out.append(child.tail)
        return "".join(out)

    def render_children(el: etree._Element) -> str:
        parts: list[str] = []
        if el.text:
            parts.append(el.text)
        for child in el:
            parts.append(render(child))
            if child.tail:
                parts.append(child.tail)
        return "".join(parts)

    return normalise_ws(render(node))


def extract_rating(section: etree._Element) -> int | None:
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


def extract_username(section: etree._Element) -> str | None:
    username = section.xpath(".//a[contains(@class,'username')][1]//text()")
    if not username:
        return None
    return "".join(username).strip()


def parse_max_page(root: etree._Element, username: str, section_path: str) -> int:
    pattern = re.compile(rf"^/ludzie/{re.escape(username)}/{re.escape(section_path)}/strona/(\d+)", re.I)
    max_page = 1
    for href in root.xpath("//a/@href"):
        match = pattern.match(href)
        if not match:
            continue
        try:
            max_page = max(max_page, int(match.group(1)))
        except ValueError:
            continue
    return max_page


def parse_link_block(link_section: etree._Element) -> dict[str, Any] | None:
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
    link_url = abs_url(link_href)
    slug = link_href.strip("/").split("/")[-1] if link_href else None

    title = "".join(link_section.xpath(".//h2[contains(@class,'heading')]//a[1]//text()")).strip()
    created_at = link_section.xpath(".//section[contains(@class,'info')]//time[@title][1]/@title")
    created_at = created_at[0] if created_at else None
    tags = [tag.strip() for tag in link_section.xpath(".//li[contains(@class,'tag')]//a/text()") if tag.strip()]

    return {
        "link_id": link_id,
        "link_title": title or None,
        "link_slug": slug,
        "link_url": link_url,
        "link_created_at": created_at,
        "link_tags": tags,
    }


def parse_link_comments_page(root: etree._Element, page: int, username: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for comment in root.xpath("//section[starts-with(@id,'comment-') and contains(@class,'entry')]"):
        author = extract_username(comment)
        if not same_username(author, username):
            continue

        comment_id_raw = comment.get("id", "")
        try:
            comment_id = int(comment_id_raw.removeprefix("comment-"))
        except ValueError:
            continue

        created_at = comment.xpath(".//time[@title][1]/@title")
        created_at = created_at[0] if created_at else None
        comment_href = comment.xpath(".//a[contains(@href,'/komentarz/')][1]/@href")
        comment_url = abs_url(comment_href[0]) if comment_href else None
        rating = extract_rating(comment)

        wrapper = comment.xpath(".//section[contains(@class,'entry-content')]//div[contains(@class,'wrapper')][1]")
        wrapper_el = wrapper[0] if wrapper else None
        content = element_to_markdown(wrapper_el) if wrapper_el is not None else ""

        photo = comment.xpath(".//section[contains(@class,'entry-content')]//img[not(contains(@src,'q80'))][1]/@src")
        photo_url = photo[0] if photo else None

        link_ancestor = comment.xpath("ancestor::section[starts-with(@id,'link-')][1]")
        link_meta = parse_link_block(link_ancestor[0]) if link_ancestor else None
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


def parse_links_page(root: etree._Element, page: int, username: str, kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for link in root.xpath("//section[starts-with(@id,'link-') and contains(@class,'link-block')]"):
        meta = parse_link_block(link)
        if meta is None:
            continue
        out.append({"platform": "wykop", "kind": kind, "username": username, "page": page, **meta})
    return out


def parse_entries_page(root: etree._Element, page: int, username: str, kind: str, authored_only: bool) -> list[dict[str, Any]]:
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

        author = extract_username(section)
        if authored_only and not same_username(author, username):
            continue

        created_at = section.xpath(
            ".//a[starts-with(@href,'/wpis/') and not(contains(@href,'#'))][1]//time[@title][1]/@title"
        )
        created_at = created_at[0] if created_at else None

        entry_url = abs_url(href)
        rating = extract_rating(section)
        votes_up = max(rating or 0, 0)
        votes_down = abs(min(rating or 0, 0))

        wrapper = section.xpath(".//section[contains(@class,'entry-content')]//div[contains(@class,'wrapper')][1]")
        wrapper_el = wrapper[0] if wrapper else None
        content = element_to_markdown(wrapper_el) if wrapper_el is not None else ""

        tags: list[str] = []
        if wrapper_el is not None:
            for anchor in wrapper_el.xpath(".//a[starts-with(@href,'/tag/')]"):
                label = normalise_ws("".join(anchor.itertext()))
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


def parse_entry_comments_page(root: etree._Element, page: int, username: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for section in root.xpath("//section[starts-with(@id,'comment-') and contains(@class,'entry')]"):
        hrefs = section.xpath(".//a[starts-with(@href,'/wpis/')][1]/@href")
        if not hrefs:
            continue
        href = hrefs[0]
        if "#" not in href:
            continue

        author = extract_username(section)
        if not same_username(author, username):
            continue

        comment_id_raw = section.get("id", "")
        try:
            comment_id = int(comment_id_raw.removeprefix("comment-"))
        except ValueError:
            continue

        created_at = section.xpath(".//time[@title][1]/@title")
        created_at = created_at[0] if created_at else None
        rating = extract_rating(section)

        wrapper = section.xpath(".//section[contains(@class,'entry-content')]//div[contains(@class,'wrapper')][1]")
        wrapper_el = wrapper[0] if wrapper else None
        content = element_to_markdown(wrapper_el) if wrapper_el is not None else ""

        photo = section.xpath(".//section[contains(@class,'entry-content')]//img[not(contains(@src,'q80'))][1]/@src")
        photo_url = photo[0] if photo else None

        entry_ancestor = None
        for ancestor in section.xpath("ancestor::section[starts-with(@id,'comment-')]"):
            ancestor_href = ancestor.xpath(".//a[starts-with(@href,'/wpis/')][1]/@href")
            if ancestor_href and "#" not in ancestor_href[0]:
                entry_ancestor = ancestor
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
                entry_url = abs_url(entry_href[0])
            entry_created_at = entry_ancestor.xpath(
                ".//a[starts-with(@href,'/wpis/') and not(contains(@href,'#'))][1]//time[@title][1]/@title"
            )
            entry_created_at = entry_created_at[0] if entry_created_at else None
            entry_author = extract_username(entry_ancestor)

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
    parse_page: Callable[..., list[dict[str, Any]]]
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
        parse_page=parse_link_comments_page,
    ),
    "znaleziska_dodane": Collection(
        key="znaleziska_dodane",
        section_path="znaleziska/dodane",
        output_name="wykop_links_added.jsonl",
        parse_page=lambda root, page, username: parse_links_page(root, page, username, kind="link_added"),
    ),
    "znaleziska_wykopane": Collection(
        key="znaleziska_wykopane",
        section_path="znaleziska/wykopane",
        output_name="wykop_links_wykopane.jsonl",
        parse_page=lambda root, page, username: parse_links_page(root, page, username, kind="link_wykopane"),
    ),
    "znaleziska_zakopane": Collection(
        key="znaleziska_zakopane",
        section_path="znaleziska/zakopane",
        output_name="wykop_links_zakopane.jsonl",
        parse_page=lambda root, page, username: parse_links_page(root, page, username, kind="link_zakopane"),
        html_enabled=False,
    ),
    "znaleziska_opublikowane": Collection(
        key="znaleziska_opublikowane",
        section_path="znaleziska/opublikowane",
        output_name="wykop_links_opublikowane.jsonl",
        parse_page=lambda root, page, username: parse_links_page(root, page, username, kind="link_opublikowane"),
    ),
    "znaleziska_powiazane": Collection(
        key="znaleziska_powiazane",
        section_path="znaleziska/powiazane",
        output_name="wykop_links_powiazane.jsonl",
        parse_page=lambda root, page, username: parse_links_page(root, page, username, kind="link_powiazane"),
    ),
    "wpisy_dodane": Collection(
        key="wpisy_dodane",
        section_path="wpisy/dodane",
        output_name="wykop_entries_added.jsonl",
        parse_page=lambda root, page, username: parse_entries_page(root, page, username, kind="entry", authored_only=True),
    ),
    "wpisy_komentowane": Collection(
        key="wpisy_komentowane",
        section_path="wpisy/komentowane",
        output_name="wykop_entry_comments.jsonl",
        parse_page=parse_entry_comments_page,
    ),
    "wpisy_plusowane": Collection(
        key="wpisy_plusowane",
        section_path="wpisy/plusowane",
        output_name="wykop_entries_plusowane.jsonl",
        parse_page=lambda root, page, username: parse_entries_page(
            root, page, username, kind="entry_plusowane", authored_only=False
        ),
    ),
}
