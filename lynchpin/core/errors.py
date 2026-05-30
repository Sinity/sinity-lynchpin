"""Typed exception taxonomy for Lynchpin failure modes.

This module defines the base exception hierarchy and the concrete typed errors
that cover the recurring failure categories across the codebase. Callers should
raise these instead of bare ``ValueError``/``RuntimeError`` where the failure
maps to a known category.

Currently, ~14 bare ``ValueError``/``RuntimeError`` raises exist across the
codebase (see the audit note below). This module provides the taxonomy; adoption
of these types at the raise sites is a separate migration wave.

No imports from other Lynchpin modules — this is a zero-dependency foundation.

Coverage-bound errors pair with the bounds contract in ``core/coverage.py``
(``DataCoverageError``); import that module at the raise site, not here.
"""

from __future__ import annotations


# ── Base ──────────────────────────────────────────────────────────────────────


class LynchpinError(Exception):
    """Base class for all Lynchpin domain exceptions.

    Catch this to handle any Lynchpin-specific failure without depending on a
    particular subclass. Subclasses carry structured attributes for programmatic
    inspection; the string representation always includes all relevant context.
    """


# ── Source availability ───────────────────────────────────────────────────────


class SourceUnavailableError(LynchpinError):
    """A data source's files or database are missing or unreadable.

    Raised when a source module cannot locate or open its backing store. This
    is the typed replacement for bare ``RuntimeError``/``FileNotFoundError``
    raises that currently guard source availability checks.

    Degraded-source handling in ``analysis/readiness`` and the MCP surface
    currently relies on string matching against exception messages; migrating
    those callers to catch this type will eliminate that fragility.

    Attributes:
        source: Logical source name, e.g. ``"polylogue"``, ``"activitywatch"``.
        path:   Filesystem path that was missing/unreadable, if applicable.
        reason: Human-readable explanation of why the source is unavailable.
    """

    def __init__(
        self,
        source: str,
        *,
        path: str | None = None,
        reason: str = "",
    ) -> None:
        self.source = source
        self.path = path
        self.reason = reason
        parts = [f"source={source!r}"]
        if path:
            parts.append(f"path={path!r}")
        if reason:
            parts.append(reason)
        super().__init__(", ".join(parts))


# ── Schema / version ──────────────────────────────────────────────────────────


class SchemaVersionError(LynchpinError):
    """On-disk schema or version tag does not match the expected contract.

    Raised when a substrate migration, SQLite telemetry table, or JSONL product
    carries an incompatible schema version. Subsumes the role of
    ``MachineTelemetrySchemaError`` (currently in
    ``sources/machine_models.py``) and ``sinnix_runtime_inventory``'s inline
    schema-string guards.

    Attributes:
        found:    The schema version or tag found on disk.
        expected: The schema version or tag this code requires.
        source:   Logical source or product name, for context.
    """

    def __init__(
        self,
        *,
        found: object,
        expected: object,
        source: str = "",
    ) -> None:
        self.found = found
        self.expected = expected
        self.source = source
        detail = f"schema mismatch: found={found!r}, expected={expected!r}"
        if source:
            detail = f"{source}: {detail}"
        super().__init__(detail)


# ── Materialization ───────────────────────────────────────────────────────────


class MaterializationError(LynchpinError):
    """A derived product failed to materialize.

    Raised when a pipeline step, substrate promotion, or insight product cannot
    be produced. This is the taxonomy-level base for more specific materialization
    failures; ``PolylogueMaterializationError`` (in ``sources/polylogue.py``)
    is the pre-existing concrete variant and should be migrated to inherit from
    this class in a future wave.

    Attributes:
        product: Name of the product or pipeline step that failed.
        reason:  Human-readable explanation of the failure.
    """

    def __init__(
        self,
        product: str,
        *,
        reason: str = "",
    ) -> None:
        self.product = product
        self.reason = reason
        detail = f"product={product!r}"
        if reason:
            detail = f"{detail}: {reason}"
        super().__init__(detail)


# ── Coverage bounds ───────────────────────────────────────────────────────────


class DataCoverageError(LynchpinError):
    """Requested date range falls outside a source's observed coverage.

    Raised when a caller asks for data that precedes or follows the bounds
    established by the source. This pairs with the coverage-bounds contract in
    ``core/coverage.py``; import and raise from there — do not import
    ``core/coverage`` here to keep this module dependency-free.

    Attributes:
        source:      Logical source name, e.g. ``"sleep"``, ``"spotify"``.
        requested:   A string representation of the requested range.
        available:   A string representation of the available range, if known.
    """

    def __init__(
        self,
        source: str,
        *,
        requested: str = "",
        available: str = "",
    ) -> None:
        self.source = source
        self.requested = requested
        self.available = available
        parts = [f"source={source!r}"]
        if requested:
            parts.append(f"requested={requested!r}")
        if available:
            parts.append(f"available={available!r}")
        super().__init__(", ".join(parts))


__all__ = [
    "LynchpinError",
    "SourceUnavailableError",
    "SchemaVersionError",
    "MaterializationError",
    "DataCoverageError",
]
