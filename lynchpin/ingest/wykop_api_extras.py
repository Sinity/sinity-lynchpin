from __future__ import annotations

import json
import math
import time
from typing import Any

import requests
import typer

from .wykop_api import WykopApiClient, api_iter_pages
from .wykop_common import same_username
from .wykop_io import now_iso, read_last_jsonl_obj, write_json, write_jsonl


def scrape_api_extras(
    *,
    api_client: WykopApiClient,
    username: str,
    auth_username: str | None,
    user_dir: Any,
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
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if allow_statuses is not None and status in allow_statuses:
                write_json(
                    out_path,
                    {
                        "skipped": True,
                        "endpoint": endpoint,
                        "status": status,
                        "scraped_at": now_iso(),
                        "error": str(exc),
                    },
                )
                record(key, {"output": str(out_path), "endpoint": endpoint, "ok": True, "skipped": True, "status": status})
                return
            record(key, {"output": str(out_path), "endpoint": endpoint, "ok": False, "status": status, "error": str(exc)})
            return
        write_json(out_path, payload)
        record(key, {"output": str(out_path), "endpoint": endpoint, "ok": True})
        time.sleep(delay_seconds)

    include_self = auth_username is not None and same_username(username, auth_username)
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
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("profile", {"output": str(profile_path), "ok": False, "status": status, "error": str(exc)})
    else:
        write_json(profile_path, profile)
        record("profile", {"output": str(profile_path), "ok": True})
        time.sleep(delay_seconds)

    badges_path = user_dir / "wykop_badges.json"
    try:
        badges = api_client.get(f"profile/users/{username}/badges")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("badges", {"output": str(badges_path), "ok": False, "status": status, "error": str(exc)})
    else:
        write_json(badges_path, badges)
        record("badges", {"output": str(badges_path), "ok": True})
        time.sleep(delay_seconds)

    tags_path = user_dir / "wykop_tags.json"
    tags_rows: list[dict[str, Any]] = []
    pages = 0
    try:
        for page, payload in api_iter_pages(api_client, f"profile/users/{username}/tags", params={}, max_pages=max_pages):
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
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("tags", {"output": str(tags_path), "ok": False, "status": status, "error": str(exc)})
    else:
        write_json(
            tags_path,
            {
                "username": username,
                "scraped_at": now_iso(),
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
        for page, payload in api_iter_pages(
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
                observed_tags.extend(data.values())
            time.sleep(delay_seconds)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("observed_tags", {"output": str(observed_tags_path), "ok": False, "status": status, "error": str(exc)})
    else:
        write_json(
            observed_tags_path,
            {
                "username": username,
                "scraped_at": now_iso(),
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
        for page, payload in api_iter_pages(api_client, f"profile/users/{username}/actions", params={}, max_pages=max_pages):
            pages = page
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        actions_rows.append({"platform": "wykop", "kind": "action", "username": username, "page": page, **item})
            time.sleep(delay_seconds)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("actions", {"output": str(actions_path), "ok": False, "status": status, "error": str(exc)})
    else:
        write_jsonl(actions_path, actions_rows)
        record("actions", {"output": str(actions_path), "ok": True, "items": len(actions_rows), "pages": pages})

    followers_path = user_dir / "wykop_followers.json"
    followers: list[Any] = []
    pages = 0
    try:
        for page, payload in api_iter_pages(
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
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("followers", {"output": str(followers_path), "ok": False, "status": status, "error": str(exc)})
    else:
        write_json(
            followers_path,
            {
                "username": username,
                "scraped_at": now_iso(),
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
        for page, payload in api_iter_pages(
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
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("following", {"output": str(following_path), "ok": False, "status": status, "error": str(exc)})
    else:
        write_json(
            following_path,
            {
                "username": username,
                "scraped_at": now_iso(),
                "endpoint": "profile/users/{username}/observed/users/following",
                "pages": pages,
                "items": following,
            },
        )
        record("following", {"output": str(following_path), "ok": True, "items": len(following), "pages": pages})

    if not include_self:
        extras["completed_at"] = now_iso()
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
    dump_json("settings_changephone", "settings/changephone", "wykop_settings_changephone.json", allow_statuses={404})
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
            for page, payload in api_iter_pages(api_client, endpoint, params={}, max_pages=max_pages):
                pages = page
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            rows.append({"platform": "wykop", "kind": "setting_item", "endpoint": endpoint, "page": page, **item})
                time.sleep(delay_seconds)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            record(key, {"output": str(out_path), "endpoint": endpoint, "ok": False, "status": status, "error": str(exc)})
            continue
        write_jsonl(out_path, rows)
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
                last_obj = read_last_jsonl_obj(out_path)
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
            with out_path.open("a", encoding="utf-8") as handle:
                page_idx = 0
                while True:
                    payload = api_client.get(endpoint, params={"page": token} if token is not None else None)
                    page_idx += 1
                    pages += 1
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                handle.write(
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
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            item = {
                "output": str(out_path),
                "endpoint": endpoint,
                "ok": False,
                "status": status,
                "error": str(exc),
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
            last_obj = read_last_jsonl_obj(observed_discussions_path)
            last_token = last_obj.get("page_token") if isinstance(last_obj, dict) else None
            params = {"page": last_token} if isinstance(last_token, str) and last_token else None
            probe = api_client.get("observed/discussions", params=params)
            pagination = probe.get("pagination") if isinstance(probe, dict) else None
            next_token = pagination.get("next") if isinstance(pagination, dict) else None
            if isinstance(next_token, str) and next_token:
                start_token = next_token
            else:
                record("observed_discussions", {"output": str(observed_discussions_path), "endpoint": "observed/discussions", "ok": True, "complete": True, "appended_items": 0, "appended_pages": 0})
                start_token = None
                raise StopIteration

        seen: set[str] = set()
        token = start_token
        mode = "resume" if existing else "fresh"
        typer.echo(f"[wykop] extras: observed/discussions ({mode}) starting_token={token!r}", err=True)
        with observed_discussions_path.open("a", encoding="utf-8") as handle:
            page_idx = 0
            while True:
                payload = api_client.get("observed/discussions", params={"page": token} if token is not None else None)
                page_idx += 1
                pages += 1
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            handle.write(
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
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        item = {
            "output": str(observed_discussions_path),
            "endpoint": "observed/discussions",
            "ok": False,
            "status": status,
            "error": str(exc),
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
        record("observed_discussions", {"output": str(observed_discussions_path), "endpoint": "observed/discussions", "ok": True, "appended_items": observed_discussions_written, "appended_pages": pages})

    notifications_status_path = user_dir / "wykop_notifications_status.json"
    try:
        notifications_status = api_client.get("notifications/status")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("notifications_status", {"output": str(notifications_status_path), "ok": False, "status": status, "error": str(exc)})
    else:
        write_json(notifications_status_path, notifications_status)
        record("notifications_status", {"output": str(notifications_status_path), "ok": True})
        time.sleep(delay_seconds)

    notification_group_ids: set[str] = set()
    for scope in ["pm", "entries", "tags", "observed-discussions"]:
        endpoint = f"notifications/{scope}"
        out_path = user_dir / f"wykop_notifications_{scope}.jsonl"
        rows: list[dict[str, Any]] = []
        pages = 0
        try:
            for page, payload in api_iter_pages(api_client, endpoint, params={}, max_pages=max_pages):
                pages = page
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            gid = item.get("group_id")
                            if isinstance(gid, str) and gid:
                                notification_group_ids.add(gid)
                            rows.append({"platform": "wykop", "kind": "notification", "scope": scope, "endpoint": endpoint, "page": page, **item})
                time.sleep(delay_seconds)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            record(f"notifications_{scope}", {"output": str(out_path), "endpoint": endpoint, "ok": False, "status": status, "error": str(exc)})
            continue
        write_jsonl(out_path, rows)
        record(f"notifications_{scope}", {"output": str(out_path), "endpoint": endpoint, "ok": True, "items": len(rows), "pages": pages})

    groups_out_path = user_dir / "wykop_notification_groups.jsonl"
    group_rows: list[dict[str, Any]] = []
    try:
        for gid in sorted(notification_group_ids):
            endpoint = f"notifications/groups/{gid}"
            for page, payload in api_iter_pages(api_client, endpoint, params={}, max_pages=max_pages):
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            group_rows.append({"platform": "wykop", "kind": "notification_group_item", "group_id": gid, "endpoint": endpoint, "page": page, **item})
                time.sleep(delay_seconds)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("notification_groups", {"output": str(groups_out_path), "ok": False, "status": status, "error": str(exc)})
    else:
        write_jsonl(groups_out_path, group_rows)
        record("notification_groups", {"output": str(groups_out_path), "ok": True, "groups": len(notification_group_ids), "items": len(group_rows)})

    pm_dir = user_dir / "pm"
    pm_dir.mkdir(parents=True, exist_ok=True)

    pm_conversations_path = user_dir / "wykop_pm_conversations.json"
    try:
        pm_convs_payload = api_client.get("pm/conversations", params={"page": 1})
        pm_pagination = pm_convs_payload.get("pagination") if isinstance(pm_convs_payload, dict) else None
        pm_total = pm_pagination.get("total") if isinstance(pm_pagination, dict) else None
        pm_per_page = pm_pagination.get("per_page") if isinstance(pm_pagination, dict) else None
        pm_pages = max(1, math.ceil(pm_total / pm_per_page)) if isinstance(pm_total, int) and isinstance(pm_per_page, int) and pm_per_page > 0 else 1
        pm_conversations: list[dict[str, Any]] = []
        for page in range(1, (min(pm_pages, max_pages) if max_pages else pm_pages) + 1):
            payload = api_client.get("pm/conversations", params={"page": page})
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        pm_conversations.append({"page": page, **item})
            time.sleep(delay_seconds)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        record("pm_conversations", {"output": str(pm_conversations_path), "ok": False, "status": status, "error": str(exc)})
        pm_conversations = []
    else:
        write_json(
            pm_conversations_path,
            {
                "username": username,
                "scraped_at": now_iso(),
                "endpoint": "pm/conversations",
                "items": pm_conversations,
            },
        )
        record("pm_conversations", {"output": str(pm_conversations_path), "ok": True, "items": len(pm_conversations), "pages": pm_pages})

    thread_usernames: list[str] = []
    for row in pm_conversations:
        other = row.get("user") if isinstance(row, dict) else None
        user = other.get("username") if isinstance(other, dict) else None
        if isinstance(user, str) and user and user not in thread_usernames:
            thread_usernames.append(user)
    pm_threads_ok = 0
    for other_username in thread_usernames:
        out_path = pm_dir / f"{other_username}.json"
        try:
            thread_payload = api_client.get(f"pm/conversations/{other_username}")
        except requests.HTTPError:
            continue
        write_json(out_path, thread_payload)
        pm_threads_ok += 1
        time.sleep(delay_seconds)
    record("pm_threads", {"output_dir": str(pm_dir), "ok": True, "threads": len(thread_usernames), "threads_written": pm_threads_ok})

    extras["completed_at"] = now_iso()
    return extras
