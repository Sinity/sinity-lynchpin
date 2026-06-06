import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.sources.github import (
    GitHubActor,
    GitHubComment,
    GitHubItem,
    GitHubReview,
    GitHubReviewComment,
)
from lynchpin.sources import github_context
from lynchpin.sources.github_context import (
    iter_github_context,
    github_item_from_payload,
    github_item_to_payload,
)


def test_github_context_roundtrips_reviews_and_review_comments() -> None:
    item = GitHubItem(
        repo="lynchpin",
        slug="Sinity/lynchpin",
        kind="pr",
        number=42,
        title="fix: review topology",
        state="merged",
        url="https://github.com/Sinity/lynchpin/pull/42",
        author=GitHubActor("Sinity"),
        labels=(),
        body="",
        comments=(GitHubComment(GitHubActor("reviewer"), "top-level", datetime(2026, 6, 1, tzinfo=timezone.utc)),),
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        closed_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        merged_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        review_decision="APPROVED",
        reviews=(
            GitHubReview(
                author=GitHubActor("reviewer"),
                state="CHANGES_REQUESTED",
                body="please simplify",
                submitted_at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
                url="https://github.com/Sinity/lynchpin/pull/42#pullrequestreview-1",
            ),
        ),
        latest_reviews=(
            GitHubReview(
                author=GitHubActor("reviewer"),
                state="APPROVED",
                body="ok",
                submitted_at=datetime(2026, 6, 2, 12, tzinfo=timezone.utc),
            ),
        ),
        review_comments=(
            GitHubReviewComment(
                author=GitHubActor("reviewer"),
                body="inline",
                path="lynchpin/materialization.py",
                line=17,
                diff_hunk="@@",
                created_at=datetime(2026, 6, 1, 13, tzinfo=timezone.utc),
                review_id=123,
            ),
        ),
    )

    payload = json.loads(json.dumps(github_item_to_payload(project="lynchpin", item=item)))
    roundtrip = github_item_from_payload(payload)

    assert roundtrip is not None
    assert roundtrip.reviews[0].state == "CHANGES_REQUESTED"
    assert roundtrip.latest_reviews[0].state == "APPROVED"
    assert roundtrip.review_comments[0].path == "lynchpin/materialization.py"
    assert roundtrip.review_comments[0].review_id == 123


def test_github_context_default_reader_materializes(tmp_path, monkeypatch) -> None:
    calls = []
    derived = tmp_path / "derived"
    product = derived / "github/context.ndjson"
    product.parent.mkdir(parents=True)
    product.write_text(
        json.dumps(
            {
                "project": "lynchpin",
                "repo": "lynchpin",
                "slug": "Sinity/lynchpin",
                "kind": "issue",
                "number": 42,
                "title": "track materialized context",
                "state": "open",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_ensure(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(github_context, "get_config", lambda: SimpleNamespace(derived_root=derived))
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)

    rows = list(iter_github_context(projects={"lynchpin"}))

    assert calls == [("github_context", None)]
    assert [(row.project, row.item.kind, row.item.number) for row in rows] == [
        ("lynchpin", "issue", 42)
    ]


def test_github_context_default_reader_forwards_window(tmp_path, monkeypatch) -> None:
    calls = []
    derived = tmp_path / "derived"
    product = derived / "github/context.ndjson"
    product.parent.mkdir(parents=True)
    product.write_text("", encoding="utf-8")

    def fake_ensure(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(github_context, "get_config", lambda: SimpleNamespace(derived_root=derived))
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)

    rows = list(iter_github_context(window=(date(2026, 6, 1), date(2026, 6, 3))))

    assert rows == []
    assert calls == [("github_context", (date(2026, 6, 1), date(2026, 6, 3)))]


def test_github_context_reader_can_skip_prior_ensure(tmp_path, monkeypatch) -> None:
    derived = tmp_path / "derived"
    product = derived / "github/context.ndjson"
    product.parent.mkdir(parents=True)
    product.write_text("", encoding="utf-8")

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("caller already performed the windowed ensure")

    monkeypatch.setattr(github_context, "get_config", lambda: SimpleNamespace(derived_root=derived))
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fail_ensure)

    assert list(iter_github_context(ensure=False)) == []


def test_github_context_explicit_path_does_not_materialize(tmp_path, monkeypatch) -> None:
    product = tmp_path / "context.ndjson"
    product.write_text(
        json.dumps(
            {
                "project": "lynchpin",
                "repo": "lynchpin",
                "kind": "pr",
                "number": 7,
                "title": "explicit fixture",
                "state": "merged",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("explicit path reads must not materialize")

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fail_ensure)

    rows = list(iter_github_context(product))

    assert [(row.project, row.item.kind, row.item.number) for row in rows] == [
        ("lynchpin", "pr", 7)
    ]
