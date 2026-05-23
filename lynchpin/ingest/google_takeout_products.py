"""Materialize typed Google Takeout products from raw archives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..core.config import get_config
from .google_takeout_materialize import google_takeout_inventory_dir, materialize_google_takeout_inventory
from ..sources.google_takeout import TakeoutMember, discover_takeout_archives, iter_member_bytes

_STRUCTURED_PRODUCTS = {
    "Contacts",
    "Google Play Store",
    "Keep",
    "My Activity",
    "Purchases _ Reservations",
    "Tasks",
    "YouTube and YouTube Music",
}
_ASSET_PRODUCTS = {
    "Drive",
    "Fit",
    "Google Pay",
    "Google Photos",
    "Location History",
    "Mail",
    "Maps",
    "YouTube",
    "YouTube and YouTube Music",
}
_STRUCTURED_ASSET_PRODUCTS = {"Drive", "Google Pay", "Google Photos", "Location History", "Maps"}
_SKIPPED_PRODUCTS = {
    "Calendar": "calendar export is intentionally unsupported; no canonical dataset is maintained",
    "Google Chat": "exports only contain user_info/unsentmessages in current raw archives",
    "Gemini": "current exports are empty scheduling/gems stubs",
}


@dataclass(frozen=True)
class _ProductRow:
    product: str
    row: dict[str, Any]


def google_takeout_products_dir() -> Path:
    return get_config().exports_root / "google/processed/takeout-products"


def materialize_google_takeout_products(*, root: Path | None = None) -> dict[str, Any]:
    """Write canonical typed Google Takeout rows for supported non-empty products."""
    output_dir = google_takeout_products_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "contacts": output_dir / "contacts.ndjson",
        "keep_notes": output_dir / "keep_notes.ndjson",
        "my_activity": output_dir / "my_activity.ndjson",
        "play_store": output_dir / "play_store.ndjson",
        "purchases": output_dir / "purchases.ndjson",
        "tasks": output_dir / "tasks.ndjson",
        "youtube": output_dir / "youtube.ndjson",
        "assets": output_dir / "assets.ndjson",
    }
    counts: Counter[str] = Counter()
    seen: dict[str, set[str]] = {name: set() for name in paths}
    errors: Counter[str] = Counter()

    handles = {name: path.open("w", encoding="utf-8") for name, path in paths.items()}
    try:
        for item in _iter_structured_rows(root=root):
            _write_row(handles[item.product], seen[item.product], counts, item.product, item.row)
        for row in _iter_asset_rows(root=root):
            _write_row(handles["assets"], seen["assets"], counts, "assets", row)
    finally:
        for handle in handles.values():
            handle.close()

    manifest = {
        "dataset": "google.takeout.products",
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "archive_count": len(discover_takeout_archives(root)),
        "supported_products": sorted(_STRUCTURED_PRODUCTS | _ASSET_PRODUCTS),
        "skipped_products": _SKIPPED_PRODUCTS,
        "products": {
            name: {
                "path": str(path),
                "row_count": counts[name],
            }
            for name, path in sorted(paths.items())
        },
        "errors": dict(sorted(errors.items())),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _iter_structured_rows(*, root: Path | None) -> Iterator[_ProductRow]:
    products = set(_STRUCTURED_PRODUCTS)
    suffixes = {".json", ".tasks", ".html", ".csv", ".vcf"}
    for member, raw in iter_member_bytes(root=root, products=products, suffixes=suffixes):
        path = member.path
        if member.product == "Contacts" and path.lower().endswith(".vcf"):
            yield from (_ProductRow("contacts", row) for row in _parse_contacts(member, raw))
        elif member.product == "Keep" and path.lower().endswith(".json"):
            row = _parse_keep_json(member, raw)
            if row is not None:
                yield _ProductRow("keep_notes", row)
        elif member.product == "My Activity" and path.endswith("/MyActivity.html"):
            yield from (_ProductRow("my_activity", row) for row in _parse_my_activity(member, raw))
        elif member.product == "Purchases _ Reservations" and path.lower().endswith(".json"):
            row = _parse_purchase(member, raw)
            if row is not None:
                yield _ProductRow("purchases", row)
        elif member.product == "Google Play Store" and path.lower().endswith(".json"):
            yield from (_ProductRow("play_store", row) for row in _parse_play_store(member, raw))
        elif member.product == "Tasks" and path.lower().endswith((".json", ".tasks")):
            yield from (_ProductRow("tasks", row) for row in _parse_tasks(member, raw))
        elif member.product == "YouTube and YouTube Music" and path.lower().endswith(".csv"):
            yield from (_ProductRow("youtube", row) for row in _parse_youtube_csv(member, raw))


def _iter_asset_rows(*, root: Path | None) -> Iterator[dict[str, Any]]:
    members_path = google_takeout_inventory_dir() / "members.ndjson"
    if root is None and not members_path.exists():
        materialize_google_takeout_inventory()
    if root is None and members_path.exists():
        with members_path.open(encoding="utf-8") as handle:
            for line in handle:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    yield from _asset_row_from_inventory(payload)
        return
    for member, _raw in iter_member_bytes(root=root, products=_ASSET_PRODUCTS, suffixes=set()):
        yield from _asset_row_from_inventory(
            {
                "archive": str(member.archive),
                "path": member.path,
                "product": member.product,
                "size_bytes": member.size_bytes,
            }
        )


def _asset_row_from_inventory(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    product = str(payload.get("product") or "")
    if product not in _ASSET_PRODUCTS:
        return
    path = str(payload.get("path") or "")
    archive = str(payload.get("archive") or "")
    size = int(payload.get("size_bytes") or 0)
    suffix = Path(path).suffix.lower()
    if suffix in {".json", ".html", ".csv"} and product not in _STRUCTURED_ASSET_PRODUCTS:
        return
    yield {
        "id": _stable_id(product, path, size),
        "product": product,
        "archive": archive,
        "archive_date": _archive_date(Path(archive)),
        "path": path,
        "name": Path(path).name,
        "extension": suffix,
        "size_bytes": size,
        "kind": _asset_kind(path),
        "date_hint": _date_hint(path),
    }


def _parse_contacts(member: TakeoutMember, raw: bytes) -> Iterator[dict[str, Any]]:
    text = raw.decode("utf-8", "replace")
    for card in text.split("BEGIN:VCARD"):
        if "END:VCARD" not in card:
            continue
        fields: dict[str, list[str]] = {}
        for line in card.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.split(";", 1)[0].upper()
            fields.setdefault(key, []).append(value.strip())
        name = _first(fields, "FN") or _first(fields, "N")
        emails = tuple(sorted(set(fields.get("EMAIL", ()))))
        phones = tuple(sorted(set(fields.get("TEL", ()))))
        if not name and not emails and not phones:
            continue
        yield {
            "id": _stable_id("contact", name, emails, phones),
            "source_archive": str(member.archive),
            "source_member": member.path,
            "name": name,
            "emails": emails,
            "phones": phones,
            "organization": _first(fields, "ORG"),
            "updated_at": _first(fields, "REV"),
        }


def _parse_keep_json(member: TakeoutMember, raw: bytes) -> dict[str, Any] | None:
    payload = _json(raw)
    if not isinstance(payload, dict):
        return None
    created = _timestamp_usec(payload.get("createdTimestampUsec"))
    edited = _timestamp_usec(payload.get("userEditedTimestampUsec"))
    title = _string(payload.get("title"))
    text = _string(payload.get("textContent"))
    return {
        "id": _stable_id("keep", created, edited, title, text),
        "source_archive": str(member.archive),
        "source_member": member.path,
        "created_at": created,
        "edited_at": edited,
        "title": title,
        "text": text,
        "color": _string(payload.get("color")),
        "is_archived": bool(payload.get("isArchived")),
        "is_pinned": bool(payload.get("isPinned")),
        "is_trashed": bool(payload.get("isTrashed")),
        "labels": tuple(_string(row.get("name")) for row in payload.get("labels", []) if isinstance(row, dict)),
    }


def _parse_my_activity(member: TakeoutMember, raw: bytes) -> Iterator[dict[str, Any]]:
    service = _activity_service(member.path)
    for card in _activity_cards(raw):
        texts = _html_texts(card)[:8]
        if len(texts) < 2:
            continue
        timestamp = _activity_timestamp(texts)
        title = texts[1] if texts[0] == service and len(texts) > 1 else texts[0]
        rendered = " | ".join(texts)
        yield {
            "id": _stable_id("my_activity", service, timestamp, title, member.path),
            "source_archive": str(member.archive),
            "source_member": member.path,
            "service": service,
            "timestamp_text": timestamp,
            "title": title,
            "text": rendered[:500],
        }


def _activity_cards(raw: bytes) -> Iterator[str]:
    text = raw.decode("utf-8", "replace")
    pattern = re.compile(r'<div class="outer-cell[^"]*">')
    matches = pattern.finditer(text)
    previous_end: int | None = None
    for match in matches:
        if previous_end is not None:
            yield text[previous_end:match.start()]
        previous_end = match.end()
    if previous_end is not None:
        yield text[previous_end:]


def _html_texts(fragment: str) -> list[str]:
    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment, flags=re.DOTALL | re.IGNORECASE)
    stripped = re.sub(r"<[^>]+>", "\n", stripped)
    stripped = (
        stripped.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    rows: list[str] = []
    for line in stripped.splitlines():
        value = line.strip()
        if not value or value in {"Products:", "Details:"}:
            continue
        rows.append(value[:240])
        if len(rows) >= 8:
            break
    return rows


def _parse_purchase(member: TakeoutMember, raw: bytes) -> dict[str, Any] | None:
    payload = _json(raw)
    if not isinstance(payload, dict):
        return None
    created = _usec_time(payload.get("creationTime"))
    merchant = _nested_string(payload, "transactionMerchant", "name")
    raw_items = payload.get("lineItem")
    items = raw_items if isinstance(raw_items, list) else []
    item_names = tuple(
        _string(item.get("name") or _nested_string(item, "purchase", "productInfo", "name"))
        for item in items
        if isinstance(item, dict)
    )
    return {
        "id": _stable_id("purchase", payload.get("merchantOrderId"), created, merchant, item_names),
        "source_archive": str(member.archive),
        "source_member": member.path,
        "created_at": created,
        "merchant": merchant,
        "order_id": _string(payload.get("merchantOrderId")),
        "item_names": item_names,
        "item_count": len(item_names),
        "total": _purchase_total(payload),
    }


def _parse_play_store(member: TakeoutMember, raw: bytes) -> Iterator[dict[str, Any]]:
    payload = _json(raw)
    if not isinstance(payload, list):
        return
    category = Path(member.path).stem
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        body = item.get("orderHistory") if isinstance(item.get("orderHistory"), dict) else item
        if not isinstance(body, dict):
            continue
        created = _string(body.get("creationTime") or body.get("firstInstallationTime") or body.get("lastUpdateTime"))
        title = _play_title(body)
        yield {
            "id": _stable_id("play_store", category, body.get("orderId") or title, created, index),
            "source_archive": str(member.archive),
            "source_member": member.path,
            "category": category,
            "created_at": created,
            "title": title,
            "order_id": _string(body.get("orderId")),
            "total_price": _string(body.get("totalPrice")),
            "document_type": _play_document_type(body),
        }


def _parse_tasks(member: TakeoutMember, raw: bytes) -> Iterator[dict[str, Any]]:
    payload = _json(raw)
    if not isinstance(payload, dict):
        return
    for task_list in _task_lists(payload):
        list_title = _string(task_list.get("title"))
        list_id = _string(task_list.get("id"))
        for task in task_list.get("items", []):
            if not isinstance(task, dict):
                continue
            title = _string(task.get("title"))
            notes = _string(task.get("notes"))
            created = _string(task.get("created"))
            updated = _string(task.get("updated"))
            completed = _string(task.get("completed"))
            yield {
                "id": _stable_id("task", task.get("id"), list_id, created, title, notes),
                "source_archive": str(member.archive),
                "source_member": member.path,
                "list_id": list_id,
                "list_title": list_title,
                "task_id": _string(task.get("id")),
                "title": title,
                "notes": notes,
                "created_at": created,
                "updated_at": updated,
                "due_at": _string(task.get("due")),
                "completed_at": completed,
                "status": _string(task.get("status")),
                "links": tuple(
                    link.get("link")
                    for link in task.get("links", [])
                    if isinstance(link, dict) and isinstance(link.get("link"), str)
                ),
            }


def _parse_youtube_csv(member: TakeoutMember, raw: bytes) -> Iterator[dict[str, Any]]:
    text = raw.decode("utf-8-sig", "replace")
    reader = csv.DictReader(StringIO(text))
    category = "/".join(Path(member.path).parts[2:-1])
    for index, row in enumerate(reader):
        compact = {str(k): v for k, v in row.items() if k and v}
        if not compact:
            continue
        title = compact.get("Title") or compact.get("Video Title") or compact.get("Channel Title") or compact.get("Name")
        yield {
            "id": _stable_id("youtube", category, title, compact, index),
            "source_archive": str(member.archive),
            "source_member": member.path,
            "category": category,
            "title": title,
            "row": compact,
        }


def _task_lists(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


def _write_row(
    handle: Any,
    seen: set[str],
    counts: Counter[str],
    product: str,
    row: dict[str, Any],
) -> None:
    row_id = str(row.get("id") or "")
    if not row_id or row_id in seen:
        return
    seen.add(row_id)
    counts[product] += 1
    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None


def _stable_id(*parts: Any) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _first(fields: dict[str, list[str]], key: str) -> str | None:
    values = fields.get(key)
    return values[0] if values else None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _timestamp_usec(value: Any) -> str | None:
    try:
        seconds = int(str(value)) / 1_000_000
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _usec_time(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return _timestamp_usec(value.get("usecSinceEpochUtc"))


def _nested_string(payload: dict[str, Any], *keys: str) -> str | None:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _string(value)


def _purchase_total(payload: dict[str, Any]) -> dict[str, Any] | None:
    priceline = payload.get("priceline")
    if not isinstance(priceline, list):
        return None
    for row in priceline:
        if isinstance(row, dict) and row.get("type") in {"TOTAL", "SUBTOTAL"}:
            amount = row.get("amount")
            return amount if isinstance(amount, dict) else None
    return None


def _play_title(payload: dict[str, Any]) -> str | None:
    line_items = payload.get("lineItem")
    if isinstance(line_items, list) and line_items:
        first = line_items[0]
        if isinstance(first, dict):
            title = _nested_string(first, "doc", "title")
            if title:
                return title
    return _string(payload.get("title") or payload.get("name") or payload.get("docTitle"))


def _play_document_type(payload: dict[str, Any]) -> str | None:
    line_items = payload.get("lineItem")
    if isinstance(line_items, list) and line_items:
        first = line_items[0]
        if isinstance(first, dict):
            return _nested_string(first, "doc", "documentType")
    return None


def _activity_service(path: str) -> str:
    parts = Path(path).parts
    return parts[2] if len(parts) > 3 else "unknown"


def _activity_timestamp(texts: list[str]) -> str | None:
    for text in texts:
        if re.search(r"\b\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M\s+UTC\b", text):
            return text
    return None


def _archive_date(path: Path) -> str | None:
    match = re.search(r"(\d{8})T", path.name, re.IGNORECASE)
    if not match:
        return None
    raw = match.group(1)
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def _date_hint(path: str) -> str | None:
    match = re.search(r"(20\d{2})[-_/](\d{2})[-_/](\d{2})", path)
    if match:
        return "-".join(match.groups())
    return None


def _asset_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".heic", ".dng", ".webp"}:
        return "image"
    if suffix in {".mp4", ".avi", ".wmv", ".mov", ".m4v"}:
        return "video"
    if suffix in {".mp3", ".m4a", ".awb", ".3gp"}:
        return "audio"
    if suffix in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"}:
        return "document"
    if suffix in {".mbox"}:
        return "mailbox"
    if suffix in {".tcx", ".fit", ".gpx"}:
        return "fitness"
    return "file"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize typed Google Takeout products")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_google_takeout_products(root=args.root)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
