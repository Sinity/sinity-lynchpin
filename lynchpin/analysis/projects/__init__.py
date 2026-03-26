"""Project-centric analysis and bundle-generation APIs."""

from .bundles import (
    BUNDLE_ROOT,
    DEFAULT_LOGS_COUNT,
    BundleArtifact,
    GitState,
    ProjectSpec,
    build_project_bundles,
)
from .velocity_analysis import (
    AGGREGATE_PROJECT,
    AuthorStats,
    CategoryStats,
    CommitEvent,
    DailyStats,
    ProjectStats,
    select_project_profiles,
)
from .velocity_renderer import DEFAULT_OUTPUT, build_velocity_dashboard
from .rich_bundles import (
    DEFAULT_PATCH_COMMITS,
    DEFAULT_PATCH_WINDOW,
    DEFAULT_RICH_PROJECTS,
    DEFAULT_SUMMARY_WINDOW,
    RICH_BUNDLE_ROOT,
    RichProjectPlan,
    SliceArtifact,
    SliceSpec,
    build_rich_project_bundles,
)

__all__ = [
    "AGGREGATE_PROJECT",
    "AuthorStats",
    "BUNDLE_ROOT",
    "BundleArtifact",
    "CategoryStats",
    "CommitEvent",
    "DEFAULT_LOGS_COUNT",
    "DEFAULT_PATCH_COMMITS",
    "DEFAULT_PATCH_WINDOW",
    "DEFAULT_RICH_PROJECTS",
    "DEFAULT_OUTPUT",
    "DEFAULT_SUMMARY_WINDOW",
    "DailyStats",
    "GitState",
    "ProjectSpec",
    "ProjectStats",
    "RICH_BUNDLE_ROOT",
    "RichProjectPlan",
    "SliceArtifact",
    "SliceSpec",
    "build_project_bundles",
    "build_rich_project_bundles",
    "build_velocity_dashboard",
    "select_project_profiles",
]
