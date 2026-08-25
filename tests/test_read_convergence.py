from __future__ import annotations

import threading
import time
from datetime import date
from pathlib import Path

from lynchpin import materialization


def _row(name: str, *, last: date | None, status: str = "ready") -> materialization.MaterializedDataset:
    return materialization.MaterializedDataset(
        name=name,
        status=status,  # type: ignore[arg-type]
        authority="synthetic fixture",
        query_surface="synthetic fixture",
        materialized_paths=(Path(f"/tmp/{name}.json"),),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 8, 1) if last else None,
        last_date=last,
        materialization_hint="fixture",
        reason="fixture",
    )


def test_warm_read_is_a_noop_after_the_first_durable_check(monkeypatch) -> None:
    calls = 0
    row = _row("activitywatch", last=date(2026, 8, 3))

    def audit(_name: str, *, cfg):
        nonlocal calls
        calls += 1
        return row

    monkeypatch.setattr(materialization, "_audit_one", audit)
    monkeypatch.setattr(materialization, "_materializers", lambda: {})
    window = (date(2026, 8, 1), date(2026, 8, 3))

    first = materialization.ensure_materialized("activitywatch", window=window)
    second = materialization.ensure_materialized("activitywatch", window=window)

    assert first.status == second.status == "ready"
    assert first.changed is second.changed is False
    assert calls == 1


def test_missing_tail_uses_only_the_bounded_tail(monkeypatch) -> None:
    calls: list[tuple[date | None, date | None]] = []
    before = _row("activitywatch", last=date(2026, 8, 3))
    after = _row("activitywatch", last=date(2026, 8, 6))
    audits = iter((before, after))

    def audit(_name: str, *, cfg):
        return next(audits)

    def materialize(*, start=None, end=None):
        calls.append((start, end))

    monkeypatch.setattr(materialization, "_audit_one", audit)
    monkeypatch.setattr(materialization, "_materializers", lambda: {"activitywatch": materialize})
    result = materialization.ensure_materialized(
        "activitywatch", window=(date(2026, 8, 1), date(2026, 8, 6))
    )

    assert result.status == "updated"
    assert calls == [(date(2026, 8, 3), date(2026, 8, 6))]


def test_identical_concurrent_reads_single_flight_the_materializer(monkeypatch) -> None:
    calls = 0
    lock = threading.Lock()
    before = _row("activitywatch_event_index", last=date(2026, 8, 3))
    after = _row("activitywatch_event_index", last=date(2026, 8, 6))
    audits = iter((before, after))

    def audit(_name: str, *, cfg):
        return next(audits)

    def materialize(*, start=None, end=None):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)

    monkeypatch.setattr(materialization, "_audit_one", audit)
    monkeypatch.setattr(
        materialization,
        "_materializers",
        lambda: {"activitywatch_event_index": materialize},
    )
    window = (date(2026, 8, 1), date(2026, 8, 6))
    results = []

    def read() -> None:
        results.append(materialization.ensure_materialized("activitywatch_event_index", window=window))

    threads = [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert len(results) == 4
    assert all(result.status == "updated" for result in results)


def test_pinned_read_metadata_never_enters_convergence(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("pinned reads must not converge")

    monkeypatch.setattr(materialization, "ensure_materialized", fail)
    from lynchpin.mcp.tools._utils import pinned_materialization_for_read

    payload = pinned_materialization_for_read(caller="test.pinned", refresh_id="historical-1")

    assert payload == {
        "name": "evidence_graph_substrate",
        "status": "pinned",
        "changed": False,
        "caller": "test.pinned",
        "refresh_id": "historical-1",
    }
