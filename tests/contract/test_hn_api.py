"""Runs the real parsing code against responses recorded from the live Algolia API."""

import json
from datetime import UTC, datetime

import httpx2 as httpx
import pytest

from hackernews_lite.sources.hn_api import BASE_URL, FIREBASE_URL, AlgoliaHn
from tests.conftest import FIXTURES


def client_returning(fixture: str) -> httpx.Client:
    payload = json.loads((FIXTURES / fixture).read_text())
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    return httpx.Client(base_url=BASE_URL, transport=transport)


def firebase_ranking(order: dict[int, list[int]] | None = None) -> httpx.Client:
    """A stand-in for HN's own API, which lists each item's comments in ranked display order."""

    def handler(request: httpx.Request) -> httpx.Response:
        item_id = int(request.url.path.split("/")[-1].removesuffix(".json"))
        return httpx.Response(200, json={"id": item_id, "kids": (order or {}).get(item_id, [])})

    return httpx.Client(base_url=FIREBASE_URL, transport=httpx.MockTransport(handler))


def test_thread_parses_story_and_comment_tree() -> None:
    hn = AlgoliaHn(client_returning("algolia_item.json"), firebase_ranking())
    story, comments = hn.thread(50054207)
    assert story.id == 50054207
    assert story.title.startswith("Deloitte")
    assert story.num_comments > 0
    assert comments, "expected top-level comments"
    assert all(c.author and c.text and c.depth == 0 for c in comments)
    assert all(child.depth == 1 for c in comments for child in c.children)
    assert all("<p>" not in c.text for c in comments)


def test_deleted_comments_are_dropped() -> None:
    hn = AlgoliaHn(client_returning("algolia_item.json"), firebase_ranking())
    _, comments = hn.thread(50054207)
    assert 1 not in {c.id for c in comments}


def test_top_stories_sorted_by_points_and_limited() -> None:
    hn = AlgoliaHn(client_returning("algolia_search.json"))
    stories = hn.top_stories(limit=2, min_points=0, window_hours=24)
    assert len(stories) == 2
    assert stories[0].points >= stories[1].points


# ---------- feeds: front page, best, and paging through a time window ----------


def hit(story_id: int, points: int) -> dict[str, object]:
    return {
        "objectID": str(story_id),
        "title": f"Story {story_id}",
        "url": f"https://example.com/{story_id}",
        "author": "a",
        "points": points,
        "num_comments": 1,
        "created_at_i": 1_760_000_000,
    }


def routed(handler: object) -> AlgoliaHn:
    algolia = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    firebase = httpx.Client(
        base_url=FIREBASE_URL,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return AlgoliaHn(algolia, firebase)


def test_front_page_asks_for_the_front_page_tag_and_keeps_its_order() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"hits": [hit(3, 10), hit(1, 500), hit(2, 50)]})

    stories = routed(handler).front_page(limit=30)
    assert [s.id for s in stories] == [3, 1, 2]  # rank order, not points order
    assert seen[0].url.params["tags"] == "front_page" and seen[0].url.params["hitsPerPage"] == "30"


def test_best_uses_the_best_list_order_and_asks_algolia_for_those_ids() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("beststories.json"):
            return httpx.Response(200, json=[30, 10, 20, 40])
        return httpx.Response(200, json={"hits": [hit(10, 5), hit(20, 9), hit(30, 1)]})

    stories = routed(handler).best(limit=3)
    assert [s.id for s in stories] == [30, 10, 20]
    assert seen[1].url.params["tags"] == "story,(story_30,story_10,story_20)"


def test_best_with_an_empty_list_makes_no_second_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=[])

    assert routed(handler).best(limit=20) == []
    assert len(calls) == 1


def test_a_window_pages_through_results_then_ranks_by_points() -> None:
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(request.url.params["numericFilters"])
        batches = {0: [hit(1, 100), hit(2, 900)], 1: [hit(3, 500)]}
        return httpx.Response(200, json={"hits": batches[page], "nbPages": 2})

    stories = routed(handler).top_stories(limit=2, min_points=100, window_hours=48)
    assert [s.id for s in stories] == [2, 3]  # best by points across both pages
    assert len(pages) == 2 and all("points>=100" in p for p in pages)


def test_hn_errors_become_source_errors() -> None:

    from hackernews_lite.domain.errors import SourceError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(SourceError):
        routed(handler).front_page(limit=5)
    with pytest.raises(SourceError):
        routed(handler).best(limit=5)


# ---------- comment order: Algolia is oldest-first, Hacker News ranks them ----------


