from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from typing import Iterator, List

from ...core.cache import file_signature, persistent_cache
from ...core.config import get_config


@dataclass
class SessionRecord:
    date: date
    provider: str
    label: str
    doc_path: str
    highlights: str


@persistent_cache(
    "session_records",
    depends_on=lambda: file_signature(get_config().sessions_csv),
)
def iter_sessions() -> Iterator[SessionRecord]:
    cfg = get_config()
    path = cfg.sessions_csv
    if not path.exists():
        return iter(())

    def generator() -> Iterator[SessionRecord]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw_date = (row.get("date") or "").strip()
                if not raw_date:
                    continue
                try:
                    dt = date.fromisoformat(raw_date)
                except ValueError:
                    continue
                highlights = (row.get("highlights") or "").split("||")
                yield SessionRecord(
                    date=dt,
                    provider=row.get("provider", ""),
                    label=row.get("label", ""),
                    doc_path=row.get("doc_path", ""),
                    highlights=(highlights[0].strip() if highlights and highlights[0].strip() else ""),
                )

    return generator()


def sessions_by_date(target: date) -> List[SessionRecord]:
    return [record for record in iter_sessions() if record.date == target]
