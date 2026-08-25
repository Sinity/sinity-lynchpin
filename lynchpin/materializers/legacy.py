"""Description-only adapter for the current procedural materializer registry."""

from __future__ import annotations

from .specs import ArtifactRef, ProductSpec


def describe_existing_materializers() -> tuple[ProductSpec, ...]:
    from .. import materialization

    return tuple(
        ProductSpec(
            product=name,
            version="procedural-v1",
            handler=f"lynchpin.materialization:{name}",
            input_generation="legacy-registry",
            output=ArtifactRef(name, "canonical-product", name, "procedural-v1"),
        )
        for name in sorted(materialization._materializers())
    )
