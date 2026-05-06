"""Tests for core/classify.py: mode, project, topic attribution."""

from lynchpin.core.classify import classify, resolve_project, extract_topics


class TestClassify:
    def test_coding_editor(self):
        a = classify(app="kitty", title="nvim /realm/project/sinex/src/main.rs", source="activitywatch.window")
        assert a.mode == "coding"
        assert a.project == "sinex"

    def test_afk(self):
        a = classify(kind="afk")
        assert a.mode == "recovery"

    def test_git_commit(self):
        a = classify(source="git.commit")
        assert a.mode == "coding"

    def test_research_domain(self):
        a = classify(domain="github.com", app="firefox")
        assert a.mode == "research"

    def test_ai_chat(self):
        a = classify(domain="claude.ai", app="firefox")
        assert a.mode == "chat"

    def test_media(self):
        a = classify(domain="youtube.com", app="firefox")
        assert a.mode == "media"

    def test_social(self):
        a = classify(domain="reddit.com", app="firefox")
        assert a.mode == "social"

    def test_writing_app(self):
        a = classify(app="obsidian", title="journal note")
        assert a.mode == "writing"

    def test_unknown_fallback(self):
        a = classify()
        assert a.mode == "unknown"

    def test_polylogue_work_event(self):
        a = classify(source="polylogue.session", evidence={"work_event_kind": "implementation"})
        assert a.mode == "coding"


class TestResolveProject:
    def test_path(self):
        assert resolve_project("/realm/project/sinex/src/main.rs") == "sinex"

    def test_target_vision_path(self):
        assert resolve_project("/realm/project/sinex-target-vision/README.md") == "sinex-target-vision"

    def test_inactive_path_is_not_current_project(self):
        assert resolve_project("/realm/project/_inactive/codex") is None

    def test_none(self):
        assert resolve_project("/tmp/random") is None

    def test_text(self):
        assert resolve_project(None, "working on sinity-lynchpin") == "sinity-lynchpin"


class TestTopics:
    def test_rust(self):
        topics = extract_topics("building rust cargo with tokio")
        assert any(t == "rust" for t, _ in topics)

    def test_nix(self):
        topics = extract_topics("flake nixos home-manager module")
        assert any(t == "nix" for t, _ in topics)

    def test_empty(self):
        assert extract_topics("") == []

