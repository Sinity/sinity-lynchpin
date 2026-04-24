"""Tests for clipboard source."""

import json
from datetime import date
from types import SimpleNamespace

from lynchpin.sources import clipboard


def test_clipboard_reads_clipse_history_and_dedupes(tmp_path, monkeypatch):
    path = tmp_path / "clipboard_history.json"
    path.write_text(json.dumps({
        "clipboardHistory": [
            {"value": "https://example.com", "recorded": "2026-04-21 10:00:00.000000000", "filePath": "null"},
            {"value": "https://example.com", "recorded": "2026-04-21 10:00:00.000000000", "filePath": "null"},
            {"value": "note", "recorded": "2026-04-22 10:00:00.000000000", "filePath": "null", "pinned": True},
        ]
    }))
    monkeypatch.setattr(clipboard, "get_config", lambda: SimpleNamespace(
        clipboard_live_file=path,
        clipboard_export_files=(),
    ))
    clipboard._entries_from_file.cache_clear()

    rows = clipboard.entries_in_range(start=date(2026, 4, 21), end=date(2026, 4, 21))

    assert len(rows) == 1
    assert rows[0].kind == "url"
    assert rows[0].value == "https://example.com"


def test_clipboard_reads_markdown_selection_dump(tmp_path):
    path = tmp_path / "clipboard_history_selections.md"
    path.write_text("""---
generated: 2026-01-15T10:00:00Z
---

```markdown
exact selected text
```
""")
    clipboard._entries_from_file.cache_clear()

    rows = list(clipboard.entries(paths=(path,)))

    assert len(rows) == 1
    assert rows[0].value == "exact selected text"
