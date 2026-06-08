from datetime import date, datetime, timezone
from pathlib import Path

from lynchpin.sources import svn


def _entry(revision: int, day: str, *, author: str = "michab") -> dict:
    return {
        "revision": revision,
        "author": author,
        "date": datetime.fromisoformat(day).replace(tzinfo=timezone.utc),
        "message": f"r{revision}",
        "paths": [{"path": f"/file-{revision}", "action": "M", "kind": "file"}],
    }


def test_iter_commits_filters_bounds_and_stops_older_log_entries(monkeypatch, tmp_path) -> None:
    log = tmp_path / "repo/svn.log"
    log.parent.mkdir(parents=True)
    log.write_text("<log />", encoding="utf-8")
    parsed: list[int] = []

    def fake_parse(path: Path):
        assert path == log
        for entry in (
            _entry(4, "2022-09-23T10:00:00"),
            _entry(3, "2022-09-22T10:00:00"),
            _entry(2, "2022-09-21T10:00:00"),
        ):
            parsed.append(entry["revision"])
            yield entry
        raise AssertionError("iterator should stop once entries are older than start")

    monkeypatch.setattr(svn, "_parse_log_xml", fake_parse)

    rows = list(
        svn.iter_commits(
            root=tmp_path,
            start=date(2022, 9, 22),
            end=date(2022, 9, 22),
        )
    )

    assert [row.revision for row in rows] == [3]
    assert parsed == [4, 3, 2]


def test_daily_activity_passes_bounds_to_iter_commits(monkeypatch) -> None:
    calls = []

    def fake_iter_commits(**kwargs):
        calls.append(kwargs)
        yield svn.SVNCommit(
            revision=3,
            author="michab",
            date=datetime(2022, 9, 22, 10, tzinfo=timezone.utc),
            message="demo",
            paths=(svn.SVNPathChange(path="/demo", action="M"),),
        )

    monkeypatch.setattr(svn, "iter_commits", fake_iter_commits)

    rows = svn.daily_activity(start=date(2022, 9, 22), end=date(2022, 9, 22))

    assert calls == [
        {
            "author": "michab",
            "start": date(2022, 9, 22),
            "end": date(2022, 9, 22),
        }
    ]
    assert [(row.date, row.commit_count, row.files_changed) for row in rows] == [
        (date(2022, 9, 22), 1, 1)
    ]


def test_author_stats_passes_bounds_to_iter_commits(monkeypatch) -> None:
    calls = []

    def fake_iter_commits(**kwargs):
        calls.append(kwargs)
        yield svn.SVNCommit(
            revision=3,
            author="michab",
            date=datetime(2022, 9, 22, 10, tzinfo=timezone.utc),
            message="demo",
            paths=(),
        )

    monkeypatch.setattr(svn, "iter_commits", fake_iter_commits)

    rows = svn.author_stats(start=date(2022, 9, 22), end=date(2022, 9, 22))

    assert calls == [
        {
            "author": None,
            "start": date(2022, 9, 22),
            "end": date(2022, 9, 22),
        }
    ]
    assert rows == {"michab": 1}
