from __future__ import annotations

import threading
import time
from datetime import date
from pathlib import Path

from lynchpin import materialization


def _result(name: str, *, changed: bool = False) -> materialization.MaterializationResult:
    return materialization.MaterializationResult(
        name=name,
        status="updated" if changed else "ready",
        changed=changed,
        reason="fixture",
        elapsed_ms=1,
        product_paths=(),
        source_high_water={},
        coverage={},
    )


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

    def ensure(_name: str, **_kwargs):
        nonlocal calls
        calls += 1
        return _result("activitywatch")

    materialization._READ_CONVERGENCE_CACHE.clear()
    monkeypatch.setattr(materialization, "_ensure_materialized_typed", ensure)
    window = (date(2026, 8, 1), date(2026, 8, 3))

    first = materialization.ensure_materialized("activitywatch", window=window)
    second = materialization.ensure_materialized("activitywatch", window=window)

    assert first.status == second.status == "ready"
    assert first.changed is second.changed is False
    assert calls == 1


def test_missing_tail_uses_only_the_bounded_tail(monkeypatch) -> None:
    before = _row("activitywatch", last=date(2026, 8, 3))
    monkeypatch.setattr(materialization, "_audit_one", lambda *_args, **_kwargs: before)

    plan = materialization.plan_read_convergence(
        product="activitywatch",
        window=(date(2026, 8, 1), date(2026, 8, 6)),
    )

    assert plan.action == "converge"
    assert plan.effective_window == (date(2026, 8, 3), date(2026, 8, 6))


def test_identical_concurrent_reads_single_flight_the_materializer(monkeypatch) -> None:
    calls = 0
    lock = threading.Lock()
    def ensure(name: str, **_kwargs):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return _result(name, changed=True)

    materialization._READ_CONVERGENCE_CACHE.clear()
    monkeypatch.setattr(materialization, "_ensure_materialized_typed", ensure)
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
