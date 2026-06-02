"""Tests for sinnix_generations source."""

import json
from datetime import date
from pathlib import Path


from lynchpin.sources.sinnix_generations import (
    daily_generations,
    iter_generations,
)


class TestIterGenerations:
    """Tests for iter_generations."""

    def test_iter_generations_parse(self, tmp_path: Path):
        """Verify parsing of generations.jsonl with 3 records."""
        gen_file = tmp_path / "generations.jsonl"
        records = [
            {
                "generation": "45",
                "activated_at": "2026-05-18T15:32:39+00:00",
                "store_path": "/nix/store/abc123",
                "sinnix_revision": "5fe7f19",
                "nixos_label": "26.05.20260510",
                "host": "sinnix-prime",
            },
            {
                "generation": "44",
                "activated_at": "2026-05-17T10:20:15+00:00",
                "store_path": "/nix/store/def456",
                "sinnix_revision": "4be8e18",
                "nixos_label": "26.05.20260508",
                "host": "sinnix-prime",
            },
            {
                "generation": "43",
                "activated_at": "2026-05-16T08:45:00+00:00",
                "store_path": "/nix/store/ghi789",
                "sinnix_revision": "3ad7d17",
                "nixos_label": "26.05.20260505",
                "host": "sinnix-prime",
            },
        ]

        with gen_file.open("w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        result = list(iter_generations(path=gen_file))

        # Verify count
        assert len(result) == 3

        # Verify first record
        assert result[0].generation == 45
        assert result[0].host == "sinnix-prime"
        assert result[0].sinnix_revision == "5fe7f19"
        assert result[0].date == date(2026, 5, 18)

    def test_iter_generations_date_filter(self, tmp_path: Path):
        """Verify start/end date filtering."""
        gen_file = tmp_path / "generations.jsonl"
        records = [
            {
                "generation": "45",
                "activated_at": "2026-05-20T15:00:00+00:00",
                "store_path": "/nix/store/abc",
                "sinnix_revision": "abc",
                "nixos_label": "26.05",
                "host": "prime",
            },
            {
                "generation": "44",
                "activated_at": "2026-05-19T10:00:00+00:00",
                "store_path": "/nix/store/def",
                "sinnix_revision": "def",
                "nixos_label": "26.05",
                "host": "prime",
            },
            {
                "generation": "43",
                "activated_at": "2026-05-18T08:00:00+00:00",
                "store_path": "/nix/store/ghi",
                "sinnix_revision": "ghi",
                "nixos_label": "26.05",
                "host": "prime",
            },
        ]

        with gen_file.open("w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        # Filter to only 2026-05-19
        result = list(
            iter_generations(
                path=gen_file,
                start=date(2026, 5, 19),
                end=date(2026, 5, 19),
            )
        )

        assert len(result) == 1
        assert result[0].generation == 44

    def test_iter_generations_malformed_json(self, tmp_path: Path):
        """Verify resilience to malformed JSON lines."""
        gen_file = tmp_path / "generations.jsonl"
        with gen_file.open("w") as f:
            f.write('{"generation": "45", "activated_at": "2026-05-20T15:00:00+00:00", "store_path": "/nix/store/abc", "sinnix_revision": "abc", "nixos_label": "26.05", "host": "prime"}\n')
            f.write("malformed json\n")
            f.write('{"generation": "44", "activated_at": "2026-05-19T10:00:00+00:00", "store_path": "/nix/store/def", "sinnix_revision": "def", "nixos_label": "26.05", "host": "prime"}\n')

        result = list(iter_generations(path=gen_file))

        # Only 2 valid records should be returned
        assert len(result) == 2
        assert result[0].generation == 45
        assert result[1].generation == 44

    def test_iter_generations_missing_file(self, tmp_path: Path):
        """Verify graceful handling of missing file."""
        missing_file = tmp_path / "nonexistent.jsonl"
        result = list(iter_generations(path=missing_file))
        assert result == []


class TestDailyGenerations:
    """Tests for daily_generations."""

    def test_daily_generations_aggregation(self, tmp_path: Path):
        """Verify aggregation of generations by day."""
        gen_file = tmp_path / "generations.jsonl"
        records = [
            {
                "generation": "50",
                "activated_at": "2026-05-20T15:00:00+00:00",
                "store_path": "/nix/store/a",
                "sinnix_revision": "a",
                "nixos_label": "26.05",
                "host": "prime",
            },
            {
                "generation": "49",
                "activated_at": "2026-05-20T10:00:00+00:00",
                "store_path": "/nix/store/b",
                "sinnix_revision": "b",
                "nixos_label": "26.05",
                "host": "prime",
            },
            {
                "generation": "48",
                "activated_at": "2026-05-19T08:00:00+00:00",
                "store_path": "/nix/store/c",
                "sinnix_revision": "c",
                "nixos_label": "26.05",
                "host": "prime",
            },
        ]

        with gen_file.open("w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        result = list(
            daily_generations(
                start=date(2026, 5, 19),
                end=date(2026, 5, 20),
                path=gen_file,
            )
        )

        assert len(result) == 2

        # Day 1: 2026-05-19
        assert result[0].date == date(2026, 5, 19)
        assert result[0].count == 1
        assert result[0].hosts == frozenset(["prime"])
        assert result[0].generations == (48,)

        # Day 2: 2026-05-20
        assert result[1].date == date(2026, 5, 20)
        assert result[1].count == 2
        assert result[1].hosts == frozenset(["prime"])
        assert result[1].generations == (49, 50)

    def test_daily_generations_multiple_hosts(self, tmp_path: Path):
        """Verify aggregation across multiple hosts."""
        gen_file = tmp_path / "generations.jsonl"
        records = [
            {
                "generation": "10",
                "activated_at": "2026-05-20T15:00:00+00:00",
                "store_path": "/nix/store/a",
                "sinnix_revision": "a",
                "nixos_label": "26.05",
                "host": "prime",
            },
            {
                "generation": "5",
                "activated_at": "2026-05-20T14:00:00+00:00",
                "store_path": "/nix/store/b",
                "sinnix_revision": "b",
                "nixos_label": "26.05",
                "host": "laptop",
            },
        ]

        with gen_file.open("w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        result = list(
            daily_generations(
                start=date(2026, 5, 20),
                end=date(2026, 5, 20),
                path=gen_file,
            )
        )

        assert len(result) == 1
        assert result[0].date == date(2026, 5, 20)
        assert result[0].count == 2
        assert result[0].hosts == frozenset(["prime", "laptop"])
        assert result[0].generations == (5, 10)

    def test_daily_generations_empty(self, tmp_path: Path):
        """Verify empty result when no activations in date range."""
        gen_file = tmp_path / "generations.jsonl"
        records = [
            {
                "generation": "45",
                "activated_at": "2026-05-20T15:00:00+00:00",
                "store_path": "/nix/store/a",
                "sinnix_revision": "a",
                "nixos_label": "26.05",
                "host": "prime",
            },
        ]

        with gen_file.open("w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        # Query for a different date range
        result = list(
            daily_generations(
                start=date(2026, 5, 21),
                end=date(2026, 5, 22),
                path=gen_file,
            )
        )

        assert result == []
