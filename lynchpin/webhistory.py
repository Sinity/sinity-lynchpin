from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from .config import get_config


def iter_entries(start_date: Optional[str] = None, end_date: Optional[str] = None) -> Iterator[Dict[str, object]]:
    cfg = get_config()
    path = cfg.webhistory_dir
    if not path.exists():
        return iter(())

    def generator() -> Iterator[Dict[str, object]]:
        for file in sorted(path.glob("*.jsonl")):
            with file.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts_value = record.get("visitTime") or record.get("lastVisitTime")
                    ts = _to_datetime(ts_value)
                    if not ts:
                        continue
                    iso = ts.date().isoformat()
                    if start_date and iso < start_date:
                        continue
                    if end_date and iso > end_date:
                        continue
                    record["_source_file"] = str(file)
                    yield record

    return generator()


def _to_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric > 10**16:
        seconds = numeric / 1_000_000_000
    elif numeric > 10**12:
        seconds = numeric / 1_000
    else:
        seconds = numeric
    try:
        return datetime.fromtimestamp(seconds)
    except (OSError, OverflowError, ValueError):
        return None
