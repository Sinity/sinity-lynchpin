from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class LifeMonthWorkSummary:
    git_commits: int
    git_top_repos: list[tuple[str, int]]
    chat_session_count: int = 0
    chat_work_events: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.chat_work_events is None:
            object.__setattr__(self, "chat_work_events", {})

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthHealthSummary:
    sleep_sessions: int
    sleep_total_h: Optional[float]
    sleep_avg_h: Optional[float]
    weight_n: int
    weight_min: Optional[float]
    weight_max: Optional[float]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthContextSummary:
    start_date: str
    end_date: str
    days: int
    active_hours: float
    recovery_hours: float
    chain_count: int
    signal_count: int
    command_count: int
    transcript_count: int
    commit_count: int
    dominant_modes: list[tuple[str, float]]
    dominant_projects: list[tuple[str, float]]
    dominant_topics: list[tuple[str, float]]
    source_counts: dict[str, int]
    coverage: dict[str, object]
    highlights: list[str]
    chat_session_count: int = 0
    chat_work_events: dict[str, int] = None  # type: ignore[assignment]
    chat_cost_usd: float = 0.0
    episode_count: int = 0
    episode_labels: list[str] = None  # type: ignore[assignment]
    anomaly_count: int = 0
    anomaly_kinds: list[str] = None  # type: ignore[assignment]
    top_repos: list[tuple[str, int]] = None  # type: ignore[assignment]
    top_paths: list[tuple[str, int]] = None  # type: ignore[assignment]
    top_session_titles: list[tuple[str, int]] = None  # type: ignore[assignment]
    avg_fragmentation: Optional[float] = None
    evidence_bundle: str | None = None

    def __post_init__(self):
        if self.chat_work_events is None:
            object.__setattr__(self, "chat_work_events", {})
        if self.episode_labels is None:
            object.__setattr__(self, "episode_labels", [])
        if self.anomaly_kinds is None:
            object.__setattr__(self, "anomaly_kinds", [])
        if self.top_repos is None:
            object.__setattr__(self, "top_repos", [])
        if self.top_paths is None:
            object.__setattr__(self, "top_paths", [])
        if self.top_session_titles is None:
            object.__setattr__(self, "top_session_titles", [])

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthSummary:
    output: "LifeMonthOutputSummary"
    work: LifeMonthWorkSummary
    intake: "LifeMonthIntakeSummary"
    mail: "LifeMonthMailSummary"
    location: "LifeMonthLocationSummary"
    money: "LifeMonthMoneySummary"
    health: LifeMonthHealthSummary
    notes: "LifeMonthNotesSummary"
    context: Optional[LifeMonthContextSummary] = None
    narrative: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "output": self.output.to_dict(),
            "work": self.work.to_dict(),
            "intake": self.intake.to_dict(),
            "mail": self.mail.to_dict(),
            "location": self.location.to_dict(),
            "money": self.money.to_dict(),
            "health": self.health.to_dict(),
            "context": self.context.to_dict() if self.context is not None else {},
            "notes": self.notes.to_dict(),
            "narrative": self.narrative,
        }


@dataclass(frozen=True)
class LifeMonthOutputSummary:
    reddit_comments: int
    reddit_posts: int
    reddit_messages: int
    wykop_link_comments: int
    wykop_entries: int
    wykop_entry_comments: int
    reddit_top_subs: list[tuple[str, int]]
    wykop_top_tags: list[tuple[str, int]]
    wykop_entries_top_tags: list[tuple[str, int]]
    output_top_topic_tokens: list[tuple[str, int]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthIntakeSummary:
    webhistory_events: int
    webhistory_top_domains: list[tuple[str, int]]
    webhistory_top_reddit_subs: list[tuple[str, int]]
    webhistory_top_title_tokens: list[tuple[str, int]]
    raindrop_bookmarks: int
    goodreads_books_read: int
    goodreads_books_added: int
    goodreads_top_authors_read: list[tuple[str, int]]
    goodreads_top_titles_read: list[tuple[str, int]]
    google_searches: int
    google_search_top_tokens: list[tuple[str, int]]
    google_search_top_queries: list[tuple[str, int]]
    youtube_watch: int
    youtube_searches: int
    youtube_search_top_tokens: list[tuple[str, int]]
    youtube_search_top_queries: list[tuple[str, int]]
    youtube_watch_history: int
    youtube_watch_history_top_video_ids: list[tuple[str, int]]
    youtube_watch_history_top_channels: list[tuple[str, int]]
    youtube_watch_history_top_tokens: list[tuple[str, int]]
    youtube_watch_history_top_titles: list[tuple[str, int]]
    youtube_search_history: int
    youtube_search_history_top_tokens: list[tuple[str, int]]
    youtube_search_history_top_queries: list[tuple[str, int]]
    chrome_myactivity: int
    chrome_history_events: int
    chrome_history_top_domains: list[tuple[str, int]]
    chrome_history_top_reddit_subs: list[tuple[str, int]]
    chrome_history_top_title_tokens: list[tuple[str, int]]
    maps_myactivity: int
    maps_search_top_tokens: list[tuple[str, int]]
    maps_search_top_queries: list[tuple[str, int]]
    image_search_myactivity: int
    image_search_top_tokens: list[tuple[str, int]]
    image_search_top_queries: list[tuple[str, int]]
    play_store_myactivity: int
    play_store_top_tokens: list[tuple[str, int]]
    play_store_top_queries: list[tuple[str, int]]
    video_search_myactivity: int
    video_search_top_tokens: list[tuple[str, int]]
    video_search_top_queries: list[tuple[str, int]]
    shopping_myactivity: int
    shopping_top_tokens: list[tuple[str, int]]
    shopping_top_queries: list[tuple[str, int]]
    travel_myactivity: int
    travel_top_tokens: list[tuple[str, int]]
    travel_top_queries: list[tuple[str, int]]
    myactivity_other_categories: list[tuple[str, int]]
    spotify_hours: Optional[float]
    spotify_top_artists: list[str]
    spotify_top_tracks: list[str]
    intake_top_topic_tokens: list[tuple[str, int]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthMailSummary:
    gmail_messages: int
    gmail_top_from_domains: list[tuple[str, int]]
    gmail_top_subject_tokens: list[tuple[str, int]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthLocationSummary:
    records: int
    semantic_place_visits: int
    semantic_activity_segments: int
    semantic_top_places: list[tuple[str, int]]
    semantic_top_activities: list[tuple[str, int]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthMoneySummary:
    ledger_expenses_pln: Optional[float]
    revolut_out_pln: float
    revolut_in_pln: float
    mbank_personal_out_pln: float
    mbank_personal_in_pln: float
    mbank_business_out_pln: float
    mbank_business_in_pln: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthNotesSummary:
    onenote_journal_entries: int
    substance_log_headings: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
