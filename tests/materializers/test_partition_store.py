import hashlib
import json
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


def test_large_path_input_is_copied_and_hashed_without_a_whole_file_read(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "input.ndjson"
    payload = b"x" * (2 * 1_048_576 + 17)
    source.write_bytes(payload)

    def whole_file_read(_path: Path) -> bytes:
        raise AssertionError("artifact staging must use bounded reads")

    with monkeypatch.context() as context:
        context.setattr(Path, "read_bytes", whole_file_read)
        ref = ArtifactStore(tmp_path / "store").put(ProductPartitionKey.singleton("events"), source)

    assert ref.digest == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "store" / ref.path).read_bytes() == payload


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
    store.publish({key: old})

    def fail_publish(*_args: object, **_kwargs: object) -> None:
        raise OSError("manifest fsync failed")

    monkeypatch.setattr(partition_store, "_atomic_json", fail_publish)
    with pytest.raises(OSError, match="manifest fsync failed"):
        new = store.put(key, b"new", format="ndjson")
        store.publish({key: new})

    selected = store.logical_partitions()[key]
    assert selected.path == old.path
    assert store.read(selected) == b"old"


def test_selection_readability_rejects_a_missing_selected_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    ref = store.put(
        ProductPartitionKey.day("events", "2026-08-25"),
        b"selected",
        format="ndjson",
    )
    store.publish({ref.key: ref})
    assert store.selection_is_readable()

    (store.root / ref.path).unlink()

    assert not store.selection_is_readable()


def test_manifest_serialization_is_deterministic(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    created = datetime(2026, 8, 25, tzinfo=timezone.utc)
    one = store.put(ProductPartitionKey.entity("events", "alice"), b"a", format="ndjson")
    store.publish({one.key: replace(one, created_at=created)})
    first_bytes = store.manifest_path.read_bytes()
    store.publish({one.key: replace(one, created_at=created)})
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
    store.publish({referenced.key: referenced, unreferenced.key: unreferenced})
    store.publish({referenced.key: referenced})
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


def test_same_size_corruption_refuses_reuse_and_publication(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    key = ProductPartitionKey.day("events", "2026-01-01")
    ref = store.put(key, b"good")
    store.publish({key: ref})
    before = store.manifest_path.read_bytes()
    path = store.root / ref.path
    path.write_bytes(b"evil")
    with pytest.raises(ValueError, match="digest mismatch"):
        store.put(key, b"good")
    with pytest.raises(ValueError, match="digest or size mismatch"):
        store.publish({key: ref})
    assert path.read_bytes() == b"evil"
    assert store.manifest_path.read_bytes() == before


def test_gc_excludes_selected_retained_and_inflight_roots(tmp_path):
    from dataclasses import replace
    store = ArtifactStore(tmp_path / "store")
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    refs = []
    for name in ["selected", "retained", "inflight", "orphan"]:
        refs.append(replace(store.put(ProductPartitionKey.entity("events", name), name,
                                      generations=[name]), created_at=old))
    store.publish({ref.key: ref for ref in refs})
    store.publish({refs[0].key: refs[0]})
    candidates = store.plan_gc(["retained", "inflight"], now=datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert candidates == (refs[3],)


def test_gc_protects_legacy_newest_selection(tmp_path):
    from dataclasses import replace
    store = ArtifactStore(tmp_path / "store")
    key = ProductPartitionKey.entity("events", "fixture")
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = replace(store.put(key, b"first"), created_at=old)
    second = replace(store.put(key, b"second"), created_at=old)
    store.manifest_path.write_text(json.dumps({"artifacts": [first.to_dict(), second.to_dict()]}))
    assert store.plan_gc([], now=datetime(2026, 8, 25, tzinfo=timezone.utc)) == (first,)


def test_reuse_preserves_earlier_generation_roots(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    key = ProductPartitionKey.entity("events", "fixture")
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = replace(store.put(key, b"same", generations=["retained"]), created_at=old)
    store.publish({key: first})
    same = store.put(key, b"same", generations=["current"])
    store.publish({key: same})
    store.publish({})
    inventory = store.load_manifest()
    assert len(inventory) == 1
    assert inventory[0].generations == ("current", "retained")
    assert inventory[0].created_at == old
    assert store.plan_gc(["retained"], now=datetime(2026, 8, 25, tzinfo=timezone.utc)) == ()
    assert store.plan_gc([], now=datetime(2026, 8, 25, tzinfo=timezone.utc)) == inventory
