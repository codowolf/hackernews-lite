from datetime import UTC, datetime, timedelta
from typing import Any

from hackernews_lite.config import Trending
from hackernews_lite.domain.models import Story
from hackernews_lite.domain.trending import heat, is_trending, points_per_hour
from tests.conftest import make_story

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
RULE = Trending(min_points_per_hour=30, min_comments=500, min_points=100)


def story(points: int, hours_old: float, comments: int = 10, **kw: Any) -> Story:
    return make_story(
        points=points,
        num_comments=comments,
        created_at=NOW - timedelta(hours=hours_old),
        **kw,
    )


def test_fast_climbers_are_hot() -> None:
    assert is_trending(story(points=400, hours_old=8), RULE, NOW)  # 50 per hour


def test_slow_burners_are_not() -> None:
    assert not is_trending(story(points=400, hours_old=20), RULE, NOW)  # 20 per hour


def test_very_busy_discussions_are_hot_even_if_slow() -> None:
    assert is_trending(story(points=300, hours_old=20, comments=600), RULE, NOW)


def test_tiny_stories_are_never_hot() -> None:
    assert not is_trending(story(points=60, hours_old=1, comments=900), RULE, NOW)


def test_brand_new_stories_are_not_inflated() -> None:
    # 120 points in 6 minutes would be 1200 per hour; ages under an hour count as an hour
    assert points_per_hour(story(points=120, hours_old=0.1), NOW) == 120


def test_age_is_measured_to_when_the_points_were_read() -> None:
    read_at = NOW - timedelta(hours=10)
    fresh = story(points=400, hours_old=18, fetched_at=read_at)  # 8 hours old when read
    assert points_per_hour(fresh, NOW) == 50


def test_each_number_is_hot_for_its_own_reason() -> None:
    climbing = heat(story(points=400, hours_old=8, comments=20), RULE, NOW)
    assert (climbing.points, climbing.comments) == (True, False)
    busy = heat(story(points=300, hours_old=20, comments=600), RULE, NOW)
    assert (busy.points, busy.comments) == (False, True)
    both = heat(story(points=900, hours_old=5, comments=700), RULE, NOW)
    assert (both.points, both.comments) == (True, True)
