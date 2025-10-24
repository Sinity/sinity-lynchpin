"""CLI entrypoint for running modularized analyses.

Individual subcommands remain useful for debugging and one-off runs. Generated
markdown summaries from the map commands default to the configured
knowledgebase-backed analysis artefact root, not tracked docs.
"""

from __future__ import annotations

import os
import sys
import json
from datetime import date, datetime
from pathlib import Path

import typer

from .core import canonical as canonical_module
from .core import commit_facts as core_commit_facts
from .core.materialization_report import write_materialization_report
from .core.materialization_intelligence import (
    analysis_materialization_policies,
    executable_steps,
    machine_materialization_policies,
    materialization_plan_for_dag,
    render_materialization_plan,
)
from lynchpin.ingest.materialization_status import (
    compact_materialization_status,
    diagnostic_ledger_status_payload,
)
from lynchpin.core.freshness import (
    freshness_dependencies,
    freshness_explain_target,
    latest_receipts,
)
from lynchpin.core.config import get_config
from lynchpin.core.io import resolve_analysis_path
from .claim_calibration import write_claim_calibration
from .code_history_claims import write_code_history_claims
from .ecosystem import cli as ecosystem_cli
from .google_takeout_mining import write_google_takeout_retrospective
from .keylog import write_keylog_analysis
from .knowledge import cli as knowledge_cli
from .maps import cli as maps_cli
from .machine.attribution import export_below_windows_for_pressure_episodes, write_below_attribution_analysis
from .machine.attribution_candidates import write_machine_attribution_candidates
from .machine.baselines import write_machine_observational_baselines
from .machine.below import export_live_below_window, write_below_analysis
from .machine.benchmark_manifest_bundle import (
    analyze_machine_benchmark_manifest_bundle,
    export_machine_benchmark_manifest_bundle,
    write_machine_benchmark_manifest_bundle,
)
from .machine.benchmark_execution_handoff import write_machine_benchmark_execution_handoff
from .machine.benchmark_execution import write_selected_benchmark_execution
from .machine.benchmark_plans import write_machine_benchmark_plans
from .machine.benchmark_preflight import write_machine_benchmark_preflight
from .machine.command_performance import write_command_performance_analysis
from .machine.devshell import write_devshell_performance_analysis
from .machine.context import write_machine_context_analysis
from .machine.episodes import write_machine_episode_analysis
from .machine.experiment_manifest_diagnostics import write_machine_experiment_manifest_diagnostics
from .machine.experiments import write_machine_experiment_claims
from .machine.observational import write_observational_command_deltas
from .machine.readiness import DEFAULT_BIOS_BOUNDARY, write_machine_analysis_readiness
from .machine.states import write_machine_work_state_analysis
from .machine.status import machine_status_payload
from .machine.support_assessment import write_machine_support_assessment
from .machine.telemetry import write_machine_telemetry_analysis
from .active.substrate_promote import run_substrate_promote
from .active.substrate_promote_status import SOURCE_MACHINE, SOURCE_MACHINE_EXPERIMENTS
from .machine.work_observations import write_work_observation_analysis
from .personal_interest_fusion import write_personal_interest_trace
from .projects import cli as projects_cli
from .materialize import _rolling_window, current_state_dag, machine_analysis_dag
from .sinex import cli as sinex_cli
from .workflow_mechanics import write_workflow_mechanics_report

ANALYSIS_SPEC = os.path.join(os.path.dirname(__file__), 'analysis_spec.json')


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid date: {value!r}") from exc


def _opt_date(value: str | None) -> date | None:
    return _parse_date(value) if value else None


