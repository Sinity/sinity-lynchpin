"""Facade for export-style data sources and knowledgebase notes."""

from __future__ import annotations

from .exports_dendron import DendronNote, iter_dendron_notes
from .exports_goodreads import GoodreadsBook, iter_goodreads_books, summarize_goodreads_library
from .exports_messenger import (
    MessengerDayActivity,
    MessengerMessage,
    MessengerThread,
    daily_messenger_activity,
    iter_fbmessenger_messages,
    iter_fbmessenger_threads,
)
from .exports_raindrop import (
    RaindropBookmark,
    RaindropDayActivity,
    RaindropExport,
    daily_raindrop_activity,
    iter_raindrop_bookmarks,
    iter_raindrop_bookmarks_all,
    iter_raindrop_bookmarks_by_name,
    list_raindrop_exports,
    summarize_raindrop_bookmarks,
)
from .exports_wykop import (
    WykopActivitySummary,
    WykopEntry,
    WykopEntryComment,
    WykopLinkComment,
    iter_wykop_entries,
    iter_wykop_entry_comments,
    iter_wykop_link_comments,
    summarize_wykop_activity,
)

__all__ = [
    "DendronNote",
    "GoodreadsBook",
    "MessengerDayActivity",
    "MessengerMessage",
    "MessengerThread",
    "RaindropBookmark",
    "RaindropDayActivity",
    "RaindropExport",
    "WykopActivitySummary",
    "WykopEntry",
    "WykopEntryComment",
    "WykopLinkComment",
    "daily_messenger_activity",
    "daily_raindrop_activity",
    "iter_dendron_notes",
    "iter_fbmessenger_messages",
    "iter_fbmessenger_threads",
    "iter_goodreads_books",
    "iter_raindrop_bookmarks",
    "iter_raindrop_bookmarks_all",
    "iter_raindrop_bookmarks_by_name",
    "iter_wykop_entries",
    "iter_wykop_entry_comments",
    "iter_wykop_link_comments",
    "list_raindrop_exports",
    "summarize_goodreads_library",
    "summarize_raindrop_bookmarks",
    "summarize_wykop_activity",
]
