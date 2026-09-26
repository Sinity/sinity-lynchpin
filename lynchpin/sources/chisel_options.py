"""Versioned, immutable inputs shared by Chisel's entrypoints."""

from dataclasses import dataclass

DEFAULT_PROJECTS = ("sinex", "sinnix", "polylogue", "sinity-lynchpin")
DATASETS = ("source", "history", "trackers", "structure", "metrics", "execution", "context")
PROFILES = {"source": ("source", "structure", "metrics"), "review": DATASETS, "evidence": DATASETS}


@dataclass(frozen=True)
class BuildOptions:
    schema_version: int = 1
    refresh: bool = False
    target: str = "default"
    refs: tuple[tuple[str, str], ...] = ()
    task_roots: tuple[tuple[str, str], ...] = ()
    datasets: tuple[str, ...] = DATASETS
    context_days: int = 30
    context_limit: int = 200
    context_bytes: int = 2_000_000
    attachment_bytes: int = 500_000_000
    attachment_layout: str = "auto"
    events: str | None = None
    xml: bool = False
    sqlite: bool = False

    def __post_init__(self) -> None:
        if self.target not in {"default", "worktree"}:
            raise ValueError("target must be default or worktree")
        if min(self.context_days, self.context_limit, self.context_bytes, self.attachment_bytes) <= 0:
            raise ValueError("context and attachment limits must be positive")
        if set(self.datasets) - set(DATASETS):
            raise ValueError("unknown dataset")


# The build lock in chisel serializes use of this process-wide configuration.
active_options = BuildOptions()
