from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from .takeout_archives import TarReader, select_archive_with_member
from .takeout_google import (
    parse_chrome_history_json_from_takeout,
    parse_gmail_headers_from_takeout_mbox,
    parse_location_records_from_takeout,
    parse_semantic_location_history_from_takeout,
)
from .takeout_myactivity import (
    parse_myactivity_from_takeouts,
    parse_myactivity_other_category_counts_from_takeouts,
)
from .takeout_youtube import (
    load_youtube_video_titles_from_takeout,
    parse_youtube_watch_history_from_takeouts,
)


@dataclass(frozen=True)
class LifeTakeoutBundle:
    google_search_counts: dict[str, int]
    google_search_tokens: dict[str, Counter[str]]
    google_search_phrases: dict[str, Counter[str]]
    youtube_watch_counts: dict[str, int]
    youtube_search_counts: dict[str, int]
    youtube_search_tokens: dict[str, Counter[str]]
    youtube_search_phrases: dict[str, Counter[str]]
    youtube_video_titles: dict[str, str]
    youtube_watch_history_counts: dict[str, int]
    youtube_watch_history_video_ids: dict[str, Counter[str]]
    youtube_watch_history_titles: dict[str, Counter[str]]
    youtube_watch_history_channels: dict[str, Counter[str]]
    youtube_search_history_counts: dict[str, int]
    youtube_search_history_tokens: dict[str, Counter[str]]
    youtube_search_history_phrases: dict[str, Counter[str]]
    chrome_counts: dict[str, int]
    maps_counts: dict[str, int]
    maps_tokens: dict[str, Counter[str]]
    maps_phrases: dict[str, Counter[str]]
    image_search_counts: dict[str, int]
    image_search_tokens: dict[str, Counter[str]]
    image_search_phrases: dict[str, Counter[str]]
    play_store_counts: dict[str, int]
    play_store_tokens: dict[str, Counter[str]]
    play_store_phrases: dict[str, Counter[str]]
    video_search_counts: dict[str, int]
    video_search_tokens: dict[str, Counter[str]]
    video_search_phrases: dict[str, Counter[str]]
    shopping_counts: dict[str, int]
    shopping_tokens: dict[str, Counter[str]]
    shopping_phrases: dict[str, Counter[str]]
    travel_counts: dict[str, int]
    travel_tokens: dict[str, Counter[str]]
    travel_phrases: dict[str, Counter[str]]
    myactivity_other_counts: dict[str, Counter[str]]
    chrome_history_counts: dict[str, int]
    chrome_history_domains: dict[str, Counter[str]]
    chrome_history_reddit_subs: dict[str, Counter[str]]
    chrome_history_title_tokens: dict[str, Counter[str]]
    location_records: dict[str, int]
    semantic_place_visits: dict[str, int]
    semantic_activity_segments: dict[str, int]
    semantic_top_places: dict[str, Counter[str]]
    semantic_top_activities: dict[str, Counter[str]]
    gmail_counts: dict[str, int]
    gmail_from_domains: dict[str, Counter[str]]
    gmail_subject_tokens: dict[str, Counter[str]]
    location_takeout_path: str | None
    gmail_takeout_path: str | None
    chrome_history_takeout_path: str | None
    youtube_video_texts_takeout_path: str | None


