"""Tests for the raw-log -> substance CSV sync."""

from __future__ import annotations

import csv
from types import SimpleNamespace

from lynchpin.cli import substance_log_sync as sync


def _write_csv(path, rows):
    fieldnames = ["date", "time", "substance", "amount_mg", "source", "note"]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def _env(tmp_path, monkeypatch, csv_rows, rawlog_text):
    exports = tmp_path / "exports"
    (exports / "health/processed").mkdir(parents=True)
    csv_path = exports / "health/processed/substance_log_unified.csv"
    _write_csv(csv_path, csv_rows)
    rawlog = tmp_path / "logs.raw-log.md"
    rawlog.write_text(rawlog_text)
    monkeypatch.setattr(sync, "get_config", lambda: SimpleNamespace(exports_root=exports))
    monkeypatch.setattr(sync, "_rawlog_path", lambda: rawlog)
    return csv_path


BASE_ROW = {
    "date": "2026-05-30",
    "time": "14:15",
    "substance": "2-FMA",
    "amount_mg": "35",
    "source": "raw_log",
    "note": "35mg 2-FMA",
}


def test_appends_only_after_high_water_mark(tmp_path, monkeypatch):
    csv_path = _env(
        tmp_path,
        monkeypatch,
        [BASE_ROW],
        "\n".join(
            [
                "- **2026-05-30 14:15:00** 35mg 2-FMA",  # at mark: skipped
                "- **2026-06-01 09:00:00** 42mg 2-FMA",
                "- **2026-06-01 21:30:12** drug 2-FMA 30mg ; some narrative",
                "- **2026-06-02 10:00:00** plain narrative without doses",
                "- **2026-06-03 08:00:00** 32mgg 2-FMA",  # typo tolerated
            ]
        ),
    )
    added = sync.sync_substance_log(dry_run=False)
    assert added == 3
    rows = _read_csv(csv_path)
    new = [r for r in rows if r["date"] >= "2026-06-01"]
    assert [(r["date"], r["amount_mg"]) for r in new] == [
        ("2026-06-01", "42"),
        ("2026-06-01", "30"),
        ("2026-06-03", "32"),
    ]
    assert all(r["source"] == "raw_log" and r["substance"] == "2-FMA" for r in new)
    # note preserves the raw text for audit
    assert "narrative" in new[1]["note"]


def test_unknown_substances_are_not_invented(tmp_path, monkeypatch):
    csv_path = _env(
        tmp_path,
        monkeypatch,
        [BASE_ROW],
        "- **2026-06-01 09:00:00** 500mg mystery-compound\n",
    )
    assert sync.sync_substance_log(dry_run=False) == 0
    assert len(_read_csv(csv_path)) == 1


def test_rerun_is_idempotent(tmp_path, monkeypatch):
    csv_path = _env(
        tmp_path,
        monkeypatch,
        [BASE_ROW],
        "- **2026-06-01 09:00:00** 42mg 2-FMA\n",
    )
    assert sync.sync_substance_log(dry_run=False) == 1
    assert sync.sync_substance_log(dry_run=False) == 0
    assert len(_read_csv(csv_path)) == 2


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    csv_path = _env(
        tmp_path,
        monkeypatch,
        [BASE_ROW],
        "- **2026-06-01 09:00:00** 42mg 2-FMA\n",
    )
    assert sync.sync_substance_log(dry_run=True) == 1
    assert len(_read_csv(csv_path)) == 1
