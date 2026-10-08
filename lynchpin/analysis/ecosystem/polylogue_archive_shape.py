"""Polylogue raw-log archive shape and projection-size analysis.

This module answers a different question than ``polylogue_metrics``. The
existing metrics inspect Polylogue as a codebase and archive product; this
analysis reconciles the local raw agent logs against the Polylogue archive DB
and measures how much content survives into common Markdown projections.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from lynchpin.core.config import LynchpinConfig, get_config
from lynchpin.core.io import save_json, save_text

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
TEXT_META_PREFIXES = (
    "<system-reminder>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<codex_internal_context>",
    "# AGENTS.md instructions",
    "<environment_context>",
)


@dataclass(frozen=True)
class RawSessionFile:
    provider: str
    path: Path
    size_bytes: int
    local_id: str | None
    id_source: str
    line_count: int | None = None


@dataclass(frozen=True)
class RenderMeasurement:
    provider: str
    local_id: str | None
    path: Path
    raw_bytes: int
    full_markdown_bytes: int
    prose_markdown_bytes: int
    user_markdown_bytes: int
    rendered_messages: int
    prose_messages: int
    user_messages: int

    def ratios(self) -> dict[str, float | None]:
        return {
            "full_raw": _ratio(self.full_markdown_bytes, self.raw_bytes),
            "prose_raw": _ratio(self.prose_markdown_bytes, self.raw_bytes),
            "user_raw": _ratio(self.user_markdown_bytes, self.raw_bytes),
            "prose_full": _ratio(self.prose_markdown_bytes, self.full_markdown_bytes),
            "user_prose": _ratio(self.user_markdown_bytes, self.prose_markdown_bytes),
        }


@dataclass(frozen=True)
class RenderedMessage:
    role: str
    text: str
    prose_candidate: bool


def _ratio(num: int, denom: int) -> float | None:
    if denom <= 0:
        return None
    return round(num / denom, 6)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        if isinstance(text, str):
            return text
    return ""


def _has_only_prose_content(value: Any) -> bool:
    if isinstance(value, str):
        return True
    if isinstance(value, list):
        has_text = False
        for item in value:
            if isinstance(item, str):
                has_text = True
                continue
            if not isinstance(item, dict):
                return False
            kind = item.get("type")
            if kind not in (None, "text", "input_text", "output_text"):
                return False
            has_text = has_text or isinstance(item.get("text") or item.get("content"), str)
        return has_text
    if isinstance(value, dict):
        kind = value.get("type")
        return kind in (None, "text", "input_text", "output_text") and bool(_extract_text(value))
    return False


def _is_prose_text(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not any(stripped.startswith(prefix) for prefix in TEXT_META_PREFIXES)


def parse_claude_record(raw: dict[str, Any]) -> list[RenderedMessage]:
    message = raw.get("message")
    if not isinstance(message, dict):
        return []
    role = str(message.get("role") or raw.get("type") or "unknown")
    content = message.get("content")
    text = _extract_text(content)
    if not text and content is None:
        return []
    is_meta = bool(raw.get("isMeta"))
    prose = role in {"user", "assistant"} and not is_meta and _has_only_prose_content(content) and _is_prose_text(text)
    full_text = text if _has_only_prose_content(content) else _json_dumps(content)
    return [RenderedMessage(role=role, text=full_text, prose_candidate=prose)]


def parse_codex_record(raw: dict[str, Any]) -> list[RenderedMessage]:
    row_type = raw.get("type")
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        return []

    if row_type == "message":
        role = str(payload.get("role") or "unknown")
        content = payload.get("content")
        text = _extract_text(content)
        if not text:
            return []
        return [RenderedMessage(role=role, text=text, prose_candidate=role in {"user", "assistant"} and _is_prose_text(text))]

    if row_type != "response_item":
        return []

    item = payload.get("item")
    if item is None and "type" in payload:
        item = payload
    if not isinstance(item, dict):
        return []
    item_type = str(item.get("type") or "unknown")
    if item_type == "message":
        role = str(item.get("role") or "assistant")
        content = item.get("content")
        text = _extract_text(content)
        if not text:
            return []
        return [RenderedMessage(role=role, text=text, prose_candidate=role in {"user", "assistant"} and _is_prose_text(text))]
    if item_type == "reasoning":
        summary = _extract_text(item.get("summary"))
        if summary:
            return [RenderedMessage(role="assistant", text=summary, prose_candidate=False)]
        return []
    if item_type.endswith("_call") or item_type.endswith("_call_output") or item_type in {
        "function_call",
        "function_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "web_search_call",
    }:
        return [RenderedMessage(role=item_type, text=_json_dumps(item), prose_candidate=False)]
    return []


def iter_rendered_messages(path: Path, provider: str) -> Iterable[RenderedMessage]:
    parser = parse_codex_record if provider == "codex" else parse_claude_record
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict):
                    yield from parser(raw)
    except OSError:
        return


def markdown_for(messages: Iterable[RenderedMessage], *, prose_only: bool = False, user_only: bool = False) -> str:
    blocks: list[str] = []
    for message in messages:
        if prose_only and not message.prose_candidate:
            continue
        if user_only and message.role != "user":
            continue
        blocks.append(f"## {message.role}\n\n{message.text.strip()}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def measure_raw_file(raw_file: RawSessionFile) -> RenderMeasurement:
    messages = list(iter_rendered_messages(raw_file.path, raw_file.provider))
    prose_messages = [message for message in messages if message.prose_candidate]
    user_messages = [message for message in prose_messages if message.role == "user"]
    return RenderMeasurement(
        provider=raw_file.provider,
        local_id=raw_file.local_id,
        path=raw_file.path,
        raw_bytes=raw_file.size_bytes,
        full_markdown_bytes=len(markdown_for(messages).encode("utf-8")),
        prose_markdown_bytes=len(markdown_for(messages, prose_only=True).encode("utf-8")),
        user_markdown_bytes=len(markdown_for(messages, prose_only=True, user_only=True).encode("utf-8")),
        rendered_messages=len(messages),
        prose_messages=len(prose_messages),
        user_messages=len(user_messages),
    )


def _first_json_record(path: Path, *, max_lines: int = 20) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for idx, line in enumerate(handle):
                if idx >= max_lines:
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
    except OSError:
        return None
    return None


def _claude_session_id_from_record(raw: dict[str, Any]) -> str | None:
    for key in ("sessionId", "session_id"):
        value = raw.get(key)
        if isinstance(value, str) and UUID_RE.fullmatch(value):
            return value
    uuid = UUID_RE.search(_json_dumps(raw))
    return uuid.group(0) if uuid else None


def identify_raw_file(path: Path, provider: str) -> RawSessionFile:
    stat = path.stat()
    local_id: str | None = None
    id_source = "none"
    if provider == "codex":
        match = UUID_RE.search(path.stem)
        if match:
            local_id = f"codex:{match.group(0)}"
            id_source = "filename_uuid"
    else:
        match = UUID_RE.fullmatch(path.stem)
        if match:
            local_id = f"claude-code:{match.group(0)}"
            id_source = "filename_uuid"
        elif path.stem.startswith("agent-"):
            record = _first_json_record(path)
            session_id = _claude_session_id_from_record(record or {})
            if session_id:
                local_id = f"claude-code:{session_id}:{path.stem}"
                id_source = "agent_session_id"
    return RawSessionFile(provider=provider, path=path, size_bytes=stat.st_size, local_id=local_id, id_source=id_source)


def iter_raw_session_files(
    *,
    claude_root: Path | None = None,
    codex_root: Path | None = None,
    cfg: LynchpinConfig | None = None,
) -> list[RawSessionFile]:
    cfg = cfg or get_config()
    claude = claude_root or (Path.home() / ".claude" / "projects")
    codex = codex_root or cfg.codex_sessions_root
    rows: list[RawSessionFile] = []
    if claude.exists():
        rows.extend(identify_raw_file(path, "claude-code") for path in sorted(claude.rglob("*.jsonl")) if path.is_file())
    if codex.exists():
        rows.extend(identify_raw_file(path, "codex") for path in sorted(codex.rglob("*.jsonl")) if path.is_file())
    return rows


def read_polylogue_conversation_ids(db_path: Path) -> tuple[dict[str, set[str]], list[str]]:
    notes: list[str] = []
    by_provider: dict[str, set[str]] = defaultdict(set)
    if not db_path.exists():
        return {}, [f"polylogue DB missing: {db_path}"]
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            columns = {row[1] for row in conn.execute("pragma table_info(conversations)").fetchall()}
            if {"conversation_id", "source_name"} <= columns:
                rows = conn.execute("select conversation_id, source_name from conversations").fetchall()
                for conversation_id, source_name in rows:
                    if conversation_id and source_name:
                        by_provider[str(source_name)].add(str(conversation_id))
            else:
                notes.append(f"conversations table lacks conversation_id/source_name columns: {sorted(columns)}")
    except sqlite3.Error as exc:
        notes.append(f"polylogue DB probe failed: {type(exc).__name__}: {exc}")
    return dict(by_provider), notes


def _inventory(files: list[RawSessionFile]) -> dict[str, Any]:
    by_provider: dict[str, dict[str, Any]] = {}
    for provider in sorted({row.provider for row in files}):
        rows = [row for row in files if row.provider == provider]
        id_sources = Counter(row.id_source for row in rows)
        by_provider[provider] = {
            "files": len(rows),
            "nonempty_files": sum(1 for row in rows if row.size_bytes > 0),
            "bytes": sum(row.size_bytes for row in rows),
            "distinct_local_ids": len({row.local_id for row in rows if row.local_id}),
            "id_sources": dict(sorted(id_sources.items())),
        }
    return {
        "total_files": len(files),
        "total_nonempty_files": sum(1 for row in files if row.size_bytes > 0),
        "total_bytes": sum(row.size_bytes for row in files),
        "providers": by_provider,
    }


def _reconciliation(files: list[RawSessionFile], archive_ids: dict[str, set[str]]) -> dict[str, Any]:
    providers = sorted(set(archive_ids) | {row.provider for row in files})
    payload: dict[str, Any] = {}
    for provider in providers:
        local_ids = {row.local_id for row in files if row.provider == provider and row.local_id}
        archived = archive_ids.get(provider, set())
        intersection = local_ids & archived
        local_missing = sorted(local_ids - archived)
        archive_missing = sorted(archived - local_ids)
        provider_files = [row for row in files if row.provider == provider]
        payload[provider] = {
            "raw_files": len(provider_files),
            "raw_files_without_local_id": sum(1 for row in provider_files if not row.local_id),
            "local_distinct_ids": len(local_ids),
            "polylogue_distinct_ids": len(archived),
            "matched_ids": len(intersection),
            "local_not_polylogue": len(local_missing),
            "polylogue_not_local": len(archive_missing),
            "match_ratio_local": _ratio(len(intersection), len(local_ids)),
            "match_ratio_polylogue": _ratio(len(intersection), len(archived)),
            "local_not_polylogue_samples": local_missing[:20],
            "polylogue_not_local_samples": archive_missing[:20],
        }
    return payload


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return round(values[0], 6)
    pos = (len(values) - 1) * pct
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return round(values[lower], 6)
    return round(values[lower] + (values[upper] - values[lower]) * (pos - lower), 6)


def _ratio_summary(measurements: list[RenderMeasurement]) -> dict[str, Any]:
    groups: dict[str, list[RenderMeasurement]] = {"all": measurements}
    for provider in sorted({row.provider for row in measurements}):
        groups[provider] = [row for row in measurements if row.provider == provider]
    summary: dict[str, Any] = {}
    for group, rows in groups.items():
        ratios = [row.ratios() for row in rows]
        ratio_stats: dict[str, Any] = {}
        for name in ("full_raw", "prose_raw", "user_raw", "prose_full", "user_prose"):
            values = [float(ratio[name]) for ratio in ratios if ratio[name] is not None]
            ratio_stats[name] = {
                "p10": _percentile(values, 0.10),
                "median": _percentile(values, 0.50),
                "p90": _percentile(values, 0.90),
            }
        summary[group] = {
            "sessions": len(rows),
            "raw_bytes": sum(row.raw_bytes for row in rows),
            "full_markdown_bytes": sum(row.full_markdown_bytes for row in rows),
            "prose_markdown_bytes": sum(row.prose_markdown_bytes for row in rows),
            "user_markdown_bytes": sum(row.user_markdown_bytes for row in rows),
            "ratios": ratio_stats,
        }
    return summary


def _sample_for_measurement(files: list[RawSessionFile], sample_per_provider: int | None) -> list[RawSessionFile]:
    nonempty = [row for row in files if row.size_bytes > 0]
    if sample_per_provider is None:
        return sorted(nonempty, key=lambda row: (row.provider, row.size_bytes, str(row.path)))
    selected: list[RawSessionFile] = []
    for provider in sorted({row.provider for row in nonempty}):
        rows = sorted((row for row in nonempty if row.provider == provider), key=lambda row: row.size_bytes)
        if len(rows) <= sample_per_provider:
            selected.extend(rows)
            continue
        if sample_per_provider <= 1:
            selected.append(rows[-1])
            continue
        indexes = {
            round(i * (len(rows) - 1) / (sample_per_provider - 1))
            for i in range(sample_per_provider)
        }
        selected.extend(rows[idx] for idx in sorted(indexes))
    return selected


def _measurement_rows(measurements: list[RenderMeasurement], *, limit: int = 25) -> list[dict[str, Any]]:
    rows = sorted(measurements, key=lambda row: row.raw_bytes, reverse=True)[:limit]
    return [
        {
            "provider": row.provider,
            "local_id": row.local_id,
            "path": str(row.path),
            "raw_bytes": row.raw_bytes,
            "full_markdown_bytes": row.full_markdown_bytes,
            "prose_markdown_bytes": row.prose_markdown_bytes,
            "user_markdown_bytes": row.user_markdown_bytes,
            "ratios": row.ratios(),
            "rendered_messages": row.rendered_messages,
            "prose_messages": row.prose_messages,
            "user_messages": row.user_messages,
        }
        for row in rows
    ]


def render_markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Polylogue Archive Shape",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Raw Inventory",
        "",
    ]
    inv = payload["raw_inventory"]
    lines.append(f"- Total raw files: {inv['total_files']:,}")
    lines.append(f"- Nonempty raw files: {inv['total_nonempty_files']:,}")
    lines.append(f"- Raw bytes: {inv['total_bytes']:,}")
    for provider, row in inv["providers"].items():
        lines.append(
            f"- {provider}: {row['files']:,} files, {row['distinct_local_ids']:,} distinct local ids, "
            f"{row['bytes']:,} bytes, id sources {row['id_sources']}"
        )
    lines.extend(["", "## Reconciliation", ""])
    for provider, row in payload["id_reconciliation"].items():
        lines.append(
            f"- {provider}: {row['matched_ids']:,}/{row['local_distinct_ids']:,} local ids matched "
            f"({row['match_ratio_local']}), {row['matched_ids']:,}/{row['polylogue_distinct_ids']:,} "
            f"Polylogue ids matched ({row['match_ratio_polylogue']}); "
            f"{row['raw_files_without_local_id']:,} raw files had no local id"
        )
    lines.extend(["", "## Size Ratios", ""])
    for group, row in payload["ratio_summary"].items():
        ratios = row["ratios"]
        lines.append(
            f"- {group}: {row['sessions']:,} measured; full/raw median {ratios['full_raw']['median']}, "
            f"prose/raw median {ratios['prose_raw']['median']}, user/raw median {ratios['user_raw']['median']}"
        )
    if payload["notes"]:
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines) + "\n"


def build_polylogue_archive_shape(
    *,
    cfg: LynchpinConfig | None = None,
    claude_root: Path | None = None,
    codex_root: Path | None = None,
    polylogue_db: Path | None = None,
    sample_per_provider: int | None = 100,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    raw_files = iter_raw_session_files(claude_root=claude_root, codex_root=codex_root, cfg=cfg)
    db_path = polylogue_db or cfg.polylogue_db
    archive_ids, notes = read_polylogue_conversation_ids(db_path)
    measured_files = _sample_for_measurement(raw_files, sample_per_provider)
    measurements = [measure_raw_file(row) for row in measured_files]
    return {
        "kind": "polylogue_archive_shape",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "claude_root": str(claude_root or (Path.home() / ".claude" / "projects")),
            "codex_root": str(codex_root or cfg.codex_sessions_root),
            "polylogue_db": str(db_path),
            "sample_per_provider": sample_per_provider,
        },
        "raw_inventory": _inventory(raw_files),
        "archive_inventory": {
            "providers": {provider: len(ids) for provider, ids in sorted(archive_ids.items())},
            "total_conversations": sum(len(ids) for ids in archive_ids.values()),
        },
        "id_reconciliation": _reconciliation(raw_files, archive_ids),
        "measurement_scope": {
            "measured_files": len(measurements),
            "available_nonempty_files": sum(1 for row in raw_files if row.size_bytes > 0),
            "sampled": sample_per_provider is not None,
        },
        "ratio_summary": _ratio_summary(measurements),
        "largest_measured_sessions": _measurement_rows(measurements),
        "notes": notes,
    }


def run_polylogue_archive_shape(
    out_file: str | Path,
    *,
    markdown_out: str | Path | None = None,
    sample_per_provider: int | None = 100,
) -> dict[str, Any]:
    payload = build_polylogue_archive_shape(sample_per_provider=sample_per_provider)
    save_json(out_file, payload, sort_keys=True)
    if markdown_out is not None:
        save_text(markdown_out, render_markdown_report(payload))
    return payload