def build_app() -> typer.Typer:
    app = typer.Typer(
        help="Codebase analysis suite CLI",
        no_args_is_help=True,
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    sinex_cli.register_commands(app)
    ecosystem_cli.register_commands(app, analysis_spec=ANALYSIS_SPEC)
    projects_cli.register_commands(app)
    knowledge_cli.register_commands(app)
    maps_cli.register_commands(app, analysis_spec=ANALYSIS_SPEC)

    _register_canonical(app)
    _register_status(app)
    _register_commit(app)
    _register_machine(app)
    _register_misc(app)

    return app


def _register_canonical(app: typer.Typer) -> None:
    @app.command("analysis-snapshot", help="Build canonical analysis snapshot")
    def _analysis_snapshot(
        spec: str = typer.Option(ANALYSIS_SPEC, "--spec"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path('analysis_snapshot.json')
        canonical_module.build_analysis_snapshot(spec, target)

    @app.command("analysis-validate", help="Validate canonical artifacts + invariants")
    def _analysis_validate(
        spec: str = typer.Option(ANALYSIS_SPEC, "--spec"),
    ) -> None:
        issues = canonical_module.validate_analysis_artifacts(spec)
        if issues:
            print('Validation FAILED:')
            for issue in issues:
                print(f'  - {issue}')
            raise typer.Exit(code=1)
        print('Validation OK')


def _register_status(app: typer.Typer) -> None:
    @app.command("status", help="Print compact materialization-first Lynchpin status")
    def _status(
        json_output: bool = typer.Option(False, "--json", help="emit the full compact status payload as JSON"),
    ) -> None:
        payload = compact_materialization_status()
        if json_output:
            print(json.dumps(payload, sort_keys=True))
            return
        materialization = payload.get("materialization") or {}
        print(
            "lynchpin-status: "
            f"{payload.get('health')} "
            f"materialization={materialization.get('status')}"
        )

    @app.command("analysis-status", help="Build machine-readable analysis status dashboard")
    def _analysis_status(
        out: str | None = typer.Option(None, "--out"),
        spec: str = typer.Option(ANALYSIS_SPEC, "--spec"),
    ) -> None:
        from .core import status as analysis_status

        target = out or resolve_analysis_path('analysis_status.json')
        analysis_status.run_analysis_status(target, spec_path=spec)


def _register_commit(app: typer.Typer) -> None:
    @app.command("commit-facts", help="Build canonical commit fact table (raw transport only)")
    def _commit_facts(
        spec: str = typer.Option(ANALYSIS_SPEC, "--spec"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path('commit_facts.json')
        core_commit_facts.run_commit_facts(spec, target)

    @app.command("commit-shards", help="Build deterministic shard manifests from commit facts")
    def _commit_shards(
        commit_facts: str | None = typer.Option(None, "--commit-facts"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        facts = commit_facts or resolve_analysis_path('commit_facts.json')
        target = out or resolve_analysis_path('commit_shards.json')
        core_commit_facts.run_commit_shards(facts, target)

    @app.command("code-history-claims", help="Build evidence-shaped claims from active git history")
    def _code_history_claims(
        start: str = typer.Option(..., "--start"),
        end: str = typer.Option(..., "--end"),
        project: str | None = typer.Option(None, "--project"),
        top_n: int = typer.Option(50, "--top-n"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path("code_history_claims.json"))
        rows = write_code_history_claims(
            target,
            start=_parse_date(start),
            end=_parse_date(end),
            project=project,
            top_n=top_n,
        )
        print(f"code-history-claims: {len(rows)} claims -> {target}")


def _register_machine(app: typer.Typer) -> None:
    @app.command("machine-telemetry", help="Build general statistical analysis over machine telemetry substrate rows")
    def _machine_telemetry(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_telemetry_analysis.json'))
        analysis = write_machine_telemetry_analysis(target, start=_opt_date(start), end=_opt_date(end))
        print(f"machine-telemetry: {analysis.coverage.sample_count} metric samples -> {target}")

    @app.command("machine-episodes", help="Detect typed machine-state episodes from telemetry substrate rows")
    def _machine_episodes(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_episode_analysis.json'))
        analysis = write_machine_episode_analysis(target, start=_opt_date(start), end=_opt_date(end))
        print(f"machine-episodes: {len(analysis.episodes)} episodes -> {target}")

    @app.command("machine-below", help="Build bounded below process/cgroup analysis from exported windows")
    def _machine_below(
        root: str = typer.Option("/realm/data/captures/stability-lab", "--root"),
        top_n: int = typer.Option(20, "--top-n"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_below_analysis.json'))
        analysis = write_below_analysis(target, root=Path(root), top_n=top_n)
        print(f"machine-below: {len(analysis.system)} bounded windows -> {target}")

    @app.command("machine-below-export-window", help="Export one live below window into bounded CSV files")
    def _machine_below_export_window(
        begin: str = typer.Option(..., "--begin", help="Begin time accepted by below replay/dump"),
        end: str | None = typer.Option(None, "--end", help="End time accepted by below replay/dump"),
        duration: str | None = typer.Option("5 min", "--duration", help="Duration when --end is omitted"),
        root: str = typer.Option("/realm/data/captures/stability-lab", "--root"),
        capture_id: str | None = typer.Option(None, "--capture-id"),
        top_n: int = typer.Option(20, "--top-n"),
        timeout_s: int = typer.Option(60, "--timeout-s"),
    ) -> None:
        export = export_live_below_window(
            root=Path(root),
            begin=begin,
            end=end,
            duration=duration,
            capture_id=capture_id,
            top_n=top_n,
            timeout_s=timeout_s,
        )
        print(
            "machine-below-export-window: "
            f"{export.capture_id} system={export.system_rows} "
            f"process={export.process_rows} cgroup={export.cgroup_rows} -> {export.report_path}"
        )
        if export.errors:
            for error in export.errors:
                print(f"error: {error}", file=sys.stderr)
            raise typer.Exit(1)

    @app.command("machine-below-attribution", help="Join typed machine episodes to bounded below capture windows")
    def _machine_below_attribution(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        root: str = typer.Option("/realm/data/captures/stability-lab", "--root"),
        top_n: int = typer.Option(5, "--top-n"),
        max_attributions: int = typer.Option(500, "--max-attributions"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_below_attribution.json'))
        analysis = write_below_attribution_analysis(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            root=Path(root),
            top_n=top_n,
            max_attributions=max_attributions,
        )
        print(
            "machine-below-attribution: "
            f"bounded={analysis.attributed_episode_count}/{analysis.pressure_episode_count} pressure episodes; "
            f"workload_resource={analysis.workload_resource_attributed_pressure_episode_count}/{analysis.pressure_episode_count}; "
            f"residual={analysis.residual_unattributed_pressure_episode_count} -> {target}"
        )

    @app.command(
        "machine-below-export-pressure-windows",
        help="Plan or export below windows for high-severity unattributed pressure episodes",
    )
    def _machine_below_export_pressure_windows(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        root: str = typer.Option("/realm/data/captures/stability-lab", "--root"),
        limit: int = typer.Option(10, "--limit"),
        padding_seconds: int = typer.Option(60, "--padding-seconds"),
        min_duration_seconds: int = typer.Option(120, "--min-duration-seconds"),
        top_n: int = typer.Option(20, "--top-n"),
        timeout_s: int = typer.Option(60, "--timeout-s"),
        dry_run: bool = typer.Option(True, "--dry-run/--write"),
    ) -> None:
        exports = export_below_windows_for_pressure_episodes(
            start=_opt_date(start),
            end=_opt_date(end),
            root=Path(root),
            limit=limit,
            padding_seconds=padding_seconds,
            min_duration_seconds=min_duration_seconds,
            top_n=top_n,
            timeout_s=timeout_s,
            dry_run=dry_run,
        )
        failed = sum(1 for item in exports if item.export and item.export.errors)
        succeeded = sum(1 for item in exports if item.export and not item.export.errors)
        if dry_run:
            print(f"machine-below-export-pressure-windows: planned {len(exports)} windows")
        else:
            print(
                "machine-below-export-pressure-windows: "
                f"attempted {len(exports)} windows; succeeded={succeeded}; failed={failed}"
            )
        any_errors = False
        for item in exports:
            plan = item.plan
            print(
                f"{plan.capture_id} {plan.episode_kind} severity={plan.severity:.3f} "
                f"begin={plan.begin.strftime('%Y-%m-%d %H:%M:%S')} "
                f"end={plan.end.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            if item.export and item.export.errors:
                any_errors = True
                for error in item.export.errors:
                    print(f"error: {plan.capture_id}: {error}", file=sys.stderr)
        if any_errors:
            raise typer.Exit(code=1)

    @app.command("machine-context", help="Join typed machine episodes to development/activity windows")
    def _machine_context(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        max_windows: int = typer.Option(500, "--max-windows"),
        include_polylogue: bool = typer.Option(False, "--include-polylogue/", help="Include Polylogue session windows"),
        include_ambient_sources: bool = typer.Option(
            False,
            "--include-ambient-sources/",
            help="Include terminal/git/ActivityWatch ambient context windows",
        ),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_context_windows.json'))
        analysis = write_machine_context_analysis(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            max_windows=max_windows,
            include_polylogue=include_polylogue,
            include_ambient_sources=include_ambient_sources,
        )
        print(f"machine-context: {analysis.windows_with_machine_episodes}/{analysis.window_count} windows with episodes -> {target}")

    @app.command("machine-work-states", help="Build typed machine/work state windows from machine context artifacts")
    def _machine_work_states(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        context: str | None = typer.Option(None, "--context"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_work_state_windows.json'))
        context_path = Path(context) if context else None
        analysis = write_machine_work_state_analysis(target, start=_opt_date(start), end=_opt_date(end), context_path=context_path)
        print(f"machine-work-states: {analysis.window_count} state windows -> {target}")

    @app.command("machine-work-observations", help="Build xtask work-observation summaries from substrate rows")
    def _machine_work_observations(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_work_observations.json'))
        payload = write_work_observation_analysis(target, start=_opt_date(start), end=_opt_date(end))
        print(
            "machine-work-observations: "
            f"{len(payload['daily'])} daily groups, "
            f"{len(payload['stage_summaries'])} stage summaries -> {target}"
        )

    @app.command("workflow-mechanics", help="Build workflow retry/command mechanics from work observations")
    def _workflow_mechanics(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: str | None = typer.Option(None, "--project"),
        refresh_id: str | None = typer.Option(None, "--refresh-id"),
        retry_gap_min: int = typer.Option(20, "--retry-gap-min"),
        limit: int = typer.Option(100, "--limit"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path("workflow_mechanics.json"))
        report = write_workflow_mechanics_report(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            project=project,
            refresh_id=refresh_id,
            retry_gap_min=retry_gap_min,
            limit=limit,
        )
        print(
            "workflow-mechanics: "
            f"{report.invocation_count} invocations, "
            f"{report.retry_chain_count} retry chains -> {target}"
        )

    @app.command("command-performance", help="Build command runtime outcomes joined to machine/work states")
    def _command_performance(
        start: str = typer.Option(..., "--start"),
        end: str = typer.Option(..., "--end"),
        state_path: str | None = typer.Option(None, "--state-path"),
        max_commands: int = typer.Option(1000, "--max-commands"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('command_performance_windows.json'))
        sp = Path(state_path) if state_path else None
        analysis = write_command_performance_analysis(
            target,
            start=_parse_date(start),
            end=_parse_date(end),
            state_path=sp,
            max_commands=max_commands,
        )
        print(f"command-performance: {analysis.command_count} commands -> {target}")

    @app.command("machine-observational-deltas", help="Build observational command-performance deltas by machine state")
    def _machine_observational_deltas(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        command_path: str | None = typer.Option(None, "--command-path"),
        min_cohort_size: int = typer.Option(2, "--min-cohort-size"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_observational_deltas.json'))
        cp = Path(command_path) if command_path else None
        analysis = write_observational_command_deltas(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            command_path=cp,
            min_cohort_size=min_cohort_size,
        )
        print(f"machine-observational-deltas: {len(analysis.deltas)} deltas -> {target}")

    @app.command("machine-attribution-candidates", help="Build non-causal machine attribution candidate set")
    def _machine_attribution_candidates(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        limit: int = typer.Option(25, "--limit"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_attribution_candidates.json'))
        analysis = write_machine_attribution_candidates(target, start=_opt_date(start), end=_opt_date(end), limit=limit)
        print(f"machine-attribution-candidates: {analysis.candidate_count} candidates -> {target}")

    @app.command("machine-benchmark-plans", help="Build controlled-benchmark plans from attribution candidates")
    def _machine_benchmark_plans(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        limit: int = typer.Option(10, "--limit"),
        repeats_per_cell: int = typer.Option(3, "--repeats-per-cell"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_benchmark_plans.json'))
        analysis = write_machine_benchmark_plans(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            repeats_per_cell=repeats_per_cell,
            limit=limit,
        )
        print(
            "machine-benchmark-plans: "
            f"{analysis.ready_plan_count}/{analysis.plan_count} ready -> {target}"
        )

    @app.command("machine-benchmark-bundle", help="Build exportable benchmark manifest templates")
    def _machine_benchmark_bundle(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        plans: str | None = typer.Option(None, "--plans"),
        limit: int = typer.Option(10, "--limit"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_benchmark_manifest_bundle.json'))
        analysis = write_machine_benchmark_manifest_bundle(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            plans_path=Path(plans) if plans else None,
            limit=limit,
        )
        print(
            "machine-benchmark-bundle: "
            f"{analysis.group_count} groups, {analysis.run_template_count} run templates -> {target}"
        )

    @app.command("machine-benchmark-preflight", help="Validate benchmark manifest templates before export/execution")
    def _machine_benchmark_preflight(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        bundle: str | None = typer.Option(None, "--bundle"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_benchmark_preflight.json'))
        analysis = write_machine_benchmark_preflight(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            manifest_bundle_path=Path(bundle) if bundle else None,
        )
        print(
            "machine-benchmark-preflight: "
            f"{analysis.ready_run_count}/{analysis.run_count} runs ready, "
            f"{analysis.issue_count} issues, {analysis.warning_count} warnings -> {target}"
        )

    @app.command("machine-benchmark-export", help="Export benchmark template manifests and run scripts; does not execute them")
    def _machine_benchmark_export(
        out_dir: str = typer.Option(..., "--out-dir"),
        plans: str | None = typer.Option(None, "--plans"),
        limit: int = typer.Option(10, "--limit"),
        overwrite: bool = typer.Option(False, "--overwrite/"),
        no_runner: bool = typer.Option(False, "--no-runner/"),
    ) -> None:
        bundle = analyze_machine_benchmark_manifest_bundle(
            plans_path=Path(plans) if plans else None,
            limit=limit,
        )
        written = export_machine_benchmark_manifest_bundle(
            bundle,
            Path(out_dir),
            overwrite=overwrite,
            write_runner=not no_runner,
        )
        print(
            "machine-benchmark-export: "
            f"{len(written)} files, {bundle.group_count} groups, "
            f"{bundle.run_template_count} run templates -> {Path(out_dir)}"
        )

    @app.command("machine-benchmark-handoff", help="Build ranked controlled-benchmark execution handoff")
    def _machine_benchmark_handoff(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        limit: int = typer.Option(10, "--limit"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_benchmark_execution_handoff.json'))
        analysis = write_machine_benchmark_execution_handoff(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            limit=limit,
        )
        print(
            "machine-benchmark-handoff: "
            f"{analysis.ready_group_count}/{analysis.handoff_count} groups ready, "
            f"{analysis.ready_run_count}/{analysis.run_template_count} runs ready -> {target}"
        )

    @app.command("machine-support-assessment", help="Rescore attribution support from benchmark and experiment evidence")
    def _machine_support_assessment(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_support_assessment.json'))
        analysis = write_machine_support_assessment(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
        )
        print(
            "machine-support-assessment: "
            f"{analysis.assessment_count} assessments, "
            f"{analysis.controlled_claim_count} controlled claims -> {target}"
        )

    @app.command("machine-benchmark-run-selected", help="Export, optionally execute, and rescore one ready benchmark group")
    def _machine_benchmark_run_selected(
        run_group_id: str | None = typer.Option(None, "--run-group-id"),
        candidate_id: str | None = typer.Option(None, "--candidate-id"),
        out_dir: str | None = typer.Option(None, "--out-dir"),
        execute: bool = typer.Option(False, "--execute/--dry-run"),
        materialize_after: bool = typer.Option(False, "--materialize-after/--no-materialize-after"),
        overwrite: bool = typer.Option(False, "--overwrite/"),
        require_ready: bool = typer.Option(True, "--require-ready/--allow-blocked"),
        start: str | None = typer.Option(None, "--start"),
        end: str | None = typer.Option(None, "--end"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path("machine_benchmark_selected_execution.json"))
        result = write_selected_benchmark_execution(
            target,
            run_group_id=run_group_id,
            candidate_id=candidate_id,
            output_dir=Path(out_dir) if out_dir else None,
            execute=execute,
            materialize_after=materialize_after,
            overwrite=overwrite,
            require_ready=require_ready,
            start=_opt_date(start),
            end=_opt_date(end),
        )
        print(
            "machine-benchmark-run-selected: "
            f"{result.run_group_id} scripts={len(result.run_scripts)} "
            f"execute={result.execute} materialize_after={result.materialize_after} -> {target}"
        )

    @app.command("devshell-performance", help="Build direnv/Nix devshell command performance view")
    def _devshell_performance(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        command_path: str | None = typer.Option(None, "--command-path"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('devshell_performance.json'))
        cp = Path(command_path) if command_path else None
        analysis = write_devshell_performance_analysis(target, start=_opt_date(start), end=_opt_date(end), command_path=cp)
        print(f"devshell-performance: {analysis.command_count} commands -> {target}")

    @app.command("machine-baselines", help="Build observational machine telemetry baselines and era summaries")
    def _machine_baselines(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_observational_baselines.json'))
        analysis = write_machine_observational_baselines(target, start=_opt_date(start), end=_opt_date(end))
        print(f"machine-baselines: {len(analysis.by_hour)} hour groups, {len(analysis.by_hardware_regime)} hardware regimes -> {target}")

    @app.command("machine-experiments", help="Build manifest-backed machine experiment claim packs")
    def _machine_experiments(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        refresh_id: str | None = typer.Option(None, "--refresh-id"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_experiment_claims.json'))
        analysis = write_machine_experiment_claims(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            refresh_id=refresh_id,
        )
        print(f"machine-experiments: {analysis.controlled_claim_count}/{analysis.run_count} controlled claim packs -> {target}")

    @app.command("machine-experiment-manifests", help="Audit raw experiment manifests before promotion/claiming")
    def _machine_experiment_manifests(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        root: str | None = typer.Option(None, "--root"),
        require_file_refs: bool = typer.Option(False, "--require-file-refs/"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_experiment_manifest_diagnostics.json'))
        analysis = write_machine_experiment_manifest_diagnostics(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            root=Path(root) if root else None,
            require_file_refs=require_file_refs,
        )
        print(
            "machine-experiment-manifests: "
            f"{analysis.source_loadable_count}/{analysis.manifest_count} source-loadable, "
            f"{analysis.controlled_benchmark_valid_count} controlled-valid, "
            f"{analysis.ad_hoc_observational_count} ad-hoc observational, "
            f"{analysis.controlled_run_invalid_count} invalid executed -> {target}"
        )

    @app.command("machine-promote-experiments", help="Promote machine experiment manifests and metric samples into the DuckDB substrate")
    def _machine_promote_experiments(
        start: str = typer.Option(..., "--start"),
        end: str = typer.Option(..., "--end"),
    ) -> None:
        start_date = _parse_date(start)
        end_date = _parse_date(end)
        result = run_substrate_promote(
            commit_facts_file=resolve_analysis_path("active_commit_facts.json"),
            file_changes_file=resolve_analysis_path("active_file_change_facts.json"),
            symbol_changes_file=resolve_analysis_path("active_symbol_changes.json"),
            pr_review_file=resolve_analysis_path("active_pr_review_topology.json"),
            refresh_id=f"machine-experiments:{start_date.isoformat()}:{end_date.isoformat()}",
            window_start=start_date,
            window_end=end_date,
            sources={SOURCE_MACHINE, SOURCE_MACHINE_EXPERIMENTS},
            write_evidence_graph=False,
        )
        print(
            "machine-promote-experiments: "
            f"{result.counts.get('machine_experiment_runs', 0)} experiment rows, "
            f"{result.counts.get('machine_metric_sample', 0)} metric rows -> {result.refresh_id}"
        )

    @app.command("machine-readiness", help="Build machine-analysis readiness and coverage report")
    def _machine_readiness(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        bios_boundary: str = typer.Option(None, "--bios-boundary"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path('machine_analysis_readiness.json'))
        analysis = write_machine_analysis_readiness(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            bios_boundary=_opt_date(bios_boundary) or DEFAULT_BIOS_BOUNDARY,
        )
        unstable = sum(1 for dimension in analysis.dimensions if dimension.status != "stable")
        print(f"machine-readiness: {len(analysis.dimensions)} dimensions, {unstable} non-stable -> {target}")

    @app.command("machine-status", help="Summarize current machine-analysis artifact readiness")
    def _machine_status(
        as_json: bool = typer.Option(False, "--json/", help="Emit machine status as JSON"),
    ) -> None:
        payload = machine_status_payload()
        if as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return
        support = payload["support"]
        experiments = payload["experiments"]
        manifests = payload["experiment_manifests"]
        claims = payload["claims"]
        dataset = payload["dataset"]
        print("machine-status:")
        print(
            "  support: "
            f"{support['assessment_natural_experiment']} natural assessments, "
            f"{support['executed_controlled_claim_count']} executed controlled claims, "
            f"{support['assessment_insufficient']} insufficient assessments"
        )
        print(
            "  experiments: "
            f"{experiments['controlled']}/{experiments['run_count']} controlled, "
            f"{experiments['observational']} observational, "
            f"manifest-validation={experiments['by_manifest_validation_status']}"
        )
        print(
            "  experiment manifests: "
            f"{manifests['source_loadable_count']}/{manifests['manifest_count']} source-loadable, "
            f"{manifests['controlled_benchmark_valid_count']} controlled-valid, "
            f"{manifests['ad_hoc_observational_count']} ad-hoc observational, "
            f"{manifests['controlled_run_invalid_count']} invalid executed"
        )
        print(f"  claims: {claims['by_support_level']}")
        print(
            "  dataset: "
            f"features={dataset['feature_status'] or 'missing'} "
            f"multiplicity={dataset['multiplicity_status'] or 'missing'}"
        )
        below = payload.get("below_attribution", {})
        if isinstance(below, dict):
            print(
                "  below attribution: "
                f"{below.get('bounded_below_attributed_pressure_episode_count', 0)} bounded, "
                f"{below.get('workload_resource_attributed_pressure_episode_count', 0)} workload, "
                f"{below.get('residual_unattributed_pressure_episode_count', 0)} residual pressure"
            )
        gaps = payload.get("gaps", {})
        print(f"  gaps: {gaps.get('gap_count', 0)}")
        for source, count in (gaps.get("by_missing_source") or {}).items():
            print(f"    - {source}: {count}")
        preflight = payload.get("benchmark_preflight", {})
        print(
            "  benchmark preflight: "
            f"{preflight.get('ready_run_count', 0)}/{preflight.get('run_count', 0)} ready, "
            f"{preflight.get('issue_count', 0)} issues"
        )
        print(f"  artifacts: {payload['artifacts']['available']}/{payload['artifacts']['expected']} available")
        if payload["blockers"]:
            print("  blockers:")
            for blocker in payload["blockers"]:
                print(f"    - {blocker}")
        else:
            print("  blockers: none")


def _register_misc(app: typer.Typer) -> None:
    @app.command("lynchpin-self", help="Run Lynchpin self-analysis (module breakdown, import graph, coverage)")
    def _lynchpin_self(
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        from .core.self_analysis import run_self_analysis

        target = out or resolve_analysis_path('lynchpin_self_metrics.json')
        metrics = run_self_analysis(out_file=target)
        print(f"lynchpin-self: {metrics.total_files} files, {metrics.total_code_lines} code lines")
        print(f"  subpackages: {', '.join(s.subpackage + f' ({s.code_lines})' for s in metrics.subpackages)}")
        if metrics.isolation_warnings:
            print(f"  isolation: {'; '.join(metrics.isolation_warnings)}")

    @app.command("google-takeout-retrospective", help="Mine Google Takeout products into retrospective structures")
    def _google_takeout_retrospective(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        session_gap_min: int = typer.Option(45, "--session-gap-min"),
        top_n: int = typer.Option(50, "--top-n"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path("google_takeout_retrospective.json"))
        report = write_google_takeout_retrospective(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            session_gap_min=session_gap_min,
            top_n=top_n,
        )
        print(
            "google-takeout-retrospective: "
            f"{report.event_count} events, {report.active_days} active days -> {target}"
        )

    @app.command("personal-interest-trace", help="Fuse weak personal-interest traces across local sources")
    def _personal_interest_trace(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        top_n: int = typer.Option(50, "--top-n"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path("personal_interest_trace.json"))
        report = write_personal_interest_trace(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            top_n=top_n,
        )
        print(
            "personal-interest-trace: "
            f"{report.topic_count} weak topics -> {target}"
        )

    @app.command("claim-calibration", help="Check evidence-shaped claim artifacts for calibration issues")
    def _claim_calibration(
        artifact: list[str] = typer.Option(None, "--artifact"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path("claim_calibration.json"))
        artifacts = artifact or [
            resolve_analysis_path("code_history_claims.json"),
            resolve_analysis_path("machine_attribution_claims.json"),
        ]
        report = write_claim_calibration(target, claim_artifacts=artifacts)
        print(
            "claim-calibration: "
            f"{report.claim_count} claims, {report.issue_count} issues -> {target}"
        )

    @app.command("keylog-analysis", help="Analyze keylog keybind and text-shape metadata")
    def _keylog_analysis(
        start: str = typer.Option(..., "--start"),
        end: str = typer.Option(..., "--end"),
        bindings: str = typer.Option(
            "/realm/project/sinnix/modules/features/desktop/hyprland/bindings.nix",
            "--bindings",
        ),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = Path(out or resolve_analysis_path("keylog_analysis.json"))
        analysis = write_keylog_analysis(
            target,
            start=_parse_date(start),
            end=_parse_date(end),
            bindings_path=Path(bindings),
        )
        print(
            "keylog-analysis: "
            f"{analysis.keypress_count} keypresses, "
            f"{analysis.matched_keybind_count} inferred keybind uses -> {target}"
        )

    @app.command("diagnostic-ledger-status", help="Debug diagnostic ledger and exceptional queue state", hidden=True)
    def _diagnostic_ledger_status(
        receipts: int = typer.Option(5, "--receipts", min=0),
    ) -> None:
        status = diagnostic_ledger_status_payload()
        print("diagnostic-ledger-status:")
        print(f"  canonical: {status['canonical_path']} present={status['canonical_present']}")
        print(f"  snapshot: {status['snapshot_path']} present={status['snapshot_present']}")
        if receipts:
            for row in latest_receipts(limit=receipts):
                print(
                    "  receipt: "
                    f"{row['decision']} {row['target']} "
                    f"caller={row['caller']} reason={row['reason']}"
                )

    @app.command("observability-status", help="Print compact Lynchpin observability status JSON")
    def _observability_status() -> None:
        import json

        payload = compact_materialization_status()
        print(json.dumps({**payload, "kind": "lynchpin_observability_status"}, sort_keys=True))

    @app.command("diagnostic-ledger-explain", help="Explain diagnostic ledger decisions and exceptional work for a target", hidden=True)
    def _diagnostic_ledger_explain(
        target: str = typer.Argument(...),
        limit: int = typer.Option(20, "--limit", min=1),
    ) -> None:
        import json

        print(json.dumps(freshness_explain_target(target, limit=limit), sort_keys=True))

    @app.command("diagnostic-ledger-receipts", help="List diagnostic ledger decisions", hidden=True)
    def _diagnostic_ledger_receipts(
        limit: int = typer.Option(20, "--limit", min=1),
        target: str | None = typer.Option(None, "--target"),
        decision: str | None = typer.Option(None, "--decision"),
        payload: bool = typer.Option(False, "--payload/--no-payload"),
    ) -> None:
        rows = latest_receipts(limit=limit, target=target, decision=decision, include_payload=payload)
        print(f"diagnostic-ledger-receipts: {len(rows)} rows")
        for row in rows:
            print(
                "  - "
                f"{row['created_at_utc']} {row['decision']} {row['target']} "
                f"caller={row['caller']} reason={row['reason']}"
            )
            if payload and isinstance(row.get("payload"), dict):
                artifact_statuses = row["payload"].get("artifact_statuses") or []
                for status_row in artifact_statuses:
                    if isinstance(status_row, dict):
                        print(
                            "      artifact "
                            f"{status_row.get('state')} {status_row.get('path')} "
                            f"age={status_row.get('age_seconds')}"
                        )

    @app.command("diagnostic-source-materialization-decision", help="Debug a source-contract materialization decision", hidden=True)
    def _diagnostic_source_materialization_decision(
        source: str = typer.Argument(...),
        start: str | None = typer.Option(None, "--start"),
        end: str | None = typer.Option(None, "--end"),
    ) -> None:
        from datetime import timedelta

        from lynchpin.materialization import ensure_materialized

        start_d = _parse_date(start) if start else None
        end_d = _parse_date(end) + timedelta(days=1) if end else None
        result = ensure_materialized(
            source,
            window=(start_d, end_d) if start_d is not None and end_d is not None else None,
        )
        print(
            "diagnostic-source-materialization-decision: "
            f"{result.status} {result.name} changed={result.changed} reason={result.reason}"
        )

    @app.command("diagnostic-ledger-dependencies", help="List diagnostic ledger dependency/provenance edges", hidden=True)
    def _diagnostic_ledger_dependencies(
        target: str | None = typer.Option(None, "--target"),
        receipt_id: str | None = typer.Option(None, "--receipt-id"),
        limit: int = typer.Option(50, "--limit", min=1),
    ) -> None:
        rows = freshness_dependencies(target=target, receipt_id=receipt_id, limit=limit)
        print(f"diagnostic-ledger-dependencies: {len(rows)} rows")
        for row in rows:
            print(
                "  - "
                f"{row['receipt_id']} {row['target']} <- {row['depends_on']} "
                f"reason={row['reason']}"
            )

    @app.command("materialize-current-state", help="DAG-orchestrated active-project current-state materialization")
    def _materialize_current_state(
        start: str = typer.Option(..., "--start"),
        end: str = typer.Option(..., "--end"),
        dry_run: bool = typer.Option(False, "--dry-run/", help="Show execution plan without running"),
        explain_materialization: bool = typer.Option(
            False,
            "--explain-materialization/",
            help="Explain materialization decisions during dry-run",
        ),
        up_to: str | None = typer.Option(None, "--up-to", help="Stop after this step completes"),
        project: list[str] = typer.Option(None, "--project"),
        github_frontier: bool = typer.Option(False, "--github-frontier/"),
        weak_tags: bool = typer.Option(False, "--weak-tags/"),
        persist_weak_tags: bool = typer.Option(False, "--persist-weak-tags/"),
    ) -> None:
        dag = current_state_dag(
            start=_parse_date(start),
            end=_parse_date(end),
            projects=list(project or []),
            include_github_frontier=github_frontier,
            weak_tags=weak_tags,
            persist_weak_tags=persist_weak_tags,
        )
        code = _run_dag(
            dag,
            dry_run=dry_run,
            up_to=up_to,
            explain_materialization=explain_materialization,
            requested_window=(_parse_date(start), _parse_date(end)),
        )
        raise typer.Exit(code=code)

    def _run_machine_materialization(
        *,
        dry_run: bool,
        explain: bool,
        incremental: bool,
        up_to: str | None,
        start: str | None,
        end: str | None,
    ) -> None:
        start_d = _opt_date(start)
        end_d = _opt_date(end)
        dag = machine_analysis_dag(start=start_d, end=end_d)
        requested_window = _rolling_window(start=start_d, end=end_d, days=90)
        code = _run_dag(
            dag,
            dry_run=dry_run,
            up_to=up_to,
            explain_materialization=explain,
            incremental=incremental,
            requested_window=requested_window,
        )
        raise typer.Exit(code=code)

    @app.command("materialize-machine", help="Materialize machine-analysis products")
    def _materialize_machine(
        dry_run: bool = typer.Option(False, "--dry-run/", help="Show execution plan without running"),
        explain_materialization: bool = typer.Option(
            False,
            "--explain-materialization/",
            help="Explain incremental materialization decisions during dry-run",
        ),
        incremental: bool = typer.Option(True, "--incremental/--full", help="Run only missing/expired machine-analysis steps"),
        up_to: str | None = typer.Option(None, "--up-to", help="Stop after this step completes"),
        start: str = typer.Option(None, "--start", help="Window start (ISO date)"),
        end: str = typer.Option(None, "--end", help="Window end (ISO date)"),
    ) -> None:
        _run_machine_materialization(
            dry_run=dry_run,
            explain=explain_materialization,
            incremental=incremental,
            up_to=up_to,
            start=start,
            end=end,
        )


def _materialization_log_path() -> Path:
    """Resolve the materialization log path under the configured local root.

    Repo-rooted (honours ``LYNCHPIN_LOCAL_ROOT`` / ``LYNCHPIN_REPO_ROOT``)
    rather than CWD-relative, so ``materialize`` works regardless of the
    launching directory. A systemd unit without ``WorkingDirectory`` would
    otherwise fail with ``PermissionError`` trying to create ``.lynchpin`` in
    ``/``.
    """
    return get_config().local_root / "log" / "materialization.log"


def _run_dag(
    dag,
    *,
    dry_run: bool,
    up_to: str | None,
    explain_materialization: bool = False,
    incremental: bool = False,
    memoize: bool = True,
    requested_window: tuple[date, date] | None = None,
) -> int:
    from .core.dag import StepStatus
    from .core import memo as _memo

    if dry_run:
        try:
            if up_to:
                results = dag.run(dry_run=True, up_to=up_to)
                print(f"DAG: {dag.name}")
                print(f"Steps: {len(results)} (stops after: {up_to})")
                for result in results:
                    print(f"  - {result.name}")
            else:
                print(dag.describe())
            if explain_materialization or incremental:
                selected = tuple(result.name for result in dag.run(dry_run=True, up_to=up_to))
                policies = _materialization_policies_for_dag(dag, selected)
                plan = materialization_plan_for_dag(
                    dag,
                    policies=policies,
                    up_to=up_to,
                    requested_window=requested_window,
                )
                if incremental:
                    print(render_materialization_plan(plan))
                else:
                    print(render_materialization_plan(plan))
        except ValueError as exc:
            print(f"Materialization failed: {exc}")
            return 1
        return 0

    log_path = _materialization_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _emit(line: str) -> None:
        """Write a line to both stderr (immediate) and the permanent log."""
        now = datetime.now().strftime("%H:%M:%S")
        stamped = f"[{now}] {line}"
        print(stamped, file=sys.stderr, flush=True)
        with log_path.open("a", encoding="utf-8") as lf:
            lf.write(stamped + "\n")

    def _on_step(result):
        icon = '✓' if result.status == StepStatus.SUCCESS else '✗' if result.status == StepStatus.FAILED else '⊘'
        elapsed = f" ({result.elapsed_seconds:.1f}s)" if result.elapsed_seconds else ""
        _emit(f"  {icon} {result.name}{elapsed}")
        if result.error and result.status == StepStatus.FAILED:
            for line in result.error.split('\n')[:3]:
                _emit(f"    {line}")

    try:
        materialization_plan = None
        selected = tuple(result.name for result in dag.run(dry_run=True, up_to=up_to))
        selected_count = len(selected) if up_to else len(dag._steps)
        target = f"; stops after: {up_to}" if up_to else ""
        _emit(f"Starting {dag.name} DAG ({selected_count} steps{target})")

        # Content-keyed memoization: skip fingerprinted steps whose inputs are
        # unchanged since their last successful run (output is byte-identical).
        # Steps without a fingerprint always run.
        memo_current: dict[str, str] = {}
        memo_skip: set[str] = set()
        if memoize and not dry_run:
            memo_current = _memo.compute_fingerprints(
                {name: dag._steps[name] for name in selected}
            )
            memo_skip = _memo.memoized_skips(memo_current, _memo.load_fingerprints())
            if memo_skip:
                _emit(
                    "Memoized (unchanged inputs, skipping): "
                    + ", ".join(sorted(memo_skip))
                )

        if incremental or explain_materialization:
            policies = _materialization_policies_for_dag(dag, selected)
            plan = materialization_plan_for_dag(
                dag,
                policies=policies,
                up_to=up_to,
                requested_window=requested_window,
            )
            materialization_plan = plan
            runnable = executable_steps(plan) - memo_skip
            _emit(render_materialization_plan(plan))
            results = dag.run_selected(runnable, on_step=_on_step, up_to=up_to)
        else:
            runnable = set(selected) - memo_skip
            results = dag.run_selected(runnable, on_step=_on_step, up_to=up_to)
    except ValueError as exc:
        _emit(f"Materialization failed: {exc}")
        return 1

    failed = [result for result in results if result.status == StepStatus.FAILED]
    skipped = [result for result in results if result.status == StepStatus.SKIPPED]
    succeeded = [result for result in results if result.status == StepStatus.SUCCESS]
    total_time = sum(result.elapsed_seconds for result in results)

    # Record fingerprints for steps that ran successfully this pass, so an
    # unchanged input next time can be memoized away.
    if memoize and not dry_run and memo_current:
        succeeded_names = {result.name for result in succeeded}
        _memo.record_fingerprints(
            {name: fp for name, fp in memo_current.items() if name in succeeded_names}
        )
    report_stem = dag.name.replace("-", "_")
    if report_stem.endswith("_refresh"):
        report_stem = report_stem[: -len("_refresh")]
    if report_stem.endswith("_materialization"):
        report_stem = report_stem[: -len("_materialization")]
    report_name = f"{report_stem}_materialization_report.json"
    write_materialization_report(
        Path(resolve_analysis_path(report_name)),
        dag_name=dag.name,
        results=results,
        up_to=up_to,
        materialization_plan=materialization_plan,
    )
    _emit(f"Materialization report: {resolve_analysis_path(report_name)}")
    _emit(
        f"Materialization complete: {len(succeeded)} succeeded, {len(failed)} failed, "
        f"{len(skipped)} skipped ({total_time:.1f}s)"
    )
    return 1 if failed else 0


def _materialization_policies_for_dag(dag, selected: tuple[str, ...]):
    if dag.name == "machine-analysis-materialization":
        return machine_materialization_policies(selected)
    if dag.name in {"analysis-materialization", "current-state-materialization"}:
        return analysis_materialization_policies(selected)
    return {}


app = build_app()


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, standalone_mode=False)
    except (typer.Exit, SystemExit) as exc:
        code = exc.exit_code if isinstance(exc, typer.Exit) else (exc.code or 0)
        return int(code or 0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
