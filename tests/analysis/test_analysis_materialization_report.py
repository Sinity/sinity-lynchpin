from __future__ import annotations

from lynchpin.analysis.core.dag import StepResult, StepStatus


def test_materialization_report_extracts_counts_source_statuses_and_reasons() -> None:
    from lynchpin.analysis.core.materialization_report import (
        materialization_report_payload,
    )

    payload = materialization_report_payload(
        dag_name="machine-analysis-materialization",
        up_to="machine_analysis_readiness",
        results=[
            StepResult(
                name="machine_analysis_substrate_promote",
                status=StepStatus.SUCCESS,
                elapsed_seconds=1.5,
                result={
                    "counts": {"machine_metric_sample": 10},
                    "source_statuses": [
                        {
                            "source": "machine",
                            "kind": "capture",
                            "status": "ok",
                            "reason": None,
                            "row_count": 10,
                        },
                        {
                            "source": "polylogue",
                            "kind": "export",
                            "status": "unavailable",
                            "reason": "excluded",
                            "row_count": 0,
                        },
                    ],
                },
            ),
            StepResult(
                name="machine_support_assessment",
                status=StepStatus.SKIPPED,
                error="Skipped due to failed dependency: x",
            ),
        ],
    )

    assert payload["dag_name"] == "machine-analysis-materialization"
    assert payload["by_status"] == {"skipped": 1, "success": 1}
    assert payload["steps"][0]["row_counts"] == {"machine_metric_sample": 10}
    assert payload["steps"][0]["degraded_reasons"] == ["excluded"]
    assert payload["steps"][1]["degraded_reasons"] == ["Skipped due to failed dependency: x"]
    assert payload["source_statuses"][1]["source"] == "polylogue"
