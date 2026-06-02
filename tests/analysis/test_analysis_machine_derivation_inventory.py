from __future__ import annotations

from pathlib import Path


def test_derivation_inventory_evaluates_flake_outputs_without_building(tmp_path):
    from lynchpin.analysis.machine.derivation_inventory import analyze_machine_derivation_inventory

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "flake.nix").write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    def evaluator(argv: list[str], cwd: Path) -> str:
        calls.append(argv)
        if "--json" in argv:
            return (
                '{"default":{"drvPath":"/nix/store/default.drv","outPath":"/nix/store/default"},'
                '"xtask":{"drvPath":"/nix/store/demo-xtask.drv","outPath":"/nix/store/demo-xtask"}}'
            )
        raise AssertionError(argv)

    analysis = analyze_machine_derivation_inventory(
        roots=(("sinex", repo),),
        system="x86_64-linux",
        evaluator=evaluator,
    )

    assert analysis.target_count == 1
    assert analysis.ready_target_count == 1
    assert analysis.targets[0].attr == "xtask"
    assert analysis.targets[0].drv_path == "/nix/store/demo-xtask.drv"
    assert all("build" not in call for call in calls)
    assert len(calls) == 1
    assert calls[0][3] == f"{repo}#packages.x86_64-linux"


def test_derivation_inventory_falls_back_to_per_attr_eval(tmp_path):
    from subprocess import CalledProcessError

    from lynchpin.analysis.machine.derivation_inventory import analyze_machine_derivation_inventory

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "flake.nix").write_text("{}", encoding="utf-8")

    def evaluator(argv: list[str], cwd: Path) -> str:
        if "--apply" in argv and "mapAttrs" in argv[-1]:
            raise CalledProcessError(1, argv)
        if "--json" in argv:
            return '["default","xtask"]'
        if argv[-1].endswith(".drvPath"):
            return "/nix/store/demo-xtask.drv\n"
        if argv[-1].endswith(".outPath"):
            return "/nix/store/demo-xtask\n"
        raise AssertionError(argv)

    analysis = analyze_machine_derivation_inventory(
        roots=(("sinex", repo),),
        system="x86_64-linux",
        evaluator=evaluator,
    )

    assert analysis.ready_target_count == 1
    assert analysis.targets[0].drv_path == "/nix/store/demo-xtask.drv"


def test_write_derivation_inventory_reuses_matching_artifact(monkeypatch, tmp_path):
    import lynchpin.analysis.machine.derivation_inventory as inv

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "flake.nix").write_text("{}", encoding="utf-8")
    out = tmp_path / "inventory.json"
    calls = 0

    def evaluator(argv: list[str], cwd: Path) -> str:
        nonlocal calls
        calls += 1
        return '{"xtask":{"drvPath":"/nix/store/demo-xtask.drv","outPath":"/nix/store/demo-xtask"}}'

    monkeypatch.setattr(inv, "_nix_eval", evaluator)

    first = inv.write_machine_derivation_inventory(
        out,
        roots=(("sinex", repo),),
        system="x86_64-linux",
    )
    second = inv.write_machine_derivation_inventory(
        out,
        roots=(("sinex", repo),),
        system="x86_64-linux",
    )

    assert calls == 1
    assert first.ready_target_count == 1
    assert second.ready_target_count == 1
    assert second.targets[0].drv_path == "/nix/store/demo-xtask.drv"


def test_write_derivation_inventory_cache_ignores_dirty_worktree_when_head_fixed(monkeypatch, tmp_path):
    import lynchpin.analysis.machine.derivation_inventory as inv

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "flake.nix").write_text("{}", encoding="utf-8")
    out = tmp_path / "inventory.json"
    calls = 0
    dirty_state = " M README.md"

    def evaluator(argv: list[str], cwd: Path) -> str:
        nonlocal calls
        calls += 1
        assert argv[3].startswith("git+file://")
        assert "?rev=abc123" in argv[3]
        return '{"xtask":{"drvPath":"/nix/store/demo-xtask.drv","outPath":"/nix/store/demo-xtask"}}'

    def fake_git(root: Path, *args: str) -> str | None:
        if args == ("rev-parse", "HEAD"):
            return "abc123"
        if args == ("status", "--porcelain"):
            return dirty_state
        return None

    monkeypatch.setattr(inv, "_nix_eval", evaluator)
    monkeypatch.setattr(inv, "_git", fake_git)

    first = inv.write_machine_derivation_inventory(
        out,
        roots=(("sinex", repo),),
        system="x86_64-linux",
    )
    dirty_state = " M README.md\n M docs/note.md"
    second = inv.write_machine_derivation_inventory(
        out,
        roots=(("sinex", repo),),
        system="x86_64-linux",
    )

    assert calls == 1
    assert first.ready_target_count == 1
    assert second.ready_target_count == 1
    assert "dirty worktree paths are excluded" in first.caveats[0]


