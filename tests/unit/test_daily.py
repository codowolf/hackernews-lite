import pytest
from pydantic import ValidationError

from hackernews_lite.config import DayMode
from hackernews_lite.domain.daily import pick_for_mode
from tests.conftest import make_story


def day(points: list[int]):  # type: ignore[no-untyped-def]
    return [make_story(i + 1, points=p) for i, p in enumerate(points)]


def ids(stories) -> list[int]:  # type: ignore[no-untyped-def]
    return [s.id for s in stories]


def test_a_count_takes_the_best_n_by_points() -> None:
    mode = DayMode(id="t", label="T", count=2)
    assert ids(pick_for_mode(day([10, 300, 50, 200]), mode)) == [2, 4]


def test_a_count_larger_than_the_day_takes_everything() -> None:
    assert len(pick_for_mode(day([5, 6]), DayMode(id="t", label="T", count=10))) == 2


def test_a_percent_takes_that_share_rounding_up() -> None:
    half = DayMode(id="h", label="H", percent=50)
    assert len(pick_for_mode(day([1] * 10), half)) == 5
    assert len(pick_for_mode(day([1] * 9), half)) == 5  # 4.5 rounds up


def test_a_percent_of_a_tiny_day_still_shows_one_story() -> None:
    assert len(pick_for_mode(day([7]), DayMode(id="p", label="P", percent=10))) == 1


def test_no_count_and_no_percent_means_all_best_first() -> None:
    assert ids(pick_for_mode(day([1, 3, 2]), DayMode(id="a", label="A"))) == [2, 3, 1]


def test_an_empty_day_stays_empty_in_every_mode() -> None:
    for mode in (
        DayMode(id="c", label="C", count=5),
        DayMode(id="p", label="P", percent=50),
        DayMode(id="a", label="A"),
    ):
        assert pick_for_mode([], mode) == []


def test_equal_points_put_the_newer_story_first() -> None:
    from datetime import UTC, datetime

    older = make_story(1, points=100, created_at=datetime(2026, 10, 12, 1, tzinfo=UTC))
    newer = make_story(2, points=100, created_at=datetime(2026, 10, 12, 9, tzinfo=UTC))
    assert ids(pick_for_mode([older, newer], DayMode(id="a", label="A"))) == [2, 1]


@pytest.mark.parametrize(
    "fields", [{"count": 5, "percent": 50}, {"count": -1}, {"percent": 150}, {"percent": -5}]
)
def test_a_mode_must_make_sense(fields: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        DayMode(id="x", label="X", **fields)
