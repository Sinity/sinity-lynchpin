from datetime import datetime, timezone

from lynchpin.sources.analysis_artifacts import analysis_claims, artifact_inventory, latest_artifacts

UTC = timezone.utc


def test_artifact_inventory_classifies_generated_products(tmp_path):
    (tmp_path / "maps").mkdir()
    metrics = tmp_path / "sinex_structure_metrics.json"
    metrics.write_text(
        '{"generated_at_utc":"2026-05-06T12:00:00+00:00","totals":{},"items":[]}',
        encoding="utf-8",
    )
    (tmp_path / "maps" / "project-maps.md").write_text("# Project Maps\n", encoding="utf-8")

    artifacts = artifact_inventory(tmp_path)
    by_name = {artifact.name: artifact for artifact in artifacts}

    assert by_name["sinex_structure_metrics.json"].project == "sinex"
    assert by_name["sinex_structure_metrics.json"].projects == ("sinex",)
    assert by_name["sinex_structure_metrics.json"].kind == "json"
    assert by_name["sinex_structure_metrics.json"].generated_at == datetime(2026, 5, 6, 12, tzinfo=UTC)
    assert by_name["sinex_structure_metrics.json"].top_level_keys == ("generated_at_utc", "items", "totals")
    assert by_name["maps/project-maps.md"].project == "sinex"
    assert by_name["maps/project-maps.md"].projects == ("sinex",)


def test_artifact_inventory_reuses_manifest_without_reparsing_json(monkeypatch, tmp_path):
    artifact_path = tmp_path / "workflow_mechanics.json"
    artifact_path.write_text(
        '{"generated_at_utc": "2026-06-02T00:00:00+00:00", "invocation_count": 2}',
        encoding="utf-8",
    )

    first = artifact_inventory(tmp_path)
    assert [artifact.name for artifact in first] == ["workflow_mechanics.json"]

    monkeypatch.setattr(
        "lynchpin.sources.analysis_artifacts._metadata",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("manifest should satisfy unchanged inventory")
        ),
    )
    second = artifact_inventory(tmp_path)

    assert second == first


def test_artifact_inventory_invalidates_manifest_when_artifact_changes(tmp_path):
    artifact_path = tmp_path / "workflow_mechanics.json"
    artifact_path.write_text(
        '{"generated_at_utc": "2026-06-02T00:00:00+00:00", "invocation_count": 2}',
        encoding="utf-8",
    )
    first = artifact_inventory(tmp_path)

    artifact_path.write_text(
        '{"generated_at_utc": "2026-06-03T00:00:00+00:00", "invocation_count": 3}',
        encoding="utf-8",
    )
    second = artifact_inventory(tmp_path)

    assert second[0].generated_at != first[0].generated_at


