from __future__ import annotations

import json

from lynchpin.sources.takeout_chrome import iter_takeout_chrome_visits


def test_iter_takeout_chrome_visits_reads_current_session_format(tmp_path) -> None:
    path = tmp_path / "History.json"
    path.write_text(json.dumps({
        "Session": [
            {
                "tab": {
                    "navigation": [
                        {
                            "timestamp_msec": "1773717025000",
                            "virtual_url": "https://example.com/",
                            "title": "Example",
                        },
                        {
                            "timestamp_msec": "1773717026000",
                            "virtual_url": "chrome://settings",
                            "title": "Settings",
                        },
                    ]
                }
            }
        ]
    }))

    visits = list(iter_takeout_chrome_visits(path, source_label="takeout"))

    assert len(visits) == 1
    assert visits[0].url == "https://example.com/"
    assert visits[0].title == "Example"
    assert visits[0].source == "takeout"


def test_iter_takeout_chrome_visits_ignores_legacy_browser_history_shape(tmp_path) -> None:
    path = tmp_path / "BrowserHistory.json"
    path.write_text(json.dumps({
        "Browser History": [
            {
                "time_usec": "1773717025000000",
                "url": "https://legacy.example/",
                "title": "Legacy",
            }
        ]
    }))

    assert list(iter_takeout_chrome_visits(path)) == []
