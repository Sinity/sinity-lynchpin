from __future__ import annotations

import json

from lynchpin.ingest import themotte_sync


def test_sync_themotte_writes_raw_products(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_chrome(target: str, *args: str, check: bool = True) -> str:
        calls.append(args)
        if args[0] == "new-tab":
            return json.dumps({"id": "page-1"})
        if args[0] == "evaluate":
            js = args[-1]
            if "recipient" in js:
                return json.dumps(
                    {
                        "result": {
                            "result": {
                                "value": {
                                    "rows": [
                                        {
                                            "id": "1",
                                            "author": "Sinity",
                                            "recipient": "self_made_human",
                                            "peer": "self_made_human",
                                            "body": "hello",
                                            "created_at": "2026-02-01T10:00:00.000Z",
                                            "url": "https://www.themotte.org/comment/1",
                                        }
                                    ],
                                    "next_url": None,
                                }
                            }
                        }
                    }
                )
            return json.dumps(
                {
                    "result": {
                        "result": {
                            "value": {
                                "rows": [
                                    {
                                        "id": "n1",
                                        "kind": "Username Mention",
                                        "actor": "naraburns",
                                        "title": "Username Mention",
                                        "text": "mentioned @Sinity",
                                        "created_at": "2026-02-01T11:00:00.000Z",
                                        "url": "https://www.themotte.org/comment/2",
                                    }
                                ],
                                "next_url": None,
                            }
                        }
                    }
                }
            )
        return "true"

    monkeypatch.setattr(themotte_sync, "_chrome", fake_chrome)
    report = themotte_sync.sync_themotte(
        username="Sinity",
        root=tmp_path,
        method="cdp",
        max_message_pages=1,
        max_notification_pages=1,
    )

    profile = tmp_path / "Sinity"
    assert report["message_count"] == 1
    assert report["notification_count"] == 1
    assert json.loads((profile / "themotte_messages.jsonl").read_text(encoding="utf-8"))["peer"] == "self_made_human"
    assert json.loads((profile / "themotte_notifications.jsonl").read_text(encoding="utf-8"))["actor"] == "naraburns"
    assert (profile / "sync_manifest.json").exists()
    assert any(call[0] == "close" for call in calls)
