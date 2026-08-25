"""Production catalog planning and execution.

The audit module still owns source-specific coverage probes.  This module owns
the decision to run them, the typed production plan, handler resolution, and
execution ordering.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any, Iterable, Literal, TYPE_CHECKING

from .catalog import PRODUCT_CATALOG, handler_registry
from .executor import StepContext, validate_step_contract
from .specs import PlanStep, ProductSpec

if TYPE_CHECKING:
    from ..materialization import MaterializationResult

_INCREMENTAL_MAX_CATCHUP_DAYS = 31
_INCREMENTAL_OVERLAP_DAYS: dict[str, int] = {
    "activitywatch": 2,
    "activitywatch_event_index": 2,
    "activity_content": 2,
    "activitywatch_derived": 2,
    "atuin": 2,
    "machine": 2,
    "keylog_analysis": 2,
    "personal_daily_signals": 2,
    "spotify_daily": 2,
    "temporal_signals": 7,
    "sleep_productivity": 7,
    "github_context": 2,
    "webhistory": 7,
    "irc": 7,
}


def _audit():
    from .. import materialization

    return materialization


def _incremental_window(row: Any, *, end: date) -> tuple[date, date] | None:
    if row.first_date is None or row.last_date is None or end <= row.first_date:
        return None
    if end - row.last_date > timedelta(days=_INCREMENTAL_MAX_CATCHUP_DAYS):
        return None
    overlap = _INCREMENTAL_OVERLAP_DAYS.get(row.name, 7)
    return max(row.first_date, min(row.last_date, end - timedelta(days=overlap))), end


def _step(
    spec: ProductSpec,
    *,
    row: Any,
    action: str,
    reason: str,
    window: tuple[date, date] | None,
) -> PlanStep:
    generation = _audit()._dataset_fingerprint(row)
    spec = replace(
        spec,
        input_generation=generation,
        output=replace(spec.output, generation=generation),
    )
    return PlanStep(
        product=spec.product,
        spec=spec,
        dependencies=tuple(sorted(dependency.product for dependency in spec.dependencies)),
        requested_window=window,
        effective_window=window,
        input_generation=generation,
        raw_read_permission=spec.raw_read_permission,
        output=spec.output,
        phase=spec.phase,
        resources=spec.resources,
        window_policy=spec.window_policy,
        action=action,
        reason=reason,
        materialization_hint=_audit().source_contract(spec.product).materialization_hint,
        status=row.status,
    )


def ensure_materialized(
    name: str,
    *,
    window: tuple[date, date] | None = None,
    budget: Literal["inline", "background", "manual"] = "inline",
    force: bool = False,
    cfg: Any | None = None,
) -> "MaterializationResult":
    """Ensure one source/product is materialized enough for a read path.

    Coverage probes remain in the audit module, while this typed production
    boundary owns the decision and closed-registry execution route.
    """

    audit = _audit()
    started = datetime.now(timezone.utc)
    cfg = cfg or audit.get_config()
    contract = audit.source_contract(name)
    before = audit._audit_one(name, cfg=cfg)

    if (
        name == "activity_content"
        and audit._ACTIVITY_CONTENT_MATERIALIZED_THIS_PROCESS
        and not force
    ):
        status = "ready" if before.status == "ready" else "failed"
        return audit._materialization_result(
            before,
            status=status,
            changed=False,
            reason=(
                "activity-content materialization already ran in this process; "
                f"retaining current {before.status} product: {before.reason}"
            ),
            started=started,
            window=window,
        )

    if (
        not force
        and (before.status == "ready" or before.tail_stale)
        and audit._materialized_enough_for_window(
            before,
            window,
            already_refreshed_this_process=name in audit._TAIL_REFRESHED_THIS_PROCESS,
        )
    ):
        return audit._materialization_result(
            before,
            status="ready",
            changed=False,
            reason=before.reason,
            started=started,
            window=window,
        )

    if contract.materialization_mode == "coverage_bound":
        return audit._materialization_result(
            before,
            status="coverage_bound",
            changed=False,
            reason="source coverage is bounded by external exports; Lynchpin cannot extend it locally",
            started=started,
            window=window,
        )

    if contract.materialization_mode == "external":
        return audit._materialization_result(
            before,
            status="manual",
            changed=False,
            reason=contract.materialization_hint,
            started=started,
            window=window,
        )

    if contract.materialization_mode == "live" and name not in PRODUCT_CATALOG:
        status = "ready" if before.status == "ready" else "blocked"
        return audit._materialization_result(
            before,
            status=status,
            changed=False,
            reason=before.reason,
            started=started,
            window=window,
        )

    if name not in PRODUCT_CATALOG:
        return audit._materialization_result(
            before,
            status="blocked",
            changed=False,
            reason="no transparent materializer is defined for this contract",
            started=started,
            window=window,
        )

    if budget == "manual":
        return audit._materialization_result(
            before,
            status="blocked",
            changed=False,
            reason="materialization requires local work but budget is manual",
            started=started,
            window=window,
        )

    try:
        definition = handler_registry().resolve(PRODUCT_CATALOG[name].handler)
        definition.handler(
            StepContext(
                _step(PRODUCT_CATALOG[name], row=before, action="materialize", reason="ensure materialized", window=window),
                {},
                {"window": window},
            )
        )
    except Exception as exc:
        if audit._can_read_stale_github_context(before, window):
            return audit._materialization_result(
                before,
                status="blocked",
                changed=False,
                reason=f"GitHub network refresh failed; existing canonical context product is stale: {exc}",
                started=started,
                window=window,
                diagnostics=(type(exc).__name__, "stale_github_context"),
            )
        return audit._materialization_result(
            before,
            status="failed",
            changed=False,
            reason=str(exc),
            started=started,
            window=window,
            diagnostics=(type(exc).__name__,),
        )

    after = audit._audit_one(name, cfg=cfg)
    if name == "activity_content":
        audit._ACTIVITY_CONTENT_MATERIALIZED_THIS_PROCESS = True
    if after.tail_stale:
        audit._TAIL_REFRESHED_THIS_PROCESS.add(name)
    enough_for_window = audit._materialized_enough_for_window(after, window, just_refreshed=True)
    after_usable = after.status == "ready" or after.tail_stale
    status = "updated" if after_usable and enough_for_window else "failed"
    if not after_usable:
        reason = f"materializer ran but product is still {after.status}: {after.reason}"
    elif not enough_for_window:
        reason = "materializer ran but continuous product still does not cover the requested window"
    else:
        reason = after.reason
    return audit._materialization_result(
        after,
        status=status,
        changed=status == "updated",
        reason=reason,
        started=started,
        window=window,
    )


def plan_materializations(
    *,
    cfg: Any | None = None,
    force: bool = False,
    window: tuple[date, date] | None = None,
    maintenance: bool = False,
    maintenance_end: date | None = None,
) -> list[PlanStep]:
    """Build the deterministic typed production plan."""

    if maintenance and window is not None:
        raise ValueError("maintenance planning and an explicit window are mutually exclusive")
    audit = _audit()
    cfg = cfg or audit.get_config()
    end = maintenance_end or (date.today() + timedelta(days=1))
    steps: list[PlanStep] = []
    for row in audit.audit_materialization(cfg=cfg):
        spec = PRODUCT_CATALOG.get(row.name)
        if spec is None:
            continue
        step_window: tuple[date, date] | None = None
        if force:
            action, reason = "materialize", row.reason
        elif maintenance:
            if row.repair_required:
                action, reason = "check-only", "incremental maintenance requires an explicit full repair before this product is historically verified"
            elif row.status == "ready" and not row.tail_stale:
                action, reason = "skip", "canonical product is ready"
            elif spec.window_policy == "unbounded":
                action, reason = "check-only", "incremental maintenance requires an explicit repair for this unwindowed materializer"
            else:
                step_window = _incremental_window(row, end=end)
                if step_window is None:
                    action = "check-only"
                    reason = "incremental maintenance requires a proven historical product; run explicit repair/backfill"
                else:
                    action = "materialize"
                    reason = f"incremental tail {step_window[0].isoformat()}..{step_window[1].isoformat()}: {row.reason}"
        elif window is not None:
            if audit._materialized_enough_for_window(row, window):
                action, reason = "skip", "canonical product covers requested window"
            else:
                action, reason, step_window = "materialize", row.reason, window
        elif row.status != "ready":
            action, reason = "materialize", row.reason
        else:
            action, reason = "skip", "canonical product is ready"
        if action != "skip":
            steps.append(_step(spec, row=row, action=action, reason=reason, window=step_window))
    if maintenance:
        materialized_windows = [step.effective_window for step in steps if step.action == "materialize" and step.effective_window is not None]
        if materialized_windows:
            graph_tail_start = min(item[0] for item in materialized_windows)
            steps = [
                replace(
                    step,
                    effective_window=(graph_tail_start, step.effective_window[1]),
                    reason=f"{step.reason}; widened to the shared graph tail so downstream keybind attribution reuses this artifact instead of rescanning raw keylog",
                )
                if step.product == "keylog_analysis" and step.action == "materialize" and step.effective_window is not None and step.effective_window[0] > graph_tail_start
                else step
                for step in steps
            ]
    return steps


def materializer_dependency_model(plan: Iterable[PlanStep]) -> tuple[PlanStep, ...]:
    """Return the typed materialization steps in deterministic plan order."""

    return tuple(step for step in sorted(plan, key=lambda item: item.product) if step.action == "materialize")


def materializer_execution_waves(plan: Iterable[PlanStep]) -> tuple[tuple[PlanStep, ...], ...]:
    """Build deterministic dependency and exclusivity waves."""

    pending = {step.product: step for step in plan if step.action == "materialize"}
    completed: set[str] = set()
    waves: list[tuple[PlanStep, ...]] = []
    while pending:
        ready = [step for step in pending.values() if all(dependency not in pending or dependency in completed for dependency in step.dependencies)]
        if not ready:
            raise ValueError(f"materialization dependency cycle or unresolved producer: {', '.join(sorted(pending))}")
        wave: list[PlanStep] = []
        for step in sorted(ready, key=lambda item: item.product):
            occupied = set().union(*(item.resources.exclusive for item in wave))
            if occupied.isdisjoint(step.resources.exclusive):
                wave.append(step)
        if not wave:
            raise ValueError("ready materializers are mutually exclusive")
        waves.append(tuple(wave))
        completed.update(step.product for step in wave)
        for step in wave:
            pending.pop(step.product)
    return tuple(waves)


def run_materialization_plan(
    steps: Iterable[PlanStep],
    *,
    refresh_id: str | None = None,
    window: tuple[date, date] | None = None,
    continue_on_error: bool = False,
) -> list[PlanStep]:
    """Execute typed source steps while preserving receipts and failure policy."""

    audit = _audit()
    registry = handler_registry()
    selected = tuple(step for step in steps if step.action == "materialize")
    refresh_id = refresh_id or f"materialize:{datetime.now(timezone.utc).isoformat()}"
    ran: list[PlanStep] = []
    ran_lock = Lock()

    def run_one(step: PlanStep) -> None:
        definition = registry.resolve(step.spec.handler)
        validate_step_contract(step, definition)
        effective_window = step.effective_window if step.effective_window is not None else window
        started = datetime.now(timezone.utc)
        audit._record_materialization_step(refresh_id, step.product, "started", step.reason, started_at=started)
        try:
            value = definition.handler(StepContext(step, {}, {"refresh_id": refresh_id, "window": effective_window}))
        except Exception as exc:
            audit._record_materialization_step(
                refresh_id,
                step.product,
                "error",
                json.dumps({"error": str(exc), "effective_window": audit._window_payload(effective_window)}, sort_keys=True),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
            )
            if not continue_on_error:
                raise
            return
        row_count = audit._int_or_none(value.get("row_count")) if isinstance(value, dict) else None
        audit._record_materialization_step(
            refresh_id,
            step.product,
            "ok",
            json.dumps({"status": "materialized", "effective_window": audit._window_payload(effective_window)}, sort_keys=True),
            row_count=row_count,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
        )
        if step.product == "activity_content":
            audit._ACTIVITY_CONTENT_MATERIALIZED_THIS_PROCESS = True
        with ran_lock:
            ran.append(step)

    for wave in materializer_execution_waves(selected):
        if len(wave) == 1:
            run_one(wave[0])
        else:
            with ThreadPoolExecutor(max_workers=len(wave), thread_name_prefix="lynchpin-materialize") as executor:
                futures = [executor.submit(copy_context().run, run_one, step) for step in wave]
                for future in futures:
                    future.result()
    return sorted(ran, key=lambda step: step.product)


def run_materializer_by_name(name: str) -> dict[str, Any]:
    specs = PRODUCT_CATALOG
    if name not in specs:
        from ..core.errors import MaterializationError

        raise MaterializationError(name, reason="no transparent materializer is defined for this contract")
    step = _step(specs[name], row=type("Status", (), {"status": "pending"})(), action="materialize", reason="direct materializer invocation", window=None)
    ran = run_materialization_plan((step,))
    return {"status": "materialized", "product": name} if ran else {"status": "failed", "product": name}


__all__ = [
    "ensure_materialized",
    "materializer_dependency_model",
    "materializer_execution_waves",
    "plan_materializations",
    "run_materialization_plan",
    "run_materializer_by_name",
]
