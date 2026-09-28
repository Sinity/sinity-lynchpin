"""Chisel's terminal summary reads as plain words and keeps every measurement."""

from __future__ import annotations

from pathlib import Path

from lynchpin.analysis.projects import chisel_terminal as terminal
from lynchpin.sources import chisel as source_chisel
from lynchpin.sources.chisel import RepoPlan, Slice


def _plan(name: str, slices: int) -> RepoPlan:
    return RepoPlan(
        name=name,
        path=Path("/nonexistent") / name,
        slices=tuple(Slice(f"s{i}", "", ("*",)) for i in range(slices)),
    )


_LOCAL_COUNTS = {
    "issues_open": 0, "issues_closed": 851, "prs_open": 29, "prs_merged": 4504,
    "issues_open_current": None, "prs_open_current": None,
}


def test_summary_table_uses_words_and_thousands_separators() -> None:
    """Fails if a cell falls back to the old ``?0o/851c`` or ``7/0`` codes, or
    if a measured number disappears from the row."""
    plans = [_plan("polylogue", 7), _plan("sinex", 3)]
    results = {
        "polylogue": {"status": "generated", "slices": 0, "gitlog_commits": 13041,
                      "total_bytes": 424_400_000, "elapsed_s": 265.5},
        "sinex": {"status": "failed", "total_bytes": 0, "elapsed_s": 1.0},
    }
    rows, total, statuses = terminal.summary_rows(
        plans, results, {"polylogue": _LOCAL_COUNTS}, xml_requested=False, total_elapsed=266.4,
    )

    assert statuses == ["generated", "failed"]
    assert rows[0] == ("polylogue", "ok", "7", "0 open · 851 closed", "29 open · 4,504 merged",
                       "13,041", source_chisel._fmt_bytes(424_400_000), "265.5s")
    assert rows[1][:6] == ("sinex", "failed", "–", "–", "–", "–")
    assert total[0] == "Total" and total[-1] == "266.4s"
    lines = terminal.render_plain_table(rows, total)
    assert lines[0].split() == ["Project", "Status", "Slices", "Issues", "PRs", "Commits", "Size", "Time"]
    assert len({len(lines[0]), len(lines[1])}) == 1


def test_slices_cell_counts_xml_only_when_requested() -> None:
    assert terminal.slices_cell(7, 0, xml_requested=False) == "7"
    assert terminal.slices_cell(7, 6, xml_requested=True) == "7 · 6 XML"


def test_legend_marks_local_counts_once() -> None:
    """Fails if unknown freshness is marked per cell instead of once, or if a
    refreshed count is still called local."""
    counts = {"a": _LOCAL_COUNTS, "b": dict(_LOCAL_COUNTS, prs_open_count_coverage="possibly_truncated")}
    legend = terminal.summary_legend(counts, {"refresh_status": "local_only"})
    assert legend[0] == "GitHub counts from the local mirror; remote not checked (--refresh updates it)."
    assert legend[1].startswith("≥ marks an open count that is a lower bound")
    current = dict(_LOCAL_COUNTS, issues_open_current=0, prs_open_current=29)
    assert terminal.summary_legend({"a": current}, {"refresh_status": "refreshed"}) == []
    assert terminal.summary_legend({"a": {}}, {"refresh_status": "unavailable"}) == [
        "GitHub mirror unavailable: issue and PR counts are empty, not zero activity."
    ]


def test_github_context_line_says_what_the_step_did() -> None:
    """Fails if a local-only read reports refresh counters it never measured."""
    local = terminal.github_context_line({"refresh_status": "local_only", "remote_freshness": "unknown"}, 0.6)
    assert local == "GitHub: read the local mirror in 0.6s; remote not checked (--refresh updates it)"
    refreshed = terminal.github_context_line(
        {"refresh_status": "refreshed", "inventory_items_seen": 1200, "detail_refreshes": 4,
         "detail_reuses": 1196, "detail_misses": 1}, 12.0,
    )
    assert refreshed == ("GitHub: refreshed in 12.0s — 1,200 inventory items seen; 4 details fetched; "
                         "1,196 unchanged and reused; 1 details missed")


def test_plain_progress_prints_each_state_once(monkeypatch) -> None:
    """Without a terminal, fails if an unchanged state repeats or a change is dropped."""
    printed: list[str] = []
    monkeypatch.setattr(source_chisel, "_console", None)
    monkeypatch.setattr(terminal, "_print_live", lambda text, **_: printed.append(text))
    with terminal.ProgressLine() as line:
        assert not line.live
        for stages in ({"reports"}, {"reports"}, {"reports"}, {"context"}):
            line.update(terminal.progress_text(0, 1, {"polylogue": stages}))
    assert printed == ["Progress 0/1: polylogue (reports)", "Progress 0/1: polylogue (context)"]
