"""Historical service observations for currently declared capture producers."""
from __future__ import annotations

from contextlib import closing, ExitStack
from datetime import datetime, timezone
import hashlib
import heapq
import json
import sqlite3
import time
from typing import Any

from lynchpin.core.config import get_config
from lynchpin.core.errors import LynchpinError, SourceUnavailableError
from lynchpin.sources.machine_models import MachineServiceState
from lynchpin.sources.machine_schema import validate_service_state_schema
from lynchpin.sources.machine_sqlite import acquire_machine_read_lock, as_utc, connect_readonly
from lynchpin.sources.machine_segments import resolve_machine_sources
from lynchpin.sources.service_health import MAX_OBSERVATION_GAP, _sweep_unit
from lynchpin.sources.sinnix_runtime_inventory import read_inventory


def capture_service_evidence(*, start: datetime, end: datetime, cfg: Any = None) -> dict[str, Any]:
    """Stream bounded service evidence; current bindings do not prove past ownership."""
    cfg = cfg if cfg is not None else get_config()
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("service evidence requires a nonempty aware datetime window")
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    result: dict[str, Any] = {"status":"unavailable", "window_start":start.isoformat(),
        "window_end":end.isoformat(), "basis":"service telemetry under current producer declarations",
        "interpretation":"inactivity is not proof of missing captures; unobserved time is unknown", "captures":[]}
    try:
        inventory = read_inventory(cfg.sinnix_runtime_inventory_json)
        result["binding_sha256"] = hashlib.sha256(json.dumps({"hostname":inventory.hostname,"captures":inventory.captures},sort_keys=True).encode()).hexdigest()
        if not inventory.hostname:
            raise ValueError("inventory hostname is missing")
        sources = resolve_machine_sources(cfg.machine_telemetry_db)
        if not sources:
            raise SourceUnavailableError("machine", reason="no telemetry SQLite source")
        with ExitStack() as stack:
            held = acquire_machine_read_lock(sources.current, required=sources.manifest is not None)
            if held is not None:
                stack.enter_context(closing(held))
            deadline = time.monotonic()+3.0
            connections = []
            for segment in sources.segments:
                segment.validate()
                conn = stack.enter_context(closing(connect_readonly(segment.path)))
                conn.execute("BEGIN")
                validate_service_state_schema(conn)
                conn.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
                connections.append(conn)
            cache: dict[tuple[str,str],dict[str,Any]] = {}
            for capture in inventory.captures:
                producer = capture.get("producer")
                record: dict[str,Any] = {"name":capture["name"],"producer":producer}
                if not isinstance(producer,dict) or not isinstance(producer.get("unit"),str) or not producer.get("unit") or producer.get("manager") not in {"system","user"}:
                    result["captures"].append({**record,"status":"unbound","reason":"no declared service binding"})
                    continue
                key=(producer["unit"],producer["manager"])
                if key not in cache:
                    cache[key]=_read_service(tuple(connections),host=inventory.hostname,unit=key[0],scope=key[1],start=start,end=end,deadline=deadline)
                result["captures"].append({**record,**cache[key]})
        result["status"]="available" if all(row["status"] in {"observed","unbound"} for row in result["captures"]) else "partial"
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,RuntimeError,LynchpinError) as exc:
        result["reason"]=str(exc)
    return result


def _read_service(conn:sqlite3.Connection | tuple[sqlite3.Connection, ...],*,host:str,unit:str,scope:str,start:datetime,end:datetime,deadline:float)->dict[str,Any]:
    sql="SELECT observed_at,host,boot_id,unit,scope,active_state,sub_state FROM service_state WHERE unit=? AND observed_at>=? AND observed_at<? AND host=? AND scope=? ORDER BY observed_at"
    args=[unit,(start-MAX_OBSERVATION_GAP).isoformat(),end.isoformat(),host,scope]
    unknown={"status":"unavailable","inactive_seconds":None,"unobserved_seconds":None,"intervals":[]}
    try:
        if time.monotonic()>=deadline:
            return {**unknown,"reason":"service evidence budget exhausted"}
        connections = conn if isinstance(conn, tuple) else (conn,)
        for connection in connections:
            with closing(connection.execute("EXPLAIN QUERY PLAN "+sql,args)) as cursor:
                plan=cursor.fetchall()
            if not any("SEARCH" in row[3] and "observed_at" in row[3] for row in plan):
                return {**unknown,"reason":"no indexed service time-range read"}
        def decoded(cursor):
            for raw in cursor:
                observed=as_utc(raw[0])
                if observed is None:
                    raise ValueError("invalid service observation timestamp")
                yield MachineServiceState(observed,*raw[1:])
        def rows():
            with ExitStack() as stack:
                streams = [decoded(stack.enter_context(closing(connection.execute(sql,args)))) for connection in connections]
                yield from heapq.merge(*streams, key=lambda row: row.observed_at)
        totals={"inactive":0.0,"unobserved":0.0}
        intervals=[]
        interval_count=0
        with closing(rows()) as observations:
            for interval in _sweep_unit(unit,observations,start,end):
                totals[interval.kind]+=(interval.end-interval.start).total_seconds()
                interval_count+=1
                if len(intervals)<100:
                    intervals.append({"kind":interval.kind,"start":interval.start.isoformat(),"end":interval.end.isoformat(),"states":list(interval.observed_states)})
        return {"status":"observed","host":host,"scope":scope,
            "inactive_seconds":totals["inactive"],"unobserved_seconds":totals["unobserved"],
            "intervals":intervals,"interval_count":interval_count,"intervals_truncated":interval_count>len(intervals)}
    except (sqlite3.Error,ValueError) as exc:
        return {**unknown,"reason":str(exc)}


def render_capture_service_evidence(evidence:dict[str,Any])->str:
    lines=["Historical producer-service evidence (current declarations; inactivity is not proof of missing captures)."]
    if evidence.get("status")=="unavailable":
        lines.append("Unavailable: "+str(evidence.get("reason","unknown")))
    else:
        lines += ["", "| Capture | Service evidence | Inactive seconds | Unobserved seconds |", "|---|---|---:|---:|"]
        for row in evidence.get("captures",[]):
            inactive=row.get("inactive_seconds")
            unobserved=row.get("unobserved_seconds")
            name=str(row["name"]).replace("|","\\|")
            lines.append(f"| {name} | {row['status']} | {inactive if inactive is not None else 'unknown'} | {unobserved if unobserved is not None else 'unknown'} |")
    return "\n".join(lines)
