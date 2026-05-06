from datetime import date

from lynchpin.sources.exports import (
    daily_messenger_activity,
    daily_raindrop_activity,
)


def test_daily_messenger_returns_list():
    result = daily_messenger_activity(start=date(2024, 3, 1), end=date(2024, 12, 31))
    assert isinstance(result, list)


def test_daily_raindrop_returns_list():
    result = daily_raindrop_activity(start=date(2020, 1, 1), end=date(2025, 12, 31))
    assert isinstance(result, list)


def test_messenger_day_activity_fields():
    result = daily_messenger_activity(start=date(2024, 3, 1), end=date(2024, 12, 31))
    if result:
        day = result[0]
        assert hasattr(day, "message_count")
        assert hasattr(day, "thread_count")
        assert hasattr(day, "sent_count")
        assert day.message_count > 0


def test_raindrop_day_activity_fields():
    result = daily_raindrop_activity(start=date(2020, 1, 1), end=date(2025, 12, 31))
    if result:
        day = result[0]
        assert hasattr(day, "bookmarks_added")
        assert day.bookmarks_added > 0
