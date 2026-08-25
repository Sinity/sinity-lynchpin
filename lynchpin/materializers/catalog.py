"""Canonical source-product catalog for production convergence."""

from __future__ import annotations

from .handlers import source_handler_definitions
from .specs import ArtifactRef, Dependency, ProductSpec, ResourceHints


_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "activitywatch_event_index": ("activitywatch",),
    "activitywatch_derived": ("activitywatch", "activitywatch_event_index"),
    "activity_content": ("activitywatch_derived", "title_metadata"),
    "personal_daily_signals": ("activity_content", "activitywatch_derived", "title_metadata"),
    "temporal_signals": ("activitywatch_derived",),
}
_CANONICAL_ONLY = frozenset(
    {"activitywatch_event_index", "activitywatch_derived", "activity_content", "personal_daily_signals", "temporal_signals", "sleep_productivity"}
)
_WINDOWLESS = frozenset(
    {"google_takeout", "title_metadata", "spotify", "reddit", "facebook_messenger", "communications", "raindrop", "browser_bookmarks", "arbtt", "health_coverage", "code_snapshots", "polylogue_verify_runs", "ambient_intelligence"}
)


def _spec(name: str) -> ProductSpec:
    window_policy = "unbounded" if name in _WINDOWLESS else "bounded"
    raw_permission = "none" if name in _CANONICAL_ONLY else "owner-native"
    reads = tuple(f"canonical-product:{dependency}" for dependency in _DEPENDENCIES.get(name, ()))
    if raw_permission != "none":
        reads = (*reads, f"owner-native:{name}")
    return ProductSpec(
        product=name,
        version="typed-source-v1",
        handler=f"source:{name}",
        input_generation="source-contract-v1",
        output=ArtifactRef(name, "canonical-product", name, "typed-source-v1"),
        dependencies=tuple(Dependency(item) for item in _DEPENDENCIES.get(name, ())),
        payload={"product": name},
        raw_read_permission=raw_permission,
        phase="source-materialize",
        resources=ResourceHints(
            reads=reads,
            writes=(f"canonical-product:{name}",),
            exclusive=(f"canonical-product:{name}",),
        ),
        window_policy=window_policy,
    )


_HANDLERS = source_handler_definitions()
PRODUCT_SPECS: tuple[ProductSpec, ...] = tuple(_spec(name.removeprefix("source:")) for name in sorted(_HANDLERS))
PRODUCT_CATALOG: dict[str, ProductSpec] = {spec.product: spec for spec in PRODUCT_SPECS}


def product_catalog() -> tuple[ProductSpec, ...]:
    """Return the deterministic typed catalog."""

    return PRODUCT_SPECS


def handler_registry():
    """Return the code-owned source handler registry."""

    from .executor import ClosedHandlerRegistry

    return ClosedHandlerRegistry(_HANDLERS)


__all__ = ["PRODUCT_CATALOG", "PRODUCT_SPECS", "handler_registry", "product_catalog"]
