from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from . import activitywatch, atuin, gitstats, sessions, sleep, webhistory
from .activitywatch import ActivityWatchEvent
from .atuin import AtuinCommand
from .gitstats import GitCommit
from .sessions import SessionRecord
from .sleep import SleepEntry


@dataclass
class DaySnapshot:
    date: date
    windows: List[ActivityWatchEvent]
    afk: List[ActivityWatchEvent]
    web: List[ActivityWatchEvent]
    atuin_commands: List[AtuinCommand]
    git_commits: List[GitCommit]
    sleep: Optional[SleepEntry]
    session_records: List[SessionRecord]
    webhistory: List[Dict[str, object]]

    def to_dict(self) -> Dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "windows": [asdict(evt) for evt in self.windows],
            "afk": [asdict(evt) for evt in self.afk],
            "web": [asdict(evt) for evt in self.web],
            "atuin_commands": [asdict(cmd) for cmd in self.atuin_commands],
            "git_commits": [asdict(commit) for commit in self.git_commits],
            "sleep": asdict(self.sleep) if self.sleep else None,
            "sessions": [asdict(record) for record in self.session_records],
            "webhistory": self.webhistory,
        }


def load_day(target: date) -> DaySnapshot:
    start = datetime.combine(target, datetime.min.time())
    end = start + timedelta(days=1)
    windows = list(activitywatch.window_events(day=target))
    afk = list(activitywatch.afk_events(day=target))
    web = list(activitywatch.web_events(day=target))
    atuin_commands = list(atuin.iter_commands(start=start, end=end))
    git_commits = list(gitstats.commits_by_date(target))
    sleep_entry = sleep.sleep_by_date(target.isoformat())
    day_sessions = sessions.sessions_by_date(target)
    day_wh = list(webhistory.iter_entries(start_date=target.isoformat(), end_date=target.isoformat()))
    return DaySnapshot(
        date=target,
        windows=windows,
        afk=afk,
        web=web,
        atuin_commands=atuin_commands,
        git_commits=git_commits,
        sleep=sleep_entry,
        session_records=day_sessions,
        webhistory=day_wh,
    )
