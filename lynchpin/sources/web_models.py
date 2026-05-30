"""Dataclasses for the webhistory source API."""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dataclass_field
from datetime import date as _date_type, datetime

from ..core.primitives import TopN


@dataclass(frozen=True)
class WebHistoryEntry:
    date: str
    record_json: str
    source_file: str

    def to_record(self) -> dict[str, object]:
        data = json.loads(self.record_json)
        if not isinstance(data, dict):
            return {"_source_file": self.source_file}
        data["_source_file"] = self.source_file
        return data


@dataclass(frozen=True)
class WebHistoryVisit:
    timestamp: datetime
    url: str
    title: str
    source: str


@dataclass(frozen=True)
class WebHistoryRawEntry:
    timestamp: datetime
    url: str
    title: str
    payload_json: str
    source_file: str

    def payload(self) -> dict[str, object]:
        data = json.loads(self.payload_json)
        return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class WebDayActivity:
    date: _date_type
    visit_count: int
    unique_domains: int
    top_domains: tuple[tuple[str, float], ...]
    top_titles: tuple[str, ...]


@dataclass  # not frozen: mutable accumulator bucket mutated in-loop in web.py
class _WebDayBucket:
    count: int = 0
    domains: TopN = dataclass_field(default_factory=lambda: TopN(10))
    titles: TopN = dataclass_field(default_factory=lambda: TopN(5))
