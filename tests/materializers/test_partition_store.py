from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from lynchpin.materializers.partition_store import (
    ArtifactStore,
    ProductPartitionKey,
    deterministic_input_digest,
)


def test_identical_input_reuses_exact_artifact_without_rewrite(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    key = ProductPartitionKey.day("events", "2026-08-25")
    first = store.put(key, b'{"a":1}\n', format="ndjson", input_digest=deterministic_input_digest([b"input"]))
    artifact = tmp_path / "store" / first.path
    original_stat = artifact.stat()
    second = store.put(key, b'{"a":1}\n', format="ndjson", input_digest=first.input_digest)
    assert second.digest == first.digest
    assert second.path == first.path
    assert artifact.stat().st_ino == original_stat.st_ino
    assert artifact.stat().st_mtime_ns == original_stat.st_mtime_ns


def test_changed_input_creates_new_immutable_path(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    key = ProductPartitionKey.singleton("summary")
    first = store.put(key, b"old", format="ndjson")
    second = store.put(key, b"new", format="ndjson")
    assert first.path != second.path
    assert (tmp_path / "store" / first.path).read_bytes() == b"old"
    assert (tmp_path / "store" / second.path).read_bytes() == b"new"


def test_relation_like_input_defaults_to_parquet(tmp_path: Path) -> None:
    class RelationLike:
        def write_parquet(self, path: str) -> None:
            Path(path).write_bytes(b"PARQUET-FIXTURE")

    ref = ArtifactStore(tmp_path / "store").put(ProductPartitionKey.singleton("events"), RelationLike())
    assert ref.format == "parquet"
    assert (tmp_path / "store" / ref.path).read_bytes() == b"PARQUET-FIXTURE"


def test_interrupted_write_exposes_no_partial_artifact_or_manifest(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")

    def interrupted(output: object) -> None:
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        store.put(ProductPartitionKey.month("events", "2026-08"), interrupted, format="ndjson")
    assert not (tmp_path / "store" / "manifest.json").exists()
    assert list((tmp_path / "store" / ".staging").iterdir()) == []
    assert not list((tmp_path / "store").glob("artifacts/**/*.ndjson"))


def test_interrupted_manifest_publish_keeps_previous_selection_readable(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.materializers import partition_store

    store = ArtifactStore(tmp_path / "store")
    key = ProductPartitionKey.day("events", "2026-08-25")
    old = store.put(key, b"old", format="ndjson")

    def fail_publish(*_args: object, **_kwargs: object) -> None:
        raise OSError("manifest fsync failed")

    monkeypatch.setattr(partition_store, "_atomic_json", fail_publish)
    with pytest.raises(OSError, match="manifest fsync failed"):
        store.put(key, b"new", format="ndjson")

    selected = store.logical_partitions()[key]
    assert selected.path == old.path
    assert store.read(selected) == b"old"


def test_manifest_serialization_is_deterministic(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    created = datetime(2026, 8, 25, tzinfo=timezone.utc)
    one = store.put(ProductPartitionKey.entity("events", "alice"), b"a", format="ndjson")
    store.update_manifest(replace(one, created_at=created))
    first_bytes = store.manifest_path.read_bytes()
    store.update_manifest(replace(one, created_at=created))
    assert store.manifest_path.read_bytes() == first_bytes


def test_manifest_records_metadata_and_gc_respects_referenced_generation(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    key = ProductPartitionKey.day("events", "2026-01-01")
    referenced = replace(
        store.put(
            key,
            b"referenced",
            format="ndjson",
            row_count=2,
            first_date=date(2026, 1, 1),
            last_date=date(2026, 1, 1),
            generations=["gen-a"],
        ),
        created_at=old,
    )
    unreferenced = replace(store.put(ProductPartitionKey.day("events", "2026-01-02"), b"unused", format="ndjson"), created_at=old)
    store.update_manifest(referenced)
    store.update_manifest(unreferenced)
    candidates = store.plan_gc(["gen-a"], now=datetime(2026, 8, 25, tzinfo=timezone.utc), grace_period=timedelta(days=7))
    assert [item.path for item in candidates] == [unreferenced.path]
    assert referenced not in candidates


def test_partition_keys_reject_path_traversal() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        ProductPartitionKey.entity("events", "../outside")
    with pytest.raises(ValueError, match="unsafe"):
        ProductPartitionKey("../outside", "day", "2026-08-25")


def test_input_digest_is_order_and_type_sensitive() -> None:
    assert deterministic_input_digest(["a", "b"]) != deterministic_input_digest(["b", "a"])
    assert deterministic_input_digest([b"a"]) != deterministic_input_digest(["a"])