def test_derivations_from_inventory_filters_ready_project_targets() -> None:
    from lynchpin.analysis.machine.derivation_inventory import derivations_from_inventory

    rows = derivations_from_inventory(
        {
            "targets": [
                {"project": "sinex", "attr": "xtask", "drv_path": "/nix/store/a.drv", "eval_status": "ready"},
                {"project": "other", "attr": "demo", "drv_path": "/nix/store/b.drv", "eval_status": "ready"},
                {"project": "sinex", "attr": "broken", "drv_path": None, "eval_status": "eval_error"},
            ]
        },
        project="sinex",
    )

    assert rows == ({
        "project": "sinex",
        "name": "xtask",
        "drv_path": "/nix/store/a.drv",
        "store_path": None,
        "flake_ref": None,
    },)


def test_default_derivation_roots_include_polylogue_project(monkeypatch, tmp_path) -> None:
    import lynchpin.analysis.machine.derivation_inventory as inv

    polylogue_root = tmp_path / "polylogue"
    lynchpin_root = tmp_path / "lynchpin"
    monkeypatch.setattr(
        inv,
        "get_config",
        lambda: type(
            "Cfg",
            (),
            {"polylogue_project_root": polylogue_root, "repo_root": lynchpin_root},
        )(),
    )

    assert inv._default_roots() == (
        ("sinex", inv.Path("/realm/project/sinex")),
        ("polylogue", polylogue_root),
        ("sinity-lynchpin", lynchpin_root),
    )


def test_derivations_for_candidate_selects_polylogue_targets() -> None:
    from lynchpin.analysis.machine.derivation_inventory import derivations_for_candidate

    rows = derivations_for_candidate(
        {
            "targets": [
                {"project": "polylogue", "attr": "polylogue", "drv_path": "/nix/store/poly.drv", "eval_status": "ready"},
                {"project": "polylogue", "attr": "api-python", "drv_path": "/nix/store/api.drv", "eval_status": "ready"},
                {"project": "sinex", "attr": "xtask", "drv_path": "/nix/store/xtask.drv", "eval_status": "ready"},
            ]
        },
        {
            "project": "polylogue",
            "metric": "work.failure_count",
            "suggested_benchmark_manifest": {"workload": "invocation:polylogue"},
        },
    )

    assert [row["name"] for row in rows] == ["polylogue", "api-python"]


def test_derivations_for_candidate_selects_workload_specific_target() -> None:
    from lynchpin.analysis.machine.derivation_inventory import derivations_for_candidate

    rows = derivations_for_candidate(
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
        {
            "project": "sinex",
            "metric": "stage.duration_s",
            "suggested_benchmark_manifest": {"workload": "xtask-stage:test"},
        },
    )

    assert rows == ({
        "project": "sinex",
        "name": "xtask",
        "drv_path": "/nix/store/xtask.drv",
        "store_path": None,
        "flake_ref": None,
    },)


def test_derivations_for_candidate_infers_project_from_xtask_workload() -> None:
    from lynchpin.analysis.machine.derivation_inventory import derivations_for_candidate

    rows = derivations_for_candidate(
        {
            "targets": [
                {"project": "sinex", "attr": "xtask", "drv_path": "/nix/store/xtask.drv", "eval_status": "ready"},
                {
                    "project": "sinity-lynchpin",
                    "attr": "lynchpin",
                    "drv_path": "/nix/store/lynchpin.drv",
                    "eval_status": "ready",
                },
            ]
        },
        {
            "metric": "xtask.stage.compile.duration_s",
            "suggested_benchmark_manifest": {"workload": "xtask-stage:compile"},
        },
    )

    assert [row["name"] for row in rows] == ["xtask"]
