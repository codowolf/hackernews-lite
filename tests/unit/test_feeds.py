from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from hackernews_lite.config import Daily, DayMode, Feeds
from hackernews_lite.domain.errors import SourceError
from hackernews_lite.domain.models import FeedSnapshot, Story
from hackernews_lite.feeds import FeedService, UnknownFeedError
from hackernews_lite.storage.story_store import StoryStore
from tests.conftest import FakeHn, make_story


def service(store: StoryStore) -> tuple[FeedService, FakeHn]:
    hn = FakeHn([make_story(1, points=300), make_story(2, points=100)], [])
    return FeedService(hn=hn, store=store, feeds=Feeds(), daily=Daily()), hn


def test_default_feeds_cover_every_choice_the_app_offers() -> None:
    feeds = Feeds()
    assert [f.id for f in feeds.items] == ["daily", "top", "24h", "48h", "week", "best"]
    assert feeds.get("week") is not None and feeds.get("week").window_hours == 168  # type: ignore[union-attr]
    assert feeds.get("nope") is None


@pytest.mark.parametrize(
    ("feed_id", "expected_call"),
    [
        ("top", "front_page"),
        ("24h", "window:24h>=50"),
        ("48h", "window:48h>=100"),
        ("week", "window:168h>=200"),
        ("best", "best"),
    ],
)
def test_each_feed_makes_its_own_query(store: StoryStore, feed_id: str, expected_call: str) -> None:
    feeds, hn = service(store)
    feeds.refresh(feed_id)
    assert [call for call, _ in hn.calls] == [expected_call]


def test_refresh_saves_stories_and_their_order(store: StoryStore) -> None:
    feeds, _ = service(store)
    stories = feeds.refresh("24h")

    snapshot = store.load_feed("24h")
    assert snapshot is not None and snapshot.story_ids == [1, 2]
    assert store.load_story(1) is not None
    assert all(s.fetched_at is not None for s in stories)  # points are dated, so "hot" stays honest


def test_limit_overrides_the_feed_size(store: StoryStore) -> None:
    feeds, hn = service(store)
    feeds.refresh("24h", limit=5)
    assert hn.calls == [("window:24h>=50", 5)]


def test_staleness_follows_the_configured_age() -> None:
    feeds = FeedService(
        hn=FakeHn([], []),
        store=None,  # type: ignore[arg-type]
        feeds=Feeds(max_age_minutes=60),
        daily=Daily(),
    )
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)
    fresh = FeedSnapshot(id="x", fetched_at=now - timedelta(minutes=30), story_ids=[])
    old = FeedSnapshot(id="x", fetched_at=now - timedelta(minutes=90), story_ids=[])
    assert not feeds.is_stale(fresh, now) and feeds.is_stale(old, now)


def test_unknown_feed_is_an_error(store: StoryStore) -> None:
    feeds, _ = service(store)
    with pytest.raises(UnknownFeedError):
        feeds.refresh("nope")


# ---------- the Daily list ----------

TODAY = datetime(2026, 10, 12, 12, tzinfo=UTC)
D0, D1, D2 = date(2026, 10, 12), date(2026, 10, 11), date(2026, 10, 10)


def stories_on(day: date, points: list[int], first_id: int) -> list[Story]:
    at = datetime(day.year, day.month, day.day, 9, tzinfo=UTC)
    return [
        make_story(first_id + i, points=p, created_at=at + timedelta(minutes=i))
        for i, p in enumerate(points)
    ]


def daily_service(store: StoryStore, **daily: Any) -> tuple[FeedService, FakeHn]:
    hn = FakeHn([], [])
    hn.by_day = {
        D0: stories_on(D0, [500, 120, 80], 100),
        D1: stories_on(D1, [900, 700, 60, 40], 200),
        D2: stories_on(D2, [300], 300),
    }
    return FeedService(hn=hn, store=store, feeds=Feeds(), daily=Daily(**daily)), hn


def test_each_day_is_its_own_group_newest_first(store: StoryStore) -> None:
    feeds, _ = daily_service(store)
    result = feeds.daily("top10", 3, now=TODAY)
    assert [g.day for g in result.groups] == [D0, D1, D2]
    assert [[s.points for s in g.stories] for g in result.groups] == [
        [500, 120, 80],
        [900, 700, 60, 40],
        [300],
    ]
    assert [g.pool for g in result.groups] == [3, 4, 1]


def test_the_mode_decides_how_much_of_each_day_shows(store: StoryStore) -> None:
    feeds, _ = daily_service(store)
    assert [len(g.stories) for g in feeds.daily("top5", 3, now=TODAY).groups] == [3, 4, 1]

    modes = [
        DayMode(id="top2", label="Top 2", count=2),
        DayMode(id="half", label="Half", percent=50),
        DayMode(id="every", label="Every story"),
    ]
    custom, _ = daily_service(store, modes=modes)
    assert [len(g.stories) for g in custom.daily("top2", 3, now=TODAY).groups] == [2, 2, 1]
    assert [len(g.stories) for g in custom.daily("half", 3, now=TODAY).groups] == [2, 2, 1]
    assert [len(g.stories) for g in custom.daily("every", 3, now=TODAY).groups] == [3, 4, 1]


