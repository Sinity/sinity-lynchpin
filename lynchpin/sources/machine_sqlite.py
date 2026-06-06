"""SQLite helpers for live machine telemetry reads."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .machine_models import MachineTelemetrySchemaError
from .machine_schema import (
    validate_block_device_schema,
    validate_gpu_schema,
    validate_metric_schema,
    validate_network_schema,
    validate_service_state_schema,
    validate_service_cgroup_io_schema,
    validate_service_cgroup_pressure_schema,
)


def as_utc(value: str) -> datetime | None:
    try:
        text = value.strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def default_route_interface() -> str | None:
    route = Path("/proc/net/route")
    try:
        for line in route.read_text().splitlines()[1:]:
            fields = line.split()
            if (
                len(fields) >= 4
                and fields[1] == "00000000"
                and int(fields[3], 16) & 0x2
            ):
                return fields[0]
    except (OSError, ValueError):
        return None
    return None


def count_sqlite_rows(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    try:
        with connect_readonly(path) as conn:
            if table == "metric_sample":
                validate_metric_schema(conn)
            elif table == "service_state":
                validate_service_state_schema(conn)
            elif table == "network_sample":
                validate_network_schema(conn)
            elif table == "gpu_sample":
                validate_gpu_schema(conn)
            elif table == "block_device_sample":
                validate_block_device_schema(conn)
            elif table == "service_cgroup_io_sample":
                validate_service_cgroup_io_schema(conn)
            elif table == "service_cgroup_pressure_sample":
                validate_service_cgroup_pressure_schema(conn)
            if table == "network_sample":
                default_interface = default_route_interface()
                if default_interface:
                    return int(
                        conn.execute(
                            "SELECT COUNT(*) FROM network_sample WHERE interface = ?",
                            [default_interface],
                        ).fetchone()[0]
                    )
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except (sqlite3.Error, MachineTelemetrySchemaError):
        return 0


def json_obj(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {"_parse_error": True}
    return parsed if isinstance(parsed, dict) else {"value": parsed}
