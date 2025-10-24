from lynchpin.core.project_mentions import projects_mentioned_in_text

from lynchpin.core.projects import canonical_project_name, project_path, project_profiles


def test_canonical_project_name_accepts_known_active_paths():
    assert canonical_project_name("/realm/project/sinex-target-vision/README.md") == "sinex-target-vision"
    assert canonical_project_name("https://github.com/Sinity/polylogue.git") == "polylogue"


def test_canonical_project_name_rejects_non_project_fragments():
    assert canonical_project_name("/realm/project/_inactive/codex") is None
    assert canonical_project_name("/tmp/conversations-001-37") is None
    assert canonical_project_name("remote-agent-rhs1qtc6t.meta") is None


def test_canonical_project_name_maps_worktree_and_substrate_aliases():
    assert canonical_project_name("/tmp/polylogue-rebuild") == "polylogue"
    assert canonical_project_name("__lynchpin_exported") == "sinity-lynchpin"


def test_project_mentions_cover_current_text_evidence_aliases():
    assert projects_mentioned_in_text("polylogue and raw-log analysis") == ("knowledgebase", "polylogue")
    assert projects_mentioned_in_text("target vision and lynchpin narrativization") == (
        "sinex-target-vision",
        "sinity-lynchpin",
    )
    assert projects_mentioned_in_text("sinexical phrasing should not count") == ()


def test_project_profiles_use_registered_paths():
    profiles = project_profiles()

    assert profiles["sinex"].path == project_path("sinex")
    assert "legacy-project" not in profiles
