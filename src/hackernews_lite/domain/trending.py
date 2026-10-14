"""Pure rule for which stories are "hot". Thresholds come from config; nothing here does I/O."""

from dataclasses import dataclass
from datetime import UTC, datetime

from hackernews_lite.config import Trending
from hackernews_lite.domain.models import Story

MIN_AGE_HOURS = 1.0  # a story posted minutes ago would otherwise look infinitely fast


def points_per_hour(story: Story, now: datetime | None = None) -> float:
    """Average upvotes per hour, measured to when the points were read (not to today)."""
    measured_at = story.fetched_at or now or datetime.now(UTC)
    age_hours = max((measured_at - story.created_at).total_seconds() / 3600, MIN_AGE_HOURS)
    return story.points / age_hours


@dataclass(frozen=True)
class Heat:
    """Which of a story's two numbers is running hot."""

    points: bool  # upvotes are climbing fast
    comments: bool  # a very busy discussion


def heat(story: Story, rule: Trending, now: datetime | None = None) -> Heat:
    if story.points < rule.min_points:
        return Heat(points=False, comments=False)
    return Heat(
        points=points_per_hour(story, now) >= rule.min_points_per_hour,
        comments=story.num_comments >= rule.min_comments,
    )


def is_trending(story: Story, rule: Trending, now: datetime | None = None) -> bool:
    hot = heat(story, rule, now)
    return hot.points or hot.comments
