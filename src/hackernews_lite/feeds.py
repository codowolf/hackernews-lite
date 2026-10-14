"""Feeds: which stories to show. Fetches a list from Hacker News and saves it for the app."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from hackernews_lite.config import Daily, DayMode, Feed, Feeds
from hackernews_lite.domain.daily import pick_for_mode
from hackernews_lite.domain.errors import HnListenerError, SourceError
from hackernews_lite.domain.models import FeedSnapshot, Story
from hackernews_lite.sources.hn_api import HnSource
from hackernews_lite.storage.story_store import StoryStore

# How long a saved day stays fresh. Today's points are still climbing; old days barely move.
TODAY_MAX_AGE = timedelta(minutes=30)
YESTERDAY_MAX_AGE = timedelta(hours=3)
OLDER_MAX_AGE = timedelta(hours=24)
DAY_FETCH_WORKERS = 6


class UnknownFeedError(HnListenerError):
    """The requested feed is not defined in config.toml."""


@dataclass(frozen=True)
class DayGroup:
    day: date
    stories: list[Story]  # what the chosen mode shows for this day, best first
    pool: int  # how many stories that day reached the minimum points


@dataclass(frozen=True)
class DailyList:
    groups: list[DayGroup]  # newest day first
    has_more: bool  # older days can still be asked for
    updated_at: datetime  # when the newest day was read


class FeedService:
    def __init__(self, *, hn: HnSource, store: StoryStore, feeds: Feeds, daily: Daily) -> None:
        self._hn = hn
        self._store = store
        self._feeds = feeds
        self._daily = daily

    def definition(self, feed_id: str) -> Feed:
        feed = self._feeds.get(feed_id)
        if feed is None:
            raise UnknownFeedError(f"Unknown feed '{feed_id}'")
        return feed

    def snapshot(self, feed_id: str) -> FeedSnapshot | None:
        return self._store.load_feed(feed_id)

    def is_stale(self, snapshot: FeedSnapshot, now: datetime | None = None) -> bool:
        age = (now or datetime.now(UTC)) - snapshot.fetched_at
        return age > timedelta(minutes=self._feeds.max_age_minutes)

    def refresh(self, feed_id: str, *, limit: int | None = None) -> list[Story]:
        """Fetch the feed's current stories, save them, and remember their order."""
        feed = self.definition(feed_id)
        count = limit or feed.limit
        if feed.kind == "front_page":
            stories = self._hn.front_page(limit=count)
        elif feed.kind == "best":
            stories = self._hn.best(limit=count)
        elif feed.kind == "daily":
            groups = self.daily(self._daily.default_mode, self._daily.page_days).groups
            stories = [story for group in groups for story in group.stories]
            if limit:  # only an explicit limit (the CLI's --top) trims it
                stories = stories[:limit]
        else:
            stories = self._hn.top_stories(
                limit=count, min_points=feed.min_points, window_hours=feed.window_hours
            )
        now = datetime.now(UTC)
        stamped = [story.model_copy(update={"fetched_at": now}) for story in stories]
        for story in stamped:
            self._store.save_story(story)
        self._store.save_feed(
            FeedSnapshot(id=feed_id, fetched_at=now, story_ids=[story.id for story in stamped])
        )
        return stamped

    # --- the Daily list -----------------------------------------------------------------

    def day_mode(self, mode_id: str) -> DayMode:
        mode = self._daily.mode(mode_id)
        if mode is None:
            raise UnknownFeedError(f"Unknown day mode '{mode_id}'")
        return mode

    def daily(self, mode_id: str, days: int, *, now: datetime | None = None) -> DailyList:
        """The last `days` UTC days, each showing what `mode_id` selects. Days are saved/reused."""
        mode = self.day_mode(mode_id)
        days = max(1, min(days, self._daily.max_days))
        clock = now or datetime.now(UTC)
        today = clock.date()
        wanted = [today - timedelta(days=offset) for offset in range(days)]

        snapshots = self._load_days(wanted, today, clock)
        groups: list[DayGroup] = []
        for day in wanted:
            pool = [
                story
                for story_id in snapshots[day].story_ids
                if (story := self._store.load_story(story_id)) is not None
            ]
            groups.append(DayGroup(day=day, stories=pick_for_mode(pool, mode), pool=len(pool)))
        return DailyList(
            groups=groups,
            has_more=days < self._daily.max_days,
            updated_at=snapshots[wanted[0]].fetched_at,
        )

    def _load_days(
        self, wanted: list[date], today: date, clock: datetime
    ) -> dict[date, FeedSnapshot]:
        saved = {day: self._store.load_feed(_day_key(day)) for day in wanted}
        stale = [
            day
            for day, snapshot in saved.items()
            if snapshot is None or clock - snapshot.fetched_at > _max_age(day, today)
        ]
        with ThreadPoolExecutor(max_workers=DAY_FETCH_WORKERS) as pool:
            fetched = list(pool.map(lambda day: self._fetch_day(day, saved[day], clock), stale))
        fresh = dict(zip(stale, fetched, strict=True))
        result: dict[date, FeedSnapshot] = {}
        for day in wanted:
            snapshot = fresh.get(day) or saved[day]
            assert snapshot is not None  # a day is either fetched, or was already saved
            result[day] = snapshot
        return result

    def _fetch_day(self, day: date, fallback: FeedSnapshot | None, now: datetime) -> FeedSnapshot:
        try:
            stories = self._hn.day_stories(day, min_points=self._daily.min_points)
        except SourceError:
            if fallback is None:
                raise  # nothing saved for this day, so there is nothing honest to show
            return fallback  # Hacker News is unreachable: show what was saved
        stamped = [story.model_copy(update={"fetched_at": now}) for story in stories]
        for story in stamped:
            self._store.save_story(story)
        snapshot = FeedSnapshot(
            id=_day_key(day), fetched_at=now, story_ids=[story.id for story in stamped]
        )
        self._store.save_feed(snapshot)
        return snapshot


def _day_key(day: date) -> str:
    return f"day_{day.isoformat()}"


def _max_age(day: date, today: date) -> timedelta:
    if day >= today:
        return TODAY_MAX_AGE
    return YESTERDAY_MAX_AGE if day == today - timedelta(days=1) else OLDER_MAX_AGE
