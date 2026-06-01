from __future__ import annotations

from lynchpin.core.io import save_json


def test_benchmark_plans_emit_binding_gap_without_derivations(tmp_path):
    from lynchpin.analysis.machine.benchmark_plans import analyze_machine_benchmark_plans

    candidates = tmp_path / "machine_attribution_candidates.json"
    save_json(candidates, {"candidates": [_candidate()]}, sort_keys=True)

    analysis = analyze_machine_benchmark_plans(candidates_path=candidates)

    assert analysis.plan_count == 1
    assert analysis.ready_plan_count == 0
    plan = analysis.plans[0]
    assert plan.planning_status == "needs_binding"
    assert plan.support_ceiling == "candidate"
    assert "fixed_derivation_set" in plan.required_bindings
    assert plan.manifest_preview["pre_analysis"]["support_ceiling"] == "controlled"
    assert plan.manifest_preview["pre_analysis"]["causal_model"]["outcome_variable"] == "stage.duration_s"
    assert plan.causal_model_assessment["status"] == "passed"
    assert plan.manifest_preview["pre_analysis"]["causal_model_assessment"]["status"] == "passed"
    assert "post_state" in plan.causal_model_assessment["forbidden_post_treatment_variables"]
    assert plan.manifest_preview["pre_analysis"]["instrumentation_bundle"]["name"] == "build_phase"
    assert plan.manifest_preview["pre_analysis"]["power_note"]["repeats_per_cell"] == 3
    assert {row["design_id"] for row in plan.design_variants} >= {
        "blocked_randomization",
        "paired_before_after",
        "latin_square_ordering",
        "factorial",
        "fractional_factorial",
        "sequential",
    }
    assert plan.design_variants[0]["support_ceiling"] == "candidate"
    assert plan.manifest_preview["pre_analysis"]["selected_design_variant"] == "blocked_randomization"
    assert plan.manifest_preview["pre_analysis"]["execution_hygiene_contract"]["clock_policy"]
    assert plan.readiness["controlled"] is False


def test_benchmark_plans_generate_valid_randomized_manifest_with_derivations(tmp_path):
    from lynchpin.analysis.machine.benchmark_plans import analyze_machine_benchmark_plans

    candidates = tmp_path / "machine_attribution_candidates.json"
    save_json(candidates, {"candidates": [_candidate()]}, sort_keys=True)

    analysis = analyze_machine_benchmark_plans(
        candidates_path=candidates,
        derivations=({"name": "sinex-check", "drv_path": "/nix/store/demo.drv"},),
        repeats_per_cell=2,
    )

    plan = analysis.plans[0]
    assert analysis.ready_plan_count == 1
    assert plan.planning_status == "ready"
    assert plan.support_ceiling == "controlled"
    order = plan.manifest_preview["controlled_benchmark"]["randomized_order"]
    assert len(order) == 8
    assert {row["cache_condition"] for row in order} == {"cold", "warm"}
    assert plan.readiness["controlled"] is True
    assert plan.readiness["internal_json_log_format"] == "internal-json"
    assert plan.readiness["internal_json_capture_stream"] == "stderr"
    assert plan.design_variants[0]["support_ceiling"] == "controlled"
    assert plan.manifest_preview["controlled_benchmark"]["internal_json"]["argv_template"] == [
        "nix",
        "build",
        "--log-format",
        "internal-json",
        "{derivation_key}",
    ]
    assert plan.manifest_preview["pre_analysis"]["design_variants"][2]["design_id"] == "latin_square_ordering"
    assert plan.manifest_preview["pre_analysis"]["design_variants"][2]["support_ceiling"] == "controlled"
    fractional = next(row for row in plan.design_variants if row["design_id"] == "fractional_factorial")
    assert fractional["alias_policy"] == "every omitted interaction must be declared before execution"
    sequential = next(row for row in plan.design_variants if row["design_id"] == "sequential")
    assert sequential["interim_policy"].startswith("no peeking")
    assert plan.readiness["randomized_run_count"] == 8
    assert len(plan.run_manifest) == 8
    assert {row["derivation_key"] for row in plan.run_manifest} == {"/nix/store/demo.drv"}
    assert all(row["telemetry_window_id"].endswith(":manifest_timestamps") for row in plan.run_manifest)
    assert all("{run_id}" not in str(row["internal_json_path"]) for row in plan.run_manifest)


def test_benchmark_readiness_rejects_unbalanced_randomized_order():
    from lynchpin.analysis.machine.controlled_benchmarks import benchmark_readiness

    readiness = benchmark_readiness({
        "controlled_benchmark": {
            "run_group_id": "grp",
            "derivations": [{"drv_path": "/nix/store/demo.drv"}],
            "cache_conditions": ["cold", "warm"],
            "assignment_seed": 1,
            "randomized_order": [
                {"run_id": "r1", "treatment_label": "baseline", "cache_condition": "cold"},
                {"run_id": "r2", "treatment_label": "turbo", "cache_condition": "warm"},
            ],
            "control_label": "baseline",
            "treatment_label": "turbo",
            "internal_json": {
                "path": "/tmp/{run_id}.ndjson",
                "log_format": "internal-json",
                "capture_stream": "stderr",
                "argv_template": ["nix", "build", "--log-format", "internal-json", "{derivation_key}"],
            },
            "telemetry": {"window_source": "manifest_timestamps"},
        },
        "pre_analysis": _pre_analysis(),
    })

    assert readiness.controlled is False
    assert any("cache condition cold lacks both" in issue for issue in readiness.issues)


