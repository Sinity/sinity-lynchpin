from pathlib import Path

from lynchpin.analysis.projects.velocity_analysis import (
    CategoryStats,
    CommitEvent,
    DailyStats,
    ProjectStats,
)
from lynchpin.analysis.projects.velocity_payload import build_velocity_dashboard_payload
from lynchpin.core.projects import ProjectProfile


def _profile(name: str) -> ProjectProfile:
    return ProjectProfile(
        name=name,
        path=Path("/tmp") / name,
        classify=lambda path: "code",
        categories=("code",),
        colors={"code": "#123456"},
    )


def test_velocity_payload_author_counts_are_project_local() -> None:
    first = ProjectStats(
        name="first",
        daily={
            "2026-05-01": DailyStats(
                date="2026-05-01",
                by_category={"code": CategoryStats(added=10, removed=1)},
                commits=(
                    [
                        CommitEvent(
                            hash="a1",
                            date="2026-05-01",
                            author="Ada",
                            message="feat: one",
                            by_category={"code": CategoryStats(added=10, removed=1)},
                        ),
                        CommitEvent(
                            hash="b1",
                            date="2026-05-01",
                            author="Bea",
                            message="fix: two",
                            by_category={"code": CategoryStats(added=1, removed=0)},
                        ),
                    ]
                ),
            )
        },
    )
    second = ProjectStats(
        name="second",
        daily={
            "2026-05-01": DailyStats(
                date="2026-05-01",
                by_category={"code": CategoryStats(added=5, removed=0)},
                commits=[
                    CommitEvent(
                        hash="c1",
                        date="2026-05-01",
                        author="Cy",
                        message="feat: one",
                        by_category={"code": CategoryStats(added=5, removed=0)},
                    )
                ],
            )
        },
    )

    payload = build_velocity_dashboard_payload(
        {"first": first, "second": second},
        {"first": _profile("first"), "second": _profile("second")},
        generated_at="2026-05-16T00:00:00",
    )

    assert payload is not None
    assert payload["projectSummaries"]["first"]["authorCount"] == 2
    assert payload["projectSummaries"]["second"]["authorCount"] == 1


def test_velocity_payload_returns_none_without_dates() -> None:
    assert build_velocity_dashboard_payload({}, {}, generated_at="fixed") is None
