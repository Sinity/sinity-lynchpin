"""Regression test pinning work_correlation public output to a golden JSON.

The fixture script in ``_work_correlation_fixture.py`` builds a synthetic but
realistic mix of git/github/ai/raw-log/focus/shell evidence plus an evidence
graph, then materializes every public output of
``lynchpin.graph.work_correlation``. This test re-runs that pipeline and
compares against the committed golden JSON. Any change to aggregation
semantics, sort order, or shape will fail this test loudly — exactly the
behavior we want when migrating internals (e.g. swapping Counter/defaultdict
for polars groupbys).

To regenerate the golden after an intentional semantic change, run::

    python tests/graph/_work_correlation_fixture.py > tests/graph/_work_correlation_golden.json
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _run_fixture() -> dict[str, object]:
    fixture_path = Path(__file__).parent / "_work_correlation_fixture.py"
    spec = importlib.util.spec_from_file_location(
        "_work_correlation_fixture", fixture_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_payload()


def test_work_correlation_golden_output_matches() -> None:
    golden_path = Path(__file__).parent / "_work_correlation_golden.json"
    expected = json.loads(golden_path.read_text())
    actual = _run_fixture()
    assert actual == expected, (
        "work_correlation output drifted from golden. "
        "If the change is intentional, regenerate the golden:\n"
        "  python tests/graph/_work_correlation_fixture.py > tests/graph/_work_correlation_golden.json"
    )
