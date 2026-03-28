"""Tests for sources/terminal.py — Atuin commands, shell sessions, recordings."""

from datetime import datetime, timedelta, timezone
from lynchpin.sources.terminal import (
    AtuinCommand, ShellSession, TerminalRecording,
    _extract_project, _categorise_command, _detect_unit, _to_unit, _from_unit,
)

UTC = timezone.utc


class TestExtractProject:
    def test_realm_project(self):
        assert _extract_project("/realm/project/sinex/src") == "sinex"

    def test_no_project(self):
        assert _extract_project("/home/user") is None

    def test_lynchpin(self):
        assert _extract_project("/realm/project/sinity-lynchpin") == "sinity-lynchpin"


class TestCategorise:
    def test_sinex(self):
        assert _categorise_command("/realm/project/sinex") == "development:sinex"

    def test_sinnix(self):
        assert _categorise_command("/realm/project/sinnix") == "infrastructure:sinnix"

    def test_other_project(self):
        assert _categorise_command("/realm/project/polylogue") == "development:other"

    def test_home(self):
        assert _categorise_command("/home/sinity") == "home"

    def test_misc(self):
        assert _categorise_command("/tmp") == "misc"


class TestTimestampUnits:
    def test_roundtrip_ns(self):
        dt = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
        ns = _to_unit(dt, "ns")
        back = _from_unit(ns, "ns")
        assert abs((back - dt).total_seconds()) < 0.001

    def test_roundtrip_s(self):
        dt = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
        s = _to_unit(dt, "s")
        back = _from_unit(s, "s")
        assert abs((back - dt).total_seconds()) < 1
