"""Pure rules for the Daily list: how much of each day to show."""

import math

from hackernews_lite.config import DayMode
from hackernews_lite.domain.models import Story


def pick_for_mode(stories: list[Story], mode: DayMode) -> list[Story]:
    """The part of one day's stories this mode shows, best first.

    `stories` are the day's stories that reached the minimum points. A count takes the best N.
    A percent takes that share of them (at least one if there are any). Neither means all.
    """
    ranked = sorted(stories, key=lambda story: (-story.points, -story.created_at.timestamp()))
    if mode.count:
        return ranked[: mode.count]
    if mode.percent:
        # The tiny epsilon stops float noise turning an exact 50% of 10 into 6.
        keep = max(1, math.ceil(len(ranked) * mode.percent / 100 - 1e-9)) if ranked else 0
        return ranked[:keep]
    return ranked
