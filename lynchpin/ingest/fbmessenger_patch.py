from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from .fbmessenger_chrome import requests_user_agent
from .fbmessenger_db import MessengerExportDb


def _patch_fbchat(fbchat: Any) -> None:
    fbchat._util.USER_AGENTS = [requests_user_agent()]  # type: ignore[attr-defined]
    fbchat._state.FB_DTSG_REGEX = re.compile(  # type: ignore[attr-defined]
        r'(?:\"name\"\\s*:\\s*\"fb_dtsg\"\\s*,\\s*\"value\"\\s*:\\s*\"|name=\"fb_dtsg\"[^>]*value=\")([^\"<>]+)'
    )

    def _from_session(cls, session):  # type: ignore[no-redef]
        user_id = fbchat._state.get_user_id(session)
        mobile = session.get("https://m.facebook.com/")
        soup_mobile = fbchat._state.find_input_fields(mobile.text)
        fb_dtsg_element = soup_mobile.find("input", {"name": "fb_dtsg"})
        if fb_dtsg_element:
            fb_dtsg = fb_dtsg_element["value"]
        else:
            match = fbchat._state.FB_DTSG_REGEX.search(mobile.text)
            if not match:
                raise RuntimeError("Unable to extract fb_dtsg from m.facebook.com")
            fb_dtsg = match.group(1)

        desktop = session.get("https://www.facebook.com/")
        revision_match = re.search(r'\"client_revision\":(\\d+)', desktop.text)
        revision = int(revision_match.group(1)) if revision_match else 0
        soup_desktop = fbchat._state.find_input_fields(desktop.text)
        logout_h_element = soup_desktop.find("input", {"name": "h"})
        logout_h = logout_h_element["value"] if logout_h_element else None

        return cls(
            user_id=user_id,
            fb_dtsg=fb_dtsg,
            revision=revision,
            session=session,
            logout_h=logout_h,
        )

    fbchat._state.State.from_session = classmethod(_from_session)  # type: ignore[attr-defined]

    def _is_logged_in(self) -> bool:  # type: ignore[no-redef]
        return bool(self._session.cookies.get("c_user"))

    fbchat._state.State.is_logged_in = _is_logged_in  # type: ignore[attr-defined]
    try:
        from fbchat import _group  # type: ignore
    except Exception:
        _group = None
    if _group is not None:

        def _group_from_graphql(cls, data):  # type: ignore[no-redef]
            if data.get("image") is None:
                data["image"] = {}
            c_info = cls._parse_customization_info(data)
            last_message_timestamp = None
            last_message = data.get("last_message") or {}
            last_nodes = last_message.get("nodes") or []
            if last_nodes:
                last_message_timestamp = last_nodes[0].get("timestamp_precise")
            plan = None
            event_reminders = data.get("event_reminders") or {}
            event_nodes = event_reminders.get("nodes") or []
            if event_nodes:
                plan = _group._plan.Plan._from_graphql(event_nodes[0])
            joinable = data.get("joinable_mode") or {}
            thread_admins = data.get("thread_admins") or []
            approval_queue = data.get("group_approval_queue") or {}
            approval_nodes = approval_queue.get("nodes") or []

            return cls(
                data["thread_key"]["thread_fbid"],
                participants=set(node["messaging_actor"]["id"] for node in data["all_participants"]["nodes"]),
                nicknames=c_info.get("nicknames"),
                color=c_info.get("color"),
                emoji=c_info.get("emoji"),
                admins=set(node.get("id") for node in thread_admins),
                approval_mode=bool(data.get("approval_mode"))
                if data.get("approval_mode") is not None
                else None,
                approval_requests=set(node["requester"]["id"] for node in approval_nodes)
                if approval_nodes
                else None,
                join_link=joinable.get("link"),
                photo=data["image"].get("uri"),
                name=data.get("name"),
                message_count=data.get("messages_count"),
                last_message_timestamp=last_message_timestamp,
                plan=plan,
            )

        _group.Group._from_graphql = classmethod(_group_from_graphql)  # type: ignore[attr-defined]
    try:
        from fbchat import _client  # type: ignore
    except Exception:
        _client = None
    if _client is not None:

        def _fetch_thread_list(self, thread_location, before=None, after=None, limit=20, offset=None):  # type: ignore[no-redef]
            if offset is not None:
                _client.log.warning(
                    "Using `offset` in `fetchThreadList` is no longer supported, "
                    "since Facebook migrated to GraphQL. Use `before` instead."
                )
            if limit > 20 or limit < 1:
                raise _client.FBchatUserError("`limit` should be between 1 and 20")
            if thread_location in _client.ThreadLocation:
                loc_str = thread_location.value
            else:
                raise _client.FBchatUserError('"thread_location" must be a value of ThreadLocation')
            params = {
                "limit": limit,
                "tags": [loc_str],
                "before": before,
                "includeDeliveryReceipts": True,
                "includeSeqID": False,
            }
            (response,) = self.graphql_requests(_client._graphql.from_doc_id("1349387578499440", params))
            threads = []
            for node in response["viewer"]["message_threads"]["nodes"]:
                thread_type = node.get("thread_type")
                if thread_type == "GROUP":
                    threads.append(_client.Group._from_graphql(node))
                elif thread_type in ("ONE_TO_ONE", "AI_BOT"):
                    threads.append(_client.User._from_thread_fetch(node))
                else:
                    _client.log.warning("Unknown thread type %s; skipping", thread_type)
            return threads

        _client.Client.fetchThreadList = _fetch_thread_list  # type: ignore[attr-defined]
    try:
        from fbchat import _user  # type: ignore
    except Exception:
        _user = None
    if _user is not None:

        def _user_from_thread_fetch(cls, data):  # type: ignore[no-redef]
            c_info = cls._parse_customization_info(data)
            participants = [node["messaging_actor"] for node in data["all_participants"]["nodes"]]
            target_id = data["thread_key"].get("other_user_id")
            user = next((participant for participant in participants if participant.get("id") == target_id), None)
            if user is None and participants:
                user = participants[0]
            if user is None:
                user = {}

            last_message_timestamp = None
            last_message = data.get("last_message") or {}
            last_nodes = last_message.get("nodes") or []
            if last_nodes:
                last_message_timestamp = last_nodes[0].get("timestamp_precise")

            first_name = user.get("short_name")
            last_name = None if first_name is None else (user.get("name") or "").split(first_name, 1).pop().strip() or None

            plan = None
            event_reminders = data.get("event_reminders") or {}
            event_nodes = event_reminders.get("nodes") or []
            if event_nodes:
                plan = _user._plan.Plan._from_graphql(event_nodes[0])

            photo = None
            big_image = user.get("big_image_src") or {}
            if isinstance(big_image, dict):
                photo = big_image.get("uri")

            return cls(
                user.get("id"),
                url=user.get("url"),
                name=user.get("name"),
                first_name=first_name,
                last_name=last_name,
                is_friend=user.get("is_viewer_friend"),
                gender=_user.GENDERS.get(user.get("gender")),
                affinity=user.get("affinity"),
                nickname=c_info.get("nickname"),
                color=c_info.get("color"),
                emoji=c_info.get("emoji"),
                own_nickname=c_info.get("own_nickname"),
                photo=photo,
                message_count=data.get("messages_count"),
                last_message_timestamp=last_message_timestamp,
                plan=plan,
            )

        _user.User._from_thread_fetch = classmethod(_user_from_thread_fetch)  # type: ignore[attr-defined]