def parse_life_takeouts(
    takeout_paths: list[Path],
    *,
    start_month: str,
    end_month: str,
) -> LifeTakeoutBundle:
    with ExitStack() as stack:
        takeouts = [stack.enter_context(TarReader(path)) for path in takeout_paths]

        google_search_counts, google_search_tokens, google_search_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="search",
            member_path="Takeout/My Activity/Search/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=("Searched for",),
        )
        youtube_watch_counts, _, _ = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="youtube",
            member_path="Takeout/My Activity/YouTube/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=("Watched",),
        )
        youtube_search_counts, youtube_search_tokens, youtube_search_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="youtube",
            member_path="Takeout/My Activity/YouTube/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=("Searched for",),
        )

        youtube_video_titles: dict[str, str] = {}
        youtube_video_texts_takeout_path: str | None = None
        youtube_video_text_takeout = select_archive_with_member(
            takeouts,
            "Takeout/YouTube and YouTube Music/video metadata/video texts.csv",
        )
        if youtube_video_text_takeout is not None:
            youtube_video_texts_takeout_path = str(youtube_video_text_takeout.tar_path)
            youtube_video_titles = load_youtube_video_titles_from_takeout(
                youtube_video_text_takeout,
                member_path="Takeout/YouTube and YouTube Music/video metadata/video texts.csv",
            )

        (
            youtube_watch_history_counts,
            youtube_watch_history_video_ids,
            youtube_watch_history_titles,
            youtube_watch_history_channels,
        ) = parse_youtube_watch_history_from_takeouts(
            takeouts=takeouts,
            start_month=start_month,
            end_month=end_month,
        )
        (
            youtube_search_history_counts,
            youtube_search_history_tokens,
            youtube_search_history_phrases,
        ) = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="youtube_search_history",
            member_path="Takeout/YouTube and YouTube Music/history/search-history.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=("Searched for",),
        )
        chrome_counts, _, _ = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="chrome",
            member_path="Takeout/My Activity/Chrome/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        maps_counts, maps_tokens, maps_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="maps",
            member_path="Takeout/My Activity/Maps/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        image_search_counts, image_search_tokens, image_search_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="image_search",
            member_path="Takeout/My Activity/Image Search/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        play_store_counts, play_store_tokens, play_store_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="play_store",
            member_path="Takeout/My Activity/Google Play Store/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        video_search_counts, video_search_tokens, video_search_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="video_search",
            member_path="Takeout/My Activity/Video Search/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        shopping_counts, shopping_tokens, shopping_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="shopping",
            member_path="Takeout/My Activity/Shopping/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        travel_counts, travel_tokens, travel_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="travel",
            member_path="Takeout/My Activity/Travel/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )

        core_myactivity_categories = {
            "Search",
            "YouTube",
            "Chrome",
            "Maps",
            "Image Search",
            "Google Play Store",
            "Video Search",
            "Shopping",
            "Travel",
        }
        myactivity_other_counts = parse_myactivity_other_category_counts_from_takeouts(
            takeouts=takeouts,
            start_month=start_month,
            end_month=end_month,
            exclude_categories=core_myactivity_categories,
        )

        chrome_history_takeout_path: str | None = None
        chrome_history_takeout = select_archive_with_member(takeouts, "Takeout/Chrome/History.json")
        if chrome_history_takeout is not None:
            chrome_history_takeout_path = str(chrome_history_takeout.tar_path)
            (
                chrome_history_counts,
                chrome_history_domains,
                chrome_history_reddit_subs,
                chrome_history_title_tokens,
            ) = parse_chrome_history_json_from_takeout(
                chrome_history_takeout,
                member_path="Takeout/Chrome/History.json",
                start_month=start_month,
                end_month=end_month,
            )
        else:
            chrome_history_counts = defaultdict(int)
            chrome_history_domains = defaultdict(Counter)
            chrome_history_reddit_subs = defaultdict(Counter)
            chrome_history_title_tokens = defaultdict(Counter)

        location_takeout_path: str | None = None
        location_takeout = select_archive_with_member(takeouts, "Takeout/Location History/Records.json")
        if location_takeout is not None:
            location_takeout_path = str(location_takeout.tar_path)
            location_records = parse_location_records_from_takeout(
                location_takeout,
                member_path="Takeout/Location History/Records.json",
                start_month=start_month,
                end_month=end_month,
            )
            (
                semantic_place_visits,
                semantic_activity_segments,
                semantic_top_places,
                semantic_top_activities,
            ) = parse_semantic_location_history_from_takeout(
                location_takeout,
                root_prefix="Takeout/Location History/Semantic Location History/",
                start_month=start_month,
                end_month=end_month,
            )
        else:
            location_records = defaultdict(int)
            semantic_place_visits = defaultdict(int)
            semantic_activity_segments = defaultdict(int)
            semantic_top_places = defaultdict(Counter)
            semantic_top_activities = defaultdict(Counter)

        gmail_takeout_path: str | None = None
        gmail_takeout = select_archive_with_member(
            takeouts,
            "Takeout/Mail/All mail Including Spam and Trash.mbox",
        )
        if gmail_takeout is None:
            gmail_counts = defaultdict(int)
            gmail_from_domains = defaultdict(Counter)
            gmail_subject_tokens = defaultdict(Counter)
        else:
            gmail_takeout_path = str(gmail_takeout.tar_path)
            gmail_counts, gmail_from_domains, gmail_subject_tokens = parse_gmail_headers_from_takeout_mbox(
                gmail_takeout,
                member_path="Takeout/Mail/All mail Including Spam and Trash.mbox",
                start_month=start_month,
                end_month=end_month,
            )

    return LifeTakeoutBundle(
        google_search_counts=dict(google_search_counts),
        google_search_tokens=dict(google_search_tokens),
        google_search_phrases=dict(google_search_phrases),
        youtube_watch_counts=dict(youtube_watch_counts),
        youtube_search_counts=dict(youtube_search_counts),
        youtube_search_tokens=dict(youtube_search_tokens),
        youtube_search_phrases=dict(youtube_search_phrases),
        youtube_video_titles=dict(youtube_video_titles),
        youtube_watch_history_counts=dict(youtube_watch_history_counts),
        youtube_watch_history_video_ids=dict(youtube_watch_history_video_ids),
        youtube_watch_history_titles=dict(youtube_watch_history_titles),
        youtube_watch_history_channels=dict(youtube_watch_history_channels),
        youtube_search_history_counts=dict(youtube_search_history_counts),
        youtube_search_history_tokens=dict(youtube_search_history_tokens),
        youtube_search_history_phrases=dict(youtube_search_history_phrases),
        chrome_counts=dict(chrome_counts),
        maps_counts=dict(maps_counts),
        maps_tokens=dict(maps_tokens),
        maps_phrases=dict(maps_phrases),
        image_search_counts=dict(image_search_counts),
        image_search_tokens=dict(image_search_tokens),
        image_search_phrases=dict(image_search_phrases),
        play_store_counts=dict(play_store_counts),
        play_store_tokens=dict(play_store_tokens),
        play_store_phrases=dict(play_store_phrases),
        video_search_counts=dict(video_search_counts),
        video_search_tokens=dict(video_search_tokens),
        video_search_phrases=dict(video_search_phrases),
        shopping_counts=dict(shopping_counts),
        shopping_tokens=dict(shopping_tokens),
        shopping_phrases=dict(shopping_phrases),
        travel_counts=dict(travel_counts),
        travel_tokens=dict(travel_tokens),
        travel_phrases=dict(travel_phrases),
        myactivity_other_counts=dict(myactivity_other_counts),
        chrome_history_counts=dict(chrome_history_counts),
        chrome_history_domains=dict(chrome_history_domains),
        chrome_history_reddit_subs=dict(chrome_history_reddit_subs),
        chrome_history_title_tokens=dict(chrome_history_title_tokens),
        location_records=dict(location_records),
        semantic_place_visits=dict(semantic_place_visits),
        semantic_activity_segments=dict(semantic_activity_segments),
        semantic_top_places=dict(semantic_top_places),
        semantic_top_activities=dict(semantic_top_activities),
        gmail_counts=dict(gmail_counts),
        gmail_from_domains=dict(gmail_from_domains),
        gmail_subject_tokens=dict(gmail_subject_tokens),
        location_takeout_path=location_takeout_path,
        gmail_takeout_path=gmail_takeout_path,
        chrome_history_takeout_path=chrome_history_takeout_path,
        youtube_video_texts_takeout_path=youtube_video_texts_takeout_path,
    )