def test_comments_are_put_in_hacker_news_ranked_order_not_posting_order() -> None:
    algolia = client_returning("algolia_item.json")
    _, posted_order = AlgoliaHn(algolia, firebase_ranking()).thread(50054207)
    ids = [c.id for c in posted_order]
    assert len(ids) >= 3

    ranked = [ids[2], ids[0], ids[1]]  # HN says the third-posted comment is the best
    _, comments = AlgoliaHn(algolia, firebase_ranking({50054207: ranked})).thread(50054207)
    assert [c.id for c in comments][:3] == ranked
    assert {c.id for c in comments} == set(ids)  # nothing lost or duplicated


def test_replies_are_ranked_too() -> None:
    algolia = client_returning("algolia_item.json")
    _, comments = AlgoliaHn(algolia, firebase_ranking()).thread(50054207)
    parent = next(c for c in comments if len(c.children) >= 2)
    posted = [c.id for c in parent.children]

    reversed_replies = list(reversed(posted))
    _, ranked = AlgoliaHn(algolia, firebase_ranking({parent.id: reversed_replies})).thread(50054207)
    assert [c.id for c in next(c for c in ranked if c.id == parent.id).children] == reversed_replies


def test_comments_missing_from_the_ranking_keep_their_order_at_the_end() -> None:
    algolia = client_returning("algolia_item.json")
    _, comments = AlgoliaHn(algolia, firebase_ranking()).thread(50054207)
    ids = [c.id for c in comments]
    _, partial = AlgoliaHn(algolia, firebase_ranking({50054207: [ids[-1]]})).thread(50054207)
    assert [c.id for c in partial] == [ids[-1], *ids[:-1]]


def test_if_the_ranking_is_unavailable_the_story_fails_loudly() -> None:

    from hackernews_lite.domain.errors import SourceError

    down = httpx.Client(
        base_url=FIREBASE_URL, transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    with pytest.raises(SourceError):
        AlgoliaHn(client_returning("algolia_item.json"), down).thread(50054207)


# ---------- the Daily list: every story of one UTC day ----------

DAY = datetime(2026, 10, 12, tzinfo=UTC)  # a Monday, 00:00 UTC


def day_hit(story_id: int, points: int, day: datetime = DAY, offset: int = 0) -> dict[str, object]:
    return {**hit(story_id, points), "created_at_i": int(day.timestamp()) + offset or story_id}


def routed_day(handler: object) -> AlgoliaHn:
    algolia = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return AlgoliaHn(algolia, firebase_ranking())


def test_a_day_is_asked_for_as_one_utc_window_with_the_minimum_points() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"hits": [day_hit(1, 40), day_hit(2, 300), day_hit(3, 90)], "nbPages": 1}
        )

    stories = routed_day(handler).day_stories(DAY.date(), min_points=30)

    filters = seen[0].url.params["numericFilters"].split(",")
    start = int(DAY.timestamp())
    assert filters == [f"created_at_i>={start}", f"created_at_i<{start + 86_400}", "points>=30"]
    assert seen[0].url.params["tags"] == "story"
    assert [s.id for s in stories] == [2, 3, 1]  # best first


def test_a_busy_day_is_paged_through() -> None:
    pages = {0: [day_hit(1, 50), day_hit(2, 60)], 1: [day_hit(3, 500)]}
    asked: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        asked.append(page)
        return httpx.Response(200, json={"hits": pages[page], "nbPages": 2})

    stories = routed_day(handler).day_stories(DAY.date(), min_points=30)
    assert asked == [0, 1]
    assert [s.id for s in stories] == [3, 2, 1]  # ranked across both pages


def test_equal_points_put_the_newer_story_first() -> None:
    older = day_hit(1, 100, offset=100)
    newer = day_hit(2, 100, offset=5000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": [older, newer], "nbPages": 1})

    stories = routed_day(handler).day_stories(DAY.date(), min_points=0)
    assert [s.id for s in stories] == [2, 1]


def test_an_empty_day_is_just_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": [], "nbPages": 0})

    assert routed_day(handler).day_stories(DAY.date(), min_points=30) == []


def test_a_failed_day_request_is_a_source_error() -> None:

    from hackernews_lite.domain.errors import SourceError

    with pytest.raises(SourceError):
        routed_day(lambda request: httpx.Response(500)).day_stories(DAY.date(), min_points=30)


def test_comments_keep_the_real_address_of_their_links() -> None:
    item = {
        "id": 1,
        "type": "story",
        "title": "T",
        "created_at_i": 1_760_000_000,
        "children": [
            {
                "id": 2,
                "author": "a",
                "text": 'Read <a href="https://example.com/long/path">https://example.com/lo...</a>',
                "children": [],
            }
        ],
    }
    hn = routed(lambda request: httpx.Response(200, json=item))
    _, comments = hn.thread(1)
    assert comments[0].text == "Read https://example.com/lo..."
    assert [(link.text, link.href) for link in comments[0].links] == [
        ("https://example.com/lo...", "https://example.com/long/path")
    ]