def _process_all(exporter: Any, client: Any, db: MessengerExportDb, locations: Optional[str]):
    if locations:
        mapping = {
            "inbox": exporter.ThreadLocation.INBOX,
            "other": exporter.ThreadLocation.OTHER,
            "archived": exporter.ThreadLocation.ARCHIVED,
        }
        selected_locations = []
        for raw in locations.split(","):
            key = raw.strip().lower()
            if not key:
                continue
            if key not in mapping:
                raise RuntimeError(f"Unknown thread location: {raw}")
            selected_locations.append(mapping[key])
        if not selected_locations:
            raise RuntimeError("No thread locations selected.")
    else:
        selected_locations = [
            exporter.ThreadLocation.INBOX,
            exporter.ThreadLocation.OTHER,
            exporter.ThreadLocation.ARCHIVED,
        ]

    threads = []
    for location in selected_locations:
        exporter.logger.info("Fetching threads: %s", location)
        fetched_threads = client.fetchThreads(location)
        exporter.logger.info("Fetched %d threads from %s", len(fetched_threads), location)
        threads.extend(fetched_threads)
    exporter.logger.info("Total threads: %d", len(threads))

    for index, thread in enumerate(threads, 1):
        if index == 1 or index % 50 == 0:
            exporter.logger.info("Indexing thread %d/%d: %s", index, len(threads), thread.name)
        db.insert_thread(thread)

    for index, thread in enumerate(threads, 1):
        if index == 1 or index % 25 == 0:
            exporter.logger.info("Exporting thread %d/%d: %s", index, len(threads), thread.name)
        oldest_newest = db.get_oldest_and_newest(thread)
        oldest, newest = oldest_newest if oldest_newest is not None else (None, None)

        def error(exc):
            exporter.logger.error("While processing thread %s", thread)
            exporter.logger.exception(exc)
            yield exc

        for result in exporter.iter_thread(client=client, thread=thread, before=oldest):
            if isinstance(result, Exception):
                yield from error(result)
            else:
                db.insert_message(thread, result)

        if newest is not None:
            with db.db:
                for result in exporter.iter_thread(client=client, thread=thread, before=None):
                    if isinstance(result, Exception):
                        yield from error(result)
                    else:
                        message_timestamp = int(result.timestamp)
                        if newest > message_timestamp:
                            exporter.logger.info(
                                "%s: fetched all new messages (up to %s)",
                                thread.name,
                                newest,
                            )
                            break
                        db.insert_message(thread, result)

        yield from db.check_fetched_all(thread)


def run_export(*, cookies: str, output_db: Path, locations: Optional[str]) -> None:
    import fbmessengerexport.export as exporter  # type: ignore

    try:
        import fbchat  # type: ignore
    except Exception:
        fbchat = None

    if fbchat is not None:
        _patch_fbchat(fbchat)

    exporter.ExportDb = MessengerExportDb  # type: ignore[attr-defined]
    exporter.process_all = lambda client, db: _process_all(exporter, client, db, locations)  # type: ignore[attr-defined]
    exporter.run(cookies=cookies, db=output_db)
