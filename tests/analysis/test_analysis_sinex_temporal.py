from lynchpin.analysis.sinex.temporal import _classify_commit_type, _top_area


def test_classify_commit_type_handles_conventional_and_fallback_subjects() -> None:
    assert _classify_commit_type("feat(cli): add replay dashboard") == "feat"
    assert _classify_commit_type("Fix invalid state handling in replay") == "fix"
    assert _classify_commit_type("merge branch 'feature/x'") == "merge"
    assert _classify_commit_type("prepare replay control hardening") == "other"


def test_top_area_extracts_first_path_segment() -> None:
    assert _top_area("crate/core/sinex-gateway/src/lib.rs") == "crate"
    assert _top_area("xtask/src/main.rs") == "xtask"
    assert _top_area("README.md") == "(root)"
