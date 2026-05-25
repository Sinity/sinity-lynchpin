from __future__ import annotations

import json
from datetime import date

from lynchpin.ingest.webhistory import build_full_history, full_history_manifest_path
from lynchpin.materialization import MaterializedDataset, render_materialization_audit


def test_build_full_history_writes_manifest(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    segment = data_dir / "segment_unique_2026-01-01_to_2026-01-01.ndjson"
    segment.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "iso_time": "2026-01-01T10:00:00+00:00",
                        "url": "https://example.com/a",
                        "title": "A",
                        "source": "fixture",
                    }
                ),
                json.dumps(
                    {
                        "iso_time": "2026-01-01T10:01:00+00:00",
                        "url": "https://example.com/b",
                        "title": "B",
                        "source": "fixture",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "derived" / "full_history.ndjson"
    report = build_full_history(data_dir=data_dir, output=output)

    manifest_path = full_history_manifest_path(output)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["row_count"] == 2
    assert output.exists()
    assert manifest["dataset"] == "webhistory.full_history"
    assert manifest["row_count"] == 2
    assert manifest["first_visit_at"].startswith("2026-01-01T10:00:00")
    assert manifest["last_visit_at"].startswith("2026-01-01T10:01:00")
    assert manifest["first_date"] == "2026-01-01"
    assert manifest["last_date"] == "2026-01-01"
    assert manifest["dedup_tolerance_seconds"] == 30
    assert manifest["segments"] == [
        {
            "path": str(segment),
            "input_visit_count": 2,
            "first_visit_at": "2026-01-01T10:00:00+00:00",
            "last_visit_at": "2026-01-01T10:01:00+00:00",
        }
    ]
    assert manifest["source_counts"] == {str(segment): 2}


def test_render_materialization_audit_marks_non_ready_reasons(tmp_path):
    row = MaterializedDataset(
        name="example",
        status="partial",
        authority="raw fixture",
        query_surface="lynchpin.sources.example",
        materialized_paths=(tmp_path / "example.ndjson",),
        raw_roots=(tmp_path / "raw",),
        row_count=10,
        first_date=date(2020, 1, 1),
        last_date=date(2020, 1, 2),
        refresh_command="example refresh",
        reason="canonical product missing",
    )

    rendered = render_materialization_audit([row])

    assert "| example | partial | 10 | 2020-01-01 -> 2020-01-02 |" in rendered
    assert "canonical product missing" in rendered


def test_materialization_contract_names_have_builders() -> None:
    from lynchpin.core.source_contracts import SOURCE_CONTRACT_NAMES
    from lynchpin.materialization import _dataset_builders

    assert tuple(_dataset_builders()) == SOURCE_CONTRACT_NAMES


def test_materialization_contract_names_have_explicit_local_materializer_policy() -> None:
    from lynchpin.core.source_contracts import SOURCE_CONTRACT_NAMES
    from lynchpin.materialization import _dataset_builders, _materializers

    builder_names = set(_dataset_builders())
    materializer_names = set(_materializers())

    assert builder_names == set(SOURCE_CONTRACT_NAMES)
    assert materializer_names <= builder_names
    assert "polylogue" not in materializer_names
    assert "evidence_graph_substrate" not in materializer_names
    assert "title_metadata" in materializer_names
    assert "activity_content" in materializer_names


def test_materialized_dataset_json_carries_contract_status_semantics(tmp_path) -> None:
    row = MaterializedDataset(
        name="webhistory",
        status="partial",
        authority="raw fixture",
        query_surface="lynchpin.sources.web",
        materialized_paths=(tmp_path / "history.ndjson",),
        raw_roots=(tmp_path / "raw",),
        row_count=None,
        first_date=None,
        last_date=None,
        refresh_command="refresh",
        reason="fixture missing",
    )

    payload = row.to_json()

    assert payload["kind"] == "dataset"
    assert payload["status"] == "partial"
    assert payload["substrate_status"] == "unavailable"
    assert payload["required"] is True
    assert payload["collection_model"] == "continuous"
    assert payload["coverage"]["relation"] == "unavailable"


def test_materialized_window_overlaps_uses_known_bounds(monkeypatch) -> None:
    from lynchpin import materialization

    rows = [
        MaterializedDataset(
            name="example",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2020, 1, 1),
            last_date=date(2020, 1, 31),
            refresh_command="refresh",
            reason="ready",
        ),
        MaterializedDataset(
            name="unknown_bounds",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=None,
            last_date=None,
            refresh_command="refresh",
            reason="ready",
        ),
    ]
    monkeypatch.setattr(materialization, "audit_materialization", lambda cfg=None: rows)

    assert materialization.materialized_window_overlaps(
        "example", start=date(2020, 1, 15), end=date(2020, 1, 16)
    )
    assert not materialization.materialized_window_overlaps(
        "example", start=date(2020, 2, 1), end=date(2020, 2, 2)
    )
    assert not materialization.materialized_window_overlaps(
        "unknown_bounds", start=date(2020, 1, 1), end=date(2020, 1, 2)
    )


def test_materialized_dataset_coverage_describes_window_without_age_scoring() -> None:
    from lynchpin.materialization import materialized_dataset_coverage

    row = MaterializedDataset(
        name="reddit",
        status="ready",
        authority="raw fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2025, 1, 1),
        last_date=date(2025, 1, 31),
        refresh_command="refresh",
        reason="ready",
    )

    coverage = materialized_dataset_coverage(
        row,
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
    )

    assert coverage["relation"] == "no_overlap"
    assert coverage["collection_model"] == "event_export"
    assert "not proof of zero activity" in coverage["interpretation"]


def test_product_with_manifest_requires_valid_manifest(tmp_path) -> None:
    from lynchpin.materialization import _product_with_manifest_exists

    product = tmp_path / "product.ndjson"
    manifest = tmp_path / "product.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text("{", encoding="utf-8")

    assert not _product_with_manifest_exists(product, manifest)

    manifest.write_text('{"row_count": 1}', encoding="utf-8")

    assert _product_with_manifest_exists(product, manifest)


def test_dataset_status_mapping_is_shared() -> None:
    from lynchpin.core.source_contracts import dataset_status_to_substrate_status

    assert dataset_status_to_substrate_status("ready") == "ok"
    assert dataset_status_to_substrate_status("empty") == "empty"
    assert dataset_status_to_substrate_status("missing") == "unavailable"
    assert dataset_status_to_substrate_status("partial") == "unavailable"
    assert dataset_status_to_substrate_status("stale") == "unavailable"
    assert dataset_status_to_substrate_status("degraded") == "error"
    assert dataset_status_to_substrate_status("error") == "error"
