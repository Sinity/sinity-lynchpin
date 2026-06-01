from __future__ import annotations


def test_machine_calibration_fixtures_cover_required_guardrails() -> None:
    from lynchpin.analysis.machine.calibration import analyze_machine_calibration

    report = analyze_machine_calibration()

    assert report.fixture_count == 8
    assert report.by_status == {"passed": 8}
    kinds = {row.fixture_kind for row in report.fixtures}
    assert kinds == {
        "null",
        "known_effect",
        "broad_scan_null",
        "confounded",
        "leakage",
        "broken_design",
        "placebo",
        "missingness",
    }
    broken = next(row for row in report.fixtures if row.fixture_kind == "broken_design")
    assert broken.evidence["readiness"]["controlled"] is False
    assert broken.evidence["readiness"]["issues"]
