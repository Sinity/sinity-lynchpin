"""Lightweight DAG orchestration for Lynchpin pipelines.

Provides a simple dependency-aware execution engine so that multi-step
flows (baseline → calendar → narrative) can be expressed declaratively
and run with parallel or sequential strategies.

Example::

    from lynchpin.orchestration import DAG, Step

    dag = DAG("daily-refresh")
    dag.add(Step("baseline", baseline_fn))
    dag.add(Step("calendar", calendar_fn, depends_on=["baseline"]))
    dag.add(Step("narrative", narrative_fn, depends_on=["calendar"]))
    results = dag.run()
"""

from .dag import DAG, Step, StepResult, StepStatus

__all__ = ["DAG", "Step", "StepResult", "StepStatus"]
