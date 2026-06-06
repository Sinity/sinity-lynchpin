from __future__ import annotations

import pytest


def test_materialize_title_metadata_records_input_high_water(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from lynchpin.ingest.title_metadata_materialize import TITLE_METADATA_SCHEMA_VERSION, materialize_title_metadata

    source = tmp_path / "semantic_classifications.duckdb"
    output = tmp_path / "title_metadata.ndjson"
    with duckdb.connect(str(source)) as conn:
        conn.execute(
            """
            create table semantic_classifications(
                title_hash varchar,
                app varchar,
                normalized_title varchar,
                classification_source varchar
            )
            """
        )
        conn.execute("insert into semantic_classifications values ('h', 'kitty', 'nvim', 'rules')")

    manifest = materialize_title_metadata(source_db=source, output=output)

    assert manifest["row_count"] == 1
    assert manifest["schema_version"] == TITLE_METADATA_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] == manifest["source_db_mtime"]