def test_days_are_saved_so_the_second_request_asks_hacker_news_nothing(store: StoryStore) -> None:
    feeds, hn = daily_service(store)
    feeds.daily("top10", 3, now=TODAY)
    assert len(hn.calls) == 3 and all(c.endswith(">=30") for c, _ in hn.calls)

    hn.calls.clear()
    feeds.daily("top20", 3, now=TODAY)  # a different mode reuses the same saved days
    assert hn.calls == []


def test_the_pool_uses_the_configured_minimum_points(store: StoryStore) -> None:
    feeds, hn = daily_service(store, min_points=75)
    feeds.daily("top10", 1, now=TODAY)
    assert hn.calls == [("day:2026-10-12>=75", 0)]


def test_today_goes_stale_quickly_and_old_days_slowly(store: StoryStore) -> None:
    feeds, hn = daily_service(store)
    feeds.daily("top10", 3, now=TODAY)  # saved at TODAY (noon, Oct 12)
    hn.calls.clear()

    feeds.daily("top10", 3, now=TODAY + timedelta(minutes=20))
    assert hn.calls == []  # nothing is old enough yet

    feeds.daily("top10", 3, now=TODAY + timedelta(minutes=40))
    assert [c for c, _ in hn.calls] == ["day:2026-10-12>=30"]  # only today passed 30 minutes
    hn.calls.clear()

    feeds.daily("top10", 3, now=TODAY + timedelta(hours=4))  # yesterday passed 3 hours too
    assert sorted(c for c, _ in hn.calls) == ["day:2026-10-11>=30", "day:2026-10-12>=30"]
    hn.calls.clear()

    # A day and a half later the calendar has moved on: a new "today", and the saved days
    # (now yesterday, and a day that has passed its 24 hours) are all out of date.
    feeds.daily("top10", 3, now=TODAY + timedelta(hours=30))
    assert sorted(c for c, _ in hn.calls) == [
        "day:2026-10-11>=30",
        "day:2026-10-12>=30",
        "day:2026-10-13>=30",
    ]


def test_a_day_that_cannot_be_loaded_falls_back_to_what_was_saved(store: StoryStore) -> None:
    feeds, hn = daily_service(store)
    feeds.daily("top10", 2, now=TODAY)
    hn.failing_days = {D0}
    later = TODAY + timedelta(hours=2)  # today's saved copy is now stale
    result = feeds.daily("top10", 2, now=later)
    assert next(g.day for g in result.groups) == D0  # shown from the saved copy
    assert [s.points for s in result.groups[0].stories] == [500, 120, 80]


def test_a_day_with_nothing_saved_and_no_connection_is_an_error(store: StoryStore) -> None:
    feeds, hn = daily_service(store)
    hn.failing_days = {D0}
    with pytest.raises(SourceError):
        feeds.daily("top10", 1, now=TODAY)


def test_days_are_limited_and_the_list_says_when_older_days_remain(store: StoryStore) -> None:
    feeds, _ = daily_service(store, max_days=5)
    assert len(feeds.daily("top10", 99, now=TODAY).groups) == 5
    assert feeds.daily("top10", 99, now=TODAY).has_more is False
    assert feeds.daily("top10", 2, now=TODAY).has_more is True
    assert len(feeds.daily("top10", 0, now=TODAY).groups) == 1  # at least today


def test_an_unknown_mode_is_an_error(store: StoryStore) -> None:
    feeds, _ = daily_service(store)
    with pytest.raises(UnknownFeedError):
        feeds.daily("nope", 3, now=TODAY)


def test_the_cli_can_still_refresh_the_daily_feed_as_a_flat_list(store: StoryStore) -> None:
    feeds, _ = daily_service(store)
    stories = feeds.refresh("daily")
    assert stories and store.load_feed("daily") is not None
    assert [s.id for s in feeds.refresh("daily", limit=2)] == [s.id for s in stories][:2]


def test_only_the_daily_feed_is_grouped() -> None:
    assert {f.id: f.grouped for f in Feeds().items} == {
        "daily": True,
        "top": False,
        "24h": False,
        "48h": False,
        "week": False,
        "best": False,
    }


def test_config_toml_defines_the_modes_and_makes_daily_the_default() -> None:
    from hackernews_lite.config import load_settings
    from tests.conftest import PROJECT_ROOT

    settings = load_settings(PROJECT_ROOT / "config.toml")
    assert settings.feeds.default == "daily" and settings.feeds.items[0].id == "daily"
    assert [m.id for m in settings.daily.modes] == ["top5", "top10", "top20"]
    assert settings.daily.default_mode == "top5"