def test_benchmark_readiness_requires_internal_json_capture_contract():
    from lynchpin.analysis.machine.controlled_benchmarks import benchmark_readiness

    manifest = {
        "controlled_benchmark": {
            "run_group_id": "grp",
            "derivations": [{"drv_path": "/nix/store/demo.drv"}],
            "cache_conditions": ["cold", "warm"],
            "assignment_seed": 1,
            "randomized_order": [
                {"run_id": "r1", "treatment_label": "baseline", "cache_condition": "cold"},
                {"run_id": "r2", "treatment_label": "turbo", "cache_condition": "cold"},
                {"run_id": "r3", "treatment_label": "baseline", "cache_condition": "warm"},
                {"run_id": "r4", "treatment_label": "turbo", "cache_condition": "warm"},
            ],
            "control_label": "baseline",
            "treatment_label": "turbo",
            "internal_json": {"path": "/tmp/run.ndjson"},
            "telemetry": {"window_source": "manifest_timestamps"},
        },
        "pre_analysis": _pre_analysis(),
    }

    readiness = benchmark_readiness(manifest)

    assert readiness.controlled is False
    assert "internal_json.log_format must be internal-json" in readiness.issues
    assert "internal_json.capture_stream must be stderr or stdout" in readiness.issues
    assert "internal_json.argv_template must be a list of strings" in readiness.issues


def test_benchmark_readiness_rejects_post_treatment_adjustment():
    from lynchpin.analysis.machine.controlled_benchmarks import benchmark_readiness

    pre_analysis = _pre_analysis()
    pre_analysis["causal_model"] = {
        "treatment_variable": "turbo",
        "outcome_variable": "duration_seconds",
        "adjustment_variables": ["post_state"],
        "forbidden_post_treatment_variables": ["post_state"],
    }
    manifest = {
        "controlled_benchmark": {
            "run_group_id": "grp",
            "derivations": [{"drv_path": "/nix/store/demo.drv"}],
            "cache_conditions": ["cold", "warm"],
            "assignment_seed": 1,
            "randomized_order": [
                {"run_id": "r1", "treatment_label": "baseline", "cache_condition": "cold"},
                {"run_id": "r2", "treatment_label": "turbo", "cache_condition": "cold"},
                {"run_id": "r3", "treatment_label": "baseline", "cache_condition": "warm"},
                {"run_id": "r4", "treatment_label": "turbo", "cache_condition": "warm"},
            ],
            "control_label": "baseline",
            "treatment_label": "turbo",
            "internal_json": {
                "path": "/tmp/{run_id}.ndjson",
                "log_format": "internal-json",
                "capture_stream": "stderr",
                "argv_template": ["nix", "build", "--log-format", "internal-json", "{derivation_key}"],
            },
            "telemetry": {"window_source": "manifest_timestamps"},
        },
        "pre_analysis": pre_analysis,
    }

    readiness = benchmark_readiness(manifest)

    assert readiness.controlled is False
    assert any("forbidden post-treatment variables: post_state" in issue for issue in readiness.issues)


def test_benchmark_plans_bind_derivations_from_inventory(tmp_path):
    from lynchpin.analysis.machine.benchmark_plans import analyze_machine_benchmark_plans

    candidates = tmp_path / "machine_attribution_candidates.json"
    inventory = tmp_path / "machine_derivation_inventory.json"
    save_json(candidates, {"candidates": [{**_candidate(), "project": "sinex"}]}, sort_keys=True)
    save_json(
        inventory,
        {
            "targets": [
                {"project": "sinex", "attr": "sinexd", "drv_path": "/nix/store/sinexd.drv", "eval_status": "ready"},
                {"project": "sinex", "attr": "xtask", "drv_path": "/nix/store/xtask.drv", "eval_status": "ready"},
                {
                    "project": "sinity-lynchpin",
                    "attr": "lynchpin",
                    "drv_path": "/nix/store/lynchpin.drv",
                    "eval_status": "ready",
                },
            ]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_benchmark_plans(
        candidates_path=candidates,
        derivation_inventory_path=inventory,
        repeats_per_cell=1,
    )

    plan = analysis.plans[0]
    assert analysis.ready_plan_count == 1
    assert plan.required_bindings == ()
    assert plan.manifest_preview["controlled_benchmark"]["derivations"] == [{
        "project": "sinex",
        "name": "xtask",
        "drv_path": "/nix/store/xtask.drv",
        "store_path": None,
        "flake_ref": None,
    }]
    assert {row["derivation_key"] for row in plan.run_manifest} == {"/nix/store/xtask.drv"}


def _candidate() -> dict:
    return {
        "candidate_id": "cand1",
        "metric": "stage.duration_s",
        "suspected_factor": "cohort_contrast:stage=test",
        "priority_score": 10.0,
        "score_components": {"effect_size": 4.0},
        "source_artifacts": ["machine_comparisons.json"],
        "source_ids": ["contrast1"],
    }


def _pre_analysis() -> dict:
    return {
        "research_question": "Does turbo change duration?",
        "hypothesis": "turbo affects duration",
        "estimand": "mean delta",
        "unit": "run",
        "primary_metric": "duration_seconds",
        "inclusion_rules": ["successful command exit"],
        "exclusion_rules": ["missing internal-json"],
        "blocking_keys": ["cache_condition", "derivation"],
        "support_ceiling": "controlled",
        "causal_model": {"treatment_variable": "turbo", "outcome_variable": "duration_seconds"},
        "instrumentation_bundle": {"name": "build_phase"},
        "power_note": {"status": "fixture"},
    }