def test_artifact_inventory_extracts_known_json_briefs(tmp_path):
    status = tmp_path / "analysis_status.json"
    status.write_text(
        '{"families":{"a":{"status":"stable"},"b":{"status":"missing"},"c":{"status":"stable"}}}',
        encoding="utf-8",
    )
    scope = tmp_path / "work_package_scope.json"
    scope.write_text(
        '{"ecosystems":{"sinex":{"summary":{"unit_count":12}},"polylogue":{"summary":{"unit_count":4}}}}',
        encoding="utf-8",
    )
    current_state = tmp_path / "current_state_context_pack.json"
    current_state.write_text('{"mode":"materialized","projects":[{"project":"lynchpin"}],"claims":[{},{}]}', encoding="utf-8")
    active_snapshot = tmp_path / "active_project_snapshot.json"
    active_snapshot.write_text(
        '{"window":{"start":"2026-05-01","end":"2026-05-05"},"projects":[{"project":"sinex"},{"project":"polylogue"}]}',
        encoding="utf-8",
    )
    active_commits = tmp_path / "active_commit_facts.json"
    active_commits.write_text(
        '{"summary":{"commit_count":7,"available_project_count":2},"projects":[{"project":"sinex"},{"project":"polylogue"}]}',
        encoding="utf-8",
    )
    active_files = tmp_path / "active_file_change_facts.json"
    active_files.write_text(
        '{"summary":{"file_change_count":40,"classified_file_change_count":32},"projects":[{"project":"sinex"}]}',
        encoding="utf-8",
    )
    active_work = tmp_path / "active_work_packages.json"
    active_work.write_text(
        '{"summary":{"package_count":6,"available_project_count":2},"projects":[{"project":"sinex"},{"project":"polylogue"}]}',
        encoding="utf-8",
    )
    velocity = tmp_path / "project_velocity_windows.json"
    velocity.write_text(
        '{"summary":{"project_count":2,"strong_support_projects":["sinex"],"moderate_support_projects":["polylogue"]},'
        '"projects":[{"project":"sinex"},{"project":"polylogue"}]}',
        encoding="utf-8",
    )
    code_history = tmp_path / "code_history_claims.json"
    code_history.write_text(
        """
        {
          "window":{"start":"2026-05-01","end":"2026-05-05"},
          "claim_count":2,
          "claims":[
            {
              "claim_id":"claim:abc",
              "claim_type":"code_hotspot",
              "project":"sinex",
              "summary":"sinex:src is a hotspot",
              "confidence":0.7,
              "support_level":"moderate",
              "source_ids":["c1"],
              "relation_ids":[],
              "caveats":["observational"],
              "payload":{"path_root":"src"}
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    calibration = tmp_path / "claim_calibration.json"
    calibration.write_text('{"claim_count":2,"issue_count":1}', encoding="utf-8")
    takeout = tmp_path / "google_takeout_retrospective.json"
    takeout.write_text('{"event_count":123,"active_days":9}', encoding="utf-8")
    interests = tmp_path / "personal_interest_trace.json"
    interests.write_text('{"topic_count":7}', encoding="utf-8")
    workflow = tmp_path / "workflow_mechanics.json"
    workflow.write_text('{"invocation_count":44,"retry_chain_count":3}', encoding="utf-8")
    machine = tmp_path / "machine_telemetry_analysis.json"
    machine.write_text(
        '{"coverage":{"sample_count":42},"daily":[{},{}],"hardware_regimes":[]}',
        encoding="utf-8",
    )
    below = tmp_path / "machine_below_analysis.json"
    below.write_text(
        '{"system":[{}],"top_processes":[{},{}],"top_cgroups":[{}]}',
        encoding="utf-8",
    )
    python_dep = tmp_path / "active_python_dependency_hygiene.json"
    python_dep.write_text(
        """
        {
          "projects":[
            {
              "project":"sinity-lynchpin",
              "audit":{"advisories":[{"observed_import":true},{"observed_import":false}]}
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    rust_dep = tmp_path / "active_rust_dependency_hygiene.json"
    rust_dep.write_text(
        """
        {
          "workspaces":[
            {
              "project":"sinex",
              "audit":{"advisories":[{"id":"RUSTSEC-demo"}]}
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    by_name = {artifact.name: artifact for artifact in artifact_inventory(tmp_path)}

    assert by_name["analysis_status.json"].brief == "families missing=1, stable=2"
    assert by_name["work_package_scope.json"].brief == "sinex=12 units, polylogue=4 units"
    assert by_name["current_state_context_pack.json"].brief == "materialized context pack, 1 project slices, 2 supported claims"
    assert by_name["current_state_context_pack.json"].projects == ("sinity-lynchpin",)
    assert by_name["active_project_snapshot.json"].brief == "2 active project snapshots, 2026-05-01 to 2026-05-05"
    assert by_name["active_project_snapshot.json"].projects == ("polylogue", "sinex")
    assert by_name["active_commit_facts.json"].brief == "7 active commits across 2 projects"
    assert by_name["active_commit_facts.json"].projects == ("polylogue", "sinex")
    assert by_name["active_file_change_facts.json"].brief == "40 active file changes (32 classified)"
    assert by_name["active_file_change_facts.json"].projects == ("sinex",)
    assert by_name["active_work_packages.json"].brief == "6 active work packages across 2 projects"
    assert by_name["active_work_packages.json"].projects == ("polylogue", "sinex")
    assert by_name["project_velocity_windows.json"].brief == "2 velocity windows; strong=1, moderate=1"
    assert by_name["project_velocity_windows.json"].projects == ("polylogue", "sinex")
    assert by_name["code_history_claims.json"].brief == "1 code-history claims, 2026-05-01 to 2026-05-05"
    assert by_name["code_history_claims.json"].projects == ("sinex",)
    assert by_name["claim_calibration.json"].brief == "2 claims calibrated, 1 issues"
    assert by_name["google_takeout_retrospective.json"].brief == "123 Google Takeout events, 9 active days"
    assert by_name["personal_interest_trace.json"].brief == "7 weak personal-interest topics"
    assert by_name["workflow_mechanics.json"].brief == "44 work invocations, 3 retry chains"
    assert by_name["machine_telemetry_analysis.json"].brief == "42 machine metric samples, 2 daily profiles"
    assert by_name["machine_telemetry_analysis.json"].projects == ("sinity-lynchpin",)
    assert by_name["machine_below_analysis.json"].brief == "1 below windows, 2 process rows, 1 cgroup rows"
    assert by_name["machine_below_analysis.json"].projects == ("sinity-lynchpin",)
    assert by_name["active_python_dependency_hygiene.json"].brief == (
        "1 Python dependency hygiene rows, 2 advisories (1 observed in imports)"
    )
    assert by_name["active_python_dependency_hygiene.json"].projects == ("sinity-lynchpin",)
    assert by_name["active_rust_dependency_hygiene.json"].brief == (
        "1 Rust dependency hygiene rows, 1 advisories"
    )
    assert by_name["active_rust_dependency_hygiene.json"].projects == ("sinex",)


def test_analysis_claims_extract_code_history_claims(tmp_path):
    (tmp_path / "code_history_claims.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "claims":[
            {
              "claim_id":"claim:abc",
              "claim_type":"code_hotspot",
              "project":"sinex",
              "summary":"sinex:src is a hotspot",
              "confidence":0.7,
              "support_level":"moderate",
              "source_ids":["c1"],
              "relation_ids":[],
              "caveats":["observational"],
              "payload":{"path_root":"src"}
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    claim = analysis_claims(projects=("sinex",), root=tmp_path)[0]

    assert claim.id == "claim:abc"
    assert claim.claim_type == "code_hotspot"
    assert claim.project == "sinex"
    assert claim.payload["path_root"] == "src"
    assert claim.payload["support_level"] == "moderate"


def test_analysis_claims_can_reuse_supplied_artifact_inventory(monkeypatch, tmp_path):
    (tmp_path / "code_history_claims.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "claims":[
            {
              "claim_id":"claim:abc",
              "claim_type":"code_hotspot",
              "project":"sinex",
              "summary":"sinex:src is a hotspot",
              "confidence":0.7,
              "payload":{"path_root":"src"}
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    artifacts = latest_artifacts(projects=("sinex",), root=tmp_path)
    monkeypatch.setattr(
        "lynchpin.sources.analysis_artifacts.latest_artifacts",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("artifact inventory should be reused")
        ),
    )

    claims = analysis_claims(projects=("sinex",), artifacts=artifacts)

    assert [claim.summary for claim in claims] == ["sinex:src is a hotspot"]


def test_analysis_claims_reuses_claim_manifest(monkeypatch, tmp_path):
    (tmp_path / "code_history_claims.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "claims":[
            {
              "claim_id":"claim:abc",
              "claim_type":"code_hotspot",
              "project":"sinex",
              "summary":"sinex:src is a hotspot",
              "confidence":0.7,
              "payload":{"path_root":"src"}
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    artifacts = latest_artifacts(projects=("sinex",), root=tmp_path)

    first = analysis_claims(projects=("sinex",), artifacts=artifacts)
    monkeypatch.setattr(
        "lynchpin.sources.analysis_artifacts._json_payload",
        lambda path: (_ for _ in ()).throw(AssertionError("claim JSON reparsed")),
    )
    second = analysis_claims(projects=("sinex",), artifacts=artifacts)

    assert [claim.id for claim in first] == ["claim:abc"]
    assert [claim.id for claim in second] == ["claim:abc"]


def test_artifact_inventory_extracts_generated_artifact_references(tmp_path):
    (tmp_path / "commit_facts.json").write_text('{"ecosystems":{}}', encoding="utf-8")
    status = tmp_path / "analysis_status.json"
    status.write_text(
        '{"families":{"commit_transport":{"artifacts":["'
        + str(tmp_path / "commit_facts.json")
        + '","/outside/ignored.json"]}}}',
        encoding="utf-8",
    )

    by_name = {artifact.name: artifact for artifact in artifact_inventory(tmp_path)}

    assert by_name["analysis_status.json"].references == ("commit_facts.json",)


def test_artifact_inventory_keeps_parse_failures_visible(tmp_path):
    broken = tmp_path / "polylogue_metrics.json"
    broken.write_text("{not json", encoding="utf-8")

    artifact = artifact_inventory(tmp_path)[0]

    assert artifact.status == "partial"
    assert artifact.project == "polylogue"
    assert "JSONDecodeError" in artifact.reason


def test_latest_artifacts_filters_by_multi_project_affinity(tmp_path):
    (tmp_path / "sinex_structure_metrics.json").write_text('{"totals":{}}', encoding="utf-8")
    (tmp_path / "project-maps.md").write_text("# Maps\n", encoding="utf-8")
    (tmp_path / "unknown.md").write_text("# Unknown\n", encoding="utf-8")
    (tmp_path / "polylogue_metrics.json").write_text('{"totals":{}}', encoding="utf-8")

    artifacts = latest_artifacts(projects=("sinex",), root=tmp_path)
    names = {artifact.name for artifact in artifacts}

    assert names == {"sinex_structure_metrics.json", "project-maps.md"}


def test_analysis_claims_extract_active_project_snapshots(tmp_path):
    (tmp_path / "active_project_snapshot.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "window":{"start":"2026-05-01","end":"2026-05-05"},
          "projects":[
            {
              "project":"sinex",
              "default_branch":"master",
              "head":"abc123",
              "dirty":false,
              "quality_gates":["cargo","tests"],
              "structure":{"counted_files":10,"counted_lines":200,"tracked_files":12},
              "recent_git":{
                "commit_count":3,
                "active_days":2,
                "files_changed":9,
                "capped_category_touches":{"src":4},
                "large_touch_commits":[],
                "top_subjects":["feat: one"]
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    claim = analysis_claims(projects=("sinex",), root=tmp_path)[0]

    assert claim.id == "active-project-snapshot:sinex"
    assert claim.claim_type == "project_snapshot"
    assert claim.project == "sinex"
    assert "3 first-parent commits" in claim.summary
    assert claim.payload["recent_git"]["capped_category_touches"] == {"src": 4}


def test_analysis_claims_extract_active_work_packages(tmp_path):
    (tmp_path / "active_work_packages.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "window":{"start":"2026-05-01","end":"2026-05-05"},
          "summary":{
            "top_work_packages":[
              {
                "work_package_id":"wp:sinex:pr:12",
                "project":"sinex",
                "label":"feat: replay",
                "unit_type":"github_thread",
                "commit_count":2,
                "durability_adjusted_scope":3.5,
                "refs":{"prs":[12],"issues":[]}
              }
            ]
          },
          "projects":[
            {
              "project":"sinex",
              "package_count":1,
              "commit_count":2,
              "packages":[
                {
                  "work_package_id":"wp:sinex:pr:12",
                  "project":"sinex",
                  "unit_type":"github_thread",
                  "unit_key":"pr#12",
                  "label":"feat: replay",
                  "status":"github_referenced",
                  "lifecycle":"landed_default_branch",
                  "confidence":0.9,
                  "first_date":"2026-05-01",
                  "last_date":"2026-05-02",
                  "commit_count":2,
                  "commit_shas":["a","b"],
                  "top_surfaces":["src"],
                  "scope_geom":4.0,
                  "durability_adjusted_scope":3.5,
                  "refs":{"prs":[12],"issues":[]},
                  "caveats":[]
                }
              ]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    claims = analysis_claims(projects=("sinex",), root=tmp_path)
    by_type = {claim.claim_type: claim for claim in claims}

    assert by_type["work_package_summary"].payload["package_count"] == 1
    assert by_type["work_package"].payload["work_package_id"] == "wp:sinex:pr:12"
    assert "github_thread" in by_type["work_package"].summary


def test_analysis_claims_extract_python_dependency_observed_imports(tmp_path):
    (tmp_path / "active_python_dependency_hygiene.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "window":{"start":"2026-05-01","end":"2026-05-05"},
          "projects":[
            {
              "project":"sinity-lynchpin",
              "manifest":"pyproject.toml",
              "observed_external_import_count":2,
              "observed_external_imports":["duckdb","requests"],
              "audit":{
                "available":true,
                "advisories":[
                  {"id":"GHSA-demo","package":"requests","direct":true,"transitive":false,"observed_import":true},
                  {"id":"GHSA-transitive","package":"urllib3","direct":false,"transitive":true,"observed_import":false}
                ]
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    claim = analysis_claims(projects=("sinity-lynchpin",), root=tmp_path)[0]

    assert claim.id == "python-dep-hygiene-summary:sinity-lynchpin"
    assert "1 observed in imports" in claim.summary
    assert claim.payload["observed_advisory_count"] == 1
    assert claim.payload["observed_external_import_count"] == 2
    assert claim.payload["observed_external_imports_sample"] == ["duckdb", "requests"]


def test_analysis_claims_extract_project_velocity_windows(tmp_path):
    (tmp_path / "project_velocity_windows.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "window":{"start":"2026-05-01","end":"2026-05-05"},
          "projects":[
            {
              "project":"sinex",
              "micro_effort":{"commit_count":3},
              "meso_delivery":{
                "landed_package_count":2,
                "github_thread_package_count":1,
                "heuristic_package_count":1,
                "total_durability_adjusted_scope":4.5,
                "top_packages":[]
              },
              "cross_source_support":{"cross_source_days":2},
              "interpretation_signals":{"support_level":"strong"},
              "caveats":[]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    claim = next(
        claim
        for claim in analysis_claims(projects=("sinex",), root=tmp_path)
        if claim.claim_type == "project_velocity_window"
    )

    assert "strong velocity-window support" in claim.summary
    assert claim.payload["meso_delivery"]["landed_package_count"] == 2


def test_analysis_claims_describe_native_python_complexity(tmp_path):
    (tmp_path / "active_python_complexity.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "window":{"start":"2026-05-01","end":"2026-05-05"},
          "projects":[
            {
              "project":"sinity-lynchpin",
              "file_count":3,
              "tool_run":{"native_ast":{"available":true,"parser":"ast"}},
              "parse_errors":[{"path":"bad.py","error":"invalid syntax"}],
              "summary":{
                "total_loc":120,
                "total_functions":9,
                "complex_functions":2,
                "avg_mi":null,
                "rank_distribution":{"A":7,"C":2}
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    claims = analysis_claims(projects=("sinity-lynchpin",), root=tmp_path)
    by_type = {claim.claim_type: claim for claim in claims}

    assert "native AST" in by_type["python_complexity_summary"].summary
    assert "MI unavailable" not in by_type["python_complexity_summary"].summary
    assert by_type["python_complexity_summary"].payload["parse_error_count"] == 1
    assert by_type["python_complexity_summary"].payload["methodology"] == "native AST decision-count approximation"
    assert "native AST decision-count" in by_type["function_complexity"].summary


def test_analysis_claims_describe_native_python_import_graph(tmp_path):
    (tmp_path / "active_python_import_graph.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "projects":[
            {
              "project":"sinity-lynchpin",
              "module_count":4,
              "import_edge_count":5,
              "cycle_modules":["lynchpin.a","lynchpin.b"],
              "parse_errors":[{"module":"bad","error":"invalid syntax"}],
              "top_fan_out":[],
              "top_fan_in":[],
              "tool_run":{"native_ast":{"available":true,"parser":"ast"}}
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    claims = analysis_claims(projects=("sinity-lynchpin",), root=tmp_path)
    by_type = {claim.claim_type: claim for claim in claims}

    assert "4 modules, 5 import edges, 2 cycle modules, 1 parse errors" in by_type["python_import_graph_summary"].summary
    assert by_type["python_import_graph_summary"].payload["parse_error_count"] == 1
    assert by_type["python_import_graph_summary"].payload["methodology"] == "native AST internal import graph"
    assert "python_import_graph_unavailable" not in by_type


def test_analysis_claims_extract_machine_attribution_claims(tmp_path):
    (tmp_path / "machine_attribution_claims.json").write_text(
        """
        {
          "generated_at_utc":"2026-05-06T12:00:00+00:00",
          "claim_count":1,
          "claims":[
            {
              "claim_id":"claim1",
              "claim_type":"machine_attribution",
              "project":"sinex",
              "support_level":"insufficient",
              "confidence":0.9,
              "summary":"Refuse causal claim",
              "payload":{"metric":"stage.duration_s"}
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    claims = analysis_claims(projects=("sinex",), root=tmp_path)

    assert len(claims) == 1
    assert claims[0].id == "claim1"
    assert claims[0].claim_type == "machine_attribution"
    assert claims[0].payload["metric"] == "stage.duration_s"
