from __future__ import annotations

from dataclasses import dataclass

from lynchpin.core.source import (
    SourceReadiness,
    file_readiness,
    read_jsonl_with,
)


@dataclass(frozen=True)
class Sample:
    value: int


def _hydrate_sample(payload: dict) -> Sample | None:
    if "value" not in payload:
        return None
    return Sample(value=int(payload["value"]))


def test_file_readiness_missing(tmp_path):
    r = file_readiness(tmp_path / "absent.jsonl")
    assert r == SourceReadiness("missing", f"{tmp_path / 'absent.jsonl'} does not exist",
                                tmp_path / "absent.jsonl", 0)


def test_file_readiness_empty(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.touch()
    assert file_readiness(p).status == "empty"


def test_file_readiness_ok_counts_nonblank(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('{"a": 1}\n\n{"a": 2}\n')
    assert file_readiness(p).status == "ok"
    assert file_readiness(p).row_count == 2


def test_read_jsonl_with_yields_hydrated_records(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('{"value": 1}\n{"value": 2}\n{"value": 3}\n')
    assert list(read_jsonl_with(p, _hydrate_sample)) == [Sample(1), Sample(2), Sample(3)]


def test_read_jsonl_with_skips_blank_lines(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('\n\n{"value": 5}\n\n')
    assert list(read_jsonl_with(p, _hydrate_sample)) == [Sample(5)]


def test_read_jsonl_with_skips_unparseable_json(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('{"value": 1}\n{ broken\n{"value": 2}\n')
    assert list(read_jsonl_with(p, _hydrate_sample)) == [Sample(1), Sample(2)]


def test_read_jsonl_with_skips_non_dict_top_level(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('{"value": 1}\n[1, 2, 3]\n{"value": 2}\n')
    assert list(read_jsonl_with(p, _hydrate_sample)) == [Sample(1), Sample(2)]


def test_read_jsonl_with_skips_when_hydrate_returns_none(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('{"value": 1}\n{"no_value": 9}\n{"value": 2}\n')
    assert list(read_jsonl_with(p, _hydrate_sample)) == [Sample(1), Sample(2)]


def test_read_jsonl_with_skips_when_hydrate_raises(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('{"value": "abc"}\n{"value": 7}\n')
    # First line will raise int("abc"); helper should swallow and continue.
    assert list(read_jsonl_with(p, _hydrate_sample)) == [Sample(7)]


def test_read_jsonl_with_missing_file_yields_nothing(tmp_path):
    assert list(read_jsonl_with(tmp_path / "absent.jsonl", _hydrate_sample)) == []
