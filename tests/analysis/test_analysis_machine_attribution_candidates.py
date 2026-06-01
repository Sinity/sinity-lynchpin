from __future__ import annotations


def test_machine_attribution_candidates_rank_observational_patterns(tmp_path):
    from lynchpin.analysis.machine.attribution_candidates import (
        analyze_machine_attribution_candidates,
    )

    deltas = tmp_path / "machine_observational_deltas.json"
    work = tmp_path / "machine_work_observations.json"
    deltas.write_text(
        """
        {
          "deltas": [
            {
              "tool": "pytest",
              "work_state": "test_workload",
              "pressure_state": "io_pressure",
              "pressure_count": 3,
              "median_delta_seconds": 4.0,
              "p95_delta_seconds": 8.0,
              "caveats": ["observational"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    work.write_text(
        """
        {
          "stage_summaries": [
            {
              "stage_name": "nextest",
              "observation_count": 2,
              "p95_duration_s": 30.0,
              "max_duration_s": 40.0
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    analysis = analyze_machine_attribution_candidates(
        deltas_path=deltas,
        work_observations_path=work,
        mining_path=tmp_path / "missing-machine-mining.json",
    )

    assert analysis.candidate_count == 2
    assert analysis.pareto_frontier_count >= 1
    assert analysis.pareto_frontier_ids
    assert analysis.candidates[0].metric == "xtask.stage.nextest.duration_s"
    assert analysis.candidates[0].support_ceiling == "candidate"
    assert analysis.candidates[0].score_components["recurrence"] == 2.0
    assert analysis.candidates[0].validation_status == "work_summary_only"
    assert analysis.candidates[0].rank_within_scan == 1
    assert isinstance(analysis.candidates[0].pareto_frontier, bool)
    assert analysis.candidates[0].suggested_benchmark_manifest["controlled_benchmark"]["cache_conditions"] == ["cold", "warm"]
    internal_json = analysis.candidates[0].suggested_benchmark_manifest["controlled_benchmark"]["internal_json"]
    assert internal_json["log_format"] == "internal-json"
    assert internal_json["capture_stream"] == "stderr"
    assert analysis.candidates[1].suspected_factor == "machine_pressure_state=io_pressure"
    assert "no causal claim" in analysis.caveats[0]


def test_machine_attribution_candidates_include_matched_designs(tmp_path):
    from lynchpin.analysis.machine.attribution_candidates import (
        analyze_machine_attribution_candidates,
    )

    empty_deltas = tmp_path / "machine_observational_deltas.json"
    empty_work = tmp_path / "machine_work_observations.json"
    matched = tmp_path / "machine_matched_designs.json"
    empty_deltas.write_text('{"deltas":[]}', encoding="utf-8")
    empty_work.write_text('{"stage_summaries":[]}', encoding="utf-8")
    matched.write_text(
        """
        {
          "design_count": 1,
          "designs": [
            {
              "design_id": "design1",
              "boundary_id": "boundary1",
              "project": "sinex",
              "stage_name": "test",
              "outcome_metric": "stage.duration_s",
              "treated_before_n": 4,
              "treated_after_n": 4,
              "control_before_n": 4,
              "control_after_n": 4,
              "difference_in_differences": 12.5,
              "placebo_delta": 0.5,
              "identification_status": "design_ready",
              "negative_control_status": "passed",
              "support_ceiling": "natural_experiment_design",
              "caveats": ["not randomized"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    analysis = analyze_machine_attribution_candidates(
        deltas_path=empty_deltas,
        work_observations_path=empty_work,
        mining_path=tmp_path / "missing-machine-mining.json",
        comparisons_path=tmp_path / "missing-machine-comparisons.json",
        matched_designs_path=matched,
    )

    assert analysis.candidate_count == 1
    candidate = analysis.candidates[0]
    assert candidate.mechanism_family == "natural_experiment_boundary"
    assert candidate.project == "sinex"
    assert candidate.support_ceiling == "natural_experiment_design"
    assert candidate.source_artifacts == ("machine_matched_designs.json",)
    assert candidate.score_components["validation_strength"] == 2.5
    assert candidate.validation_status == "design_ready"


def test_machine_attribution_candidates_include_lagged_and_anomaly_mining(tmp_path):
    from lynchpin.analysis.machine.attribution_candidates import (
        analyze_machine_attribution_candidates,
    )

    empty_deltas = tmp_path / "machine_observational_deltas.json"
    empty_work = tmp_path / "machine_work_observations.json"
    mining = tmp_path / "machine_mining.json"
    empty_deltas.write_text('{"deltas":[]}', encoding="utf-8")
    empty_work.write_text('{"stage_summaries":[]}', encoding="utf-8")
    mining.write_text(
        """
        {
          "lagged_exposure_count": 1,
          "lagged_exposures": [
            {
              "summary_id": "lag1",
              "dimensions": {"stage_name": "test", "project": "sinex"},
              "pressure_metric": "host_io_pressure_some_avg10_max",
              "paired_count": 4,
              "high_prior_pressure_count": 2,
              "median_delta": 3.5,
              "caveats": ["exploratory"]
            }
          ],
          "anomaly_cluster_count": 1,
          "anomaly_clusters": [
            {
              "cluster_id": "cluster1",
              "dimensions": {"stage_name": "build", "project": "sinex"},
              "anomaly_count": 3,
              "max_outcome": 42.0,
              "pressure_signature": ["host_cpu_pressure_some_avg10_max"],
              "caveats": ["tail only"]
            }
          ],
          "cohorts": []
        }
        """,
        encoding="utf-8",
    )

    analysis = analyze_machine_attribution_candidates(
        deltas_path=empty_deltas,
        work_observations_path=empty_work,
        mining_path=mining,
        comparisons_path=tmp_path / "missing-machine-comparisons.json",
        matched_designs_path=tmp_path / "missing-machine-matched.json",
    )

    families = {candidate.mechanism_family for candidate in analysis.candidates}
    assert "lagged_pressure_exposure" in families
    assert "machine_context_anomaly_cluster" in families
    assert any(candidate.source_ids == ("lag1",) for candidate in analysis.candidates)
    assert any(candidate.source_ids == ("cluster1",) for candidate in analysis.candidates)
    lagged = next(candidate for candidate in analysis.candidates if candidate.source_ids == ("lag1",))
    assert lagged.validation_status == "temporal_precedence_screen"
    assert lagged.mining_scan_id is None


def test_machine_attribution_candidates_include_slow_tests_and_failures(tmp_path):
    from lynchpin.analysis.machine.attribution_candidates import (
        analyze_machine_attribution_candidates,
    )

    empty_deltas = tmp_path / "machine_observational_deltas.json"
    work = tmp_path / "machine_work_observations.json"
    empty_deltas.write_text('{"deltas":[]}', encoding="utf-8")
    work.write_text(
        """
        {
          "stage_summaries": [],
          "test_summaries": [
            {
              "package": "sinex-primitives",
              "status": "pass",
              "test_count": 5,
              "p95_duration_s": 7.5,
              "max_duration_s": 12.0
            }
          ],
          "failure_summaries": [
            {
              "failure_kind": "test",
              "project": "sinex",
              "package": "sinex-primitives",
              "stage_name": null,
              "status": "fail",
              "failure_type": "assertion",
              "exit_code": null,
              "failure_count": 3,
              "affected_invocation_count": 2,
              "median_duration_s": 1.0,
              "max_duration_s": 4.0
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    analysis = analyze_machine_attribution_candidates(
        deltas_path=empty_deltas,
        work_observations_path=work,
        mining_path=tmp_path / "missing-machine-mining.json",
        comparisons_path=tmp_path / "missing-machine-comparisons.json",
        matched_designs_path=tmp_path / "missing-machine-matched.json",
    )

    families = {candidate.mechanism_family for candidate in analysis.candidates}
    assert "test_package_tail_latency" in families
    assert "test_failure_concentration" in families
    assert any(
        candidate.source_ids == ("machine-work-test-summary:sinex-primitives:pass",)
        for candidate in analysis.candidates
    )
    assert any(
        candidate.source_ids == ("machine-work-failure-summary:test:sinex-primitives:fail:assertion",)
        for candidate in analysis.candidates
    )


def test_machine_attribution_candidates_preserve_natural_designs_across_score_scales(tmp_path):
    from lynchpin.analysis.machine.attribution_candidates import (
        analyze_machine_attribution_candidates,
    )

    deltas = tmp_path / "machine_observational_deltas.json"
    work = tmp_path / "machine_work_observations.json"
    matched = tmp_path / "machine_matched_designs.json"
    deltas.write_text('{"deltas":[]}', encoding="utf-8")
    work.write_text(
        """
        {
          "stage_summaries": [
            {
              "stage_name": "compile",
              "observation_count": 100000,
              "p95_duration_s": 100000.0,
              "max_duration_s": 200000.0
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    matched.write_text(
        """
        {
          "design_count": 1,
          "designs": [
            {
              "design_id": "design1",
              "boundary_id": "boundary1",
              "project": "sinex",
              "stage_name": "test",
              "outcome_metric": "stage.duration_s",
              "treated_before_n": 4,
              "treated_after_n": 4,
              "control_before_n": 4,
              "control_after_n": 4,
              "difference_in_differences": 2.0,
              "placebo_delta": 0.1,
              "identification_status": "design_ready",
              "negative_control_status": "passed",
              "support_ceiling": "natural_experiment_design",
              "caveats": ["not randomized"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    analysis = analyze_machine_attribution_candidates(
        deltas_path=deltas,
        work_observations_path=work,
        mining_path=tmp_path / "missing-machine-mining.json",
        comparisons_path=tmp_path / "missing-machine-comparisons.json",
        matched_designs_path=matched,
        limit=1,
    )

    assert analysis.candidate_count == 1
    assert analysis.candidates[0].support_ceiling == "natural_experiment_design"


def test_machine_attribution_candidates_preserve_family_frontier(tmp_path):
    from lynchpin.analysis.machine.attribution_candidates import (
        analyze_machine_attribution_candidates,
    )

    deltas = tmp_path / "machine_observational_deltas.json"
    work = tmp_path / "machine_work_observations.json"
    mining = tmp_path / "machine_mining.json"
    deltas.write_text('{"deltas":[]}', encoding="utf-8")
    work.write_text(
        """
        {
          "stage_summaries": [
            {"stage_name": "compile", "observation_count": 1000, "p95_duration_s": 1000.0, "max_duration_s": 2000.0}
          ]
        }
        """,
        encoding="utf-8",
    )
    mining.write_text(
        """
        {
          "lagged_exposure_count": 1,
          "lagged_exposures": [
            {
              "summary_id": "lag1",
              "dimensions": {"stage_name": "test", "project": "sinex"},
              "pressure_metric": "host_io_pressure_some_avg10_max",
              "paired_count": 2,
              "high_prior_pressure_count": 1,
              "median_delta": 1.0
            }
          ],
          "cohorts": []
        }
        """,
        encoding="utf-8",
    )

    analysis = analyze_machine_attribution_candidates(
        deltas_path=deltas,
        work_observations_path=work,
        mining_path=mining,
        comparisons_path=tmp_path / "missing-machine-comparisons.json",
        matched_designs_path=tmp_path / "missing-machine-matched.json",
        limit=2,
    )

    assert {candidate.mechanism_family for candidate in analysis.candidates} == {
        "lagged_pressure_exposure",
        "stage_regression_or_contention",
    }
