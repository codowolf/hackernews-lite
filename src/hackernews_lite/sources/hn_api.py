"""Hacker News via the Algolia API: story discovery, and a full comment tree in one call."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import httpx2 as httpx

from hackernews_lite.domain.errors import SourceError
from hackernews_lite.domain.models import Comment, Story
from hackernews_lite.domain.text import extract_links, html_to_text

BASE_URL = "https://hn.algolia.com/api/v1"
FIREBASE_URL = "https://hacker-news.firebaseio.com/v0"
PAGE_SIZE = 200
MAX_PAGES = 5  # at most 1000 candidates when ranking a time window by points
# For a day's top stories: try these point floors, highest first, and stop at the first one
# that leaves enough stories. A high floor keeps the request small on a normal day; 0 is the
# fallback for a quiet day.
RANKED_THREADS = 25  # top-level comments whose replies are also put in HN's ranked order


class HnSource(Protocol):
    def front_page(self, *, limit: int) -> list[Story]:
        """The live front page, in rank order."""
        ...

    def top_stories(self, *, limit: int, min_points: int, window_hours: int) -> list[Story]:
        """The most upvoted stories posted within the last `window_hours`."""
        ...

    def best(self, *, limit: int) -> list[Story]:
        """Hacker News's own /best list, in order."""
        ...

    def day_stories(self, day: date, *, min_points: int) -> list[Story]:
        """Every story posted on the UTC `day` with at least `min_points`, best first."""
        ...

    def thread(self, story_id: int) -> tuple[Story, list[Comment]]:
        """The story and its comments, top-level and replies in Hacker News's ranked order."""
        ...


class AlgoliaHn:
    def __init__(
        self, client: httpx.Client | None = None, firebase: httpx.Client | None = None
    ) -> None:
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=20.0)
        self._firebase = firebase or httpx.Client(base_url=FIREBASE_URL, timeout=20.0)

    def front_page(self, *, limit: int) -> list[Story]:
        data = self._get("/search", {"tags": "front_page", "hitsPerPage": limit})
        return [_story_from_hit(hit) for hit in data.get("hits", [])][:limit]

    def top_stories(self, *, limit: int, min_points: int, window_hours: int) -> list[Story]:
        since = int(time.time()) - window_hours * 3600
        stories: list[Story] = []
        for page in range(MAX_PAGES):
            data = self._get(
                "/search",
                {
                    "tags": "story",
                    "numericFilters": f"created_at_i>{since},points>={min_points}",
                    "hitsPerPage": PAGE_SIZE,
                    "page": page,
                },
            )
            stories.extend(_story_from_hit(hit) for hit in data.get("hits", []))
            if page + 1 >= int(data.get("nbPages", 1)):
                break
        stories.sort(key=lambda s: s.points, reverse=True)
        return stories[:limit]

    def best(self, *, limit: int) -> list[Story]:
        ids = self._best_ids()[:limit]
        if not ids:
            return []
        group = ",".join(f"story_{story_id}" for story_id in ids)
        data = self._get("/search", {"tags": f"story,({group})", "hitsPerPage": len(ids)})
        by_id = {story.id: story for story in map(_story_from_hit, data.get("hits", []))}
        return [by_id[story_id] for story_id in ids if story_id in by_id]  # keep HN's order

    def day_stories(self, day: date, *, min_points: int) -> list[Story]:
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        stories = self._stories_in_window(_day_window(start), min_points)
        stories.sort(key=lambda story: (-story.points, -story.created_at.timestamp()))
        return stories

    def _stories_in_window(self, window: str, min_points: int) -> list[Story]:
        """Every story in the window with at least `min_points`, paged (up to MAX_PAGES pages)."""
        stories: list[Story] = []
        for page in range(MAX_PAGES):
            data = self._get(
                "/search",
                {
                    "tags": "story",
                    "numericFilters": f"{window},points>={min_points}",
                    "hitsPerPage": PAGE_SIZE,
                    "page": page,
                },
            )
            stories.extend(_story_from_hit(hit) for hit in data.get("hits", []))
            if page + 1 >= int(data.get("nbPages", 1)):
                break
        return stories

    def _best_ids(self) -> list[int]:
        try:
            response = self._firebase.get("/beststories.json")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"Hacker News best list request failed: {exc}") from exc
        if not isinstance(payload, list):
            raise SourceError("Unexpected response shape for the best list")
        return [int(story_id) for story_id in payload]

    def thread(self, story_id: int) -> tuple[Story, list[Comment]]:
        data = self._get(f"/items/{story_id}", {})
        story = _story_from_item(data)
        comments = [c for child in data.get("children", []) if (c := _comment_from_item(child, 0))]
        return story, self._in_ranked_order(story_id, comments)

    def _in_ranked_order(self, story_id: int, comments: list[Comment]) -> list[Comment]:
        """Algolia lists comments oldest first, but Hacker News ranks them. Use its order.

        Comment votes are not public, so HN's display order is the closest thing to "top voted".
        Top-level order is required (an error if unavailable, never silently wrong);
        replies are ranked best-effort for the first threads.
        """
        ranked = _order_by(comments, self._kids(story_id))
        head = ranked[:RANKED_THREADS]
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(self._ranked_replies, head))
        for comment, children in zip(head, replies, strict=True):
            comment.children = children
        return ranked

    def _ranked_replies(self, comment: Comment) -> list[Comment]:
        if len(comment.children) < 2:
            return comment.children
        try:
            return _order_by(comment.children, self._kids(comment.id))
        except SourceError:
            return comment.children  # best effort: replies stay in posting order

    def _kids(self, item_id: int) -> list[int]:
        try:
            response = self._firebase.get(f"/item/{item_id}.json")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"Could not read comment order for {item_id}: {exc}") from exc
        kids = payload.get("kids") if isinstance(payload, dict) else None
        return [int(kid) for kid in kids] if isinstance(kids, list) else []

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"Hacker News request failed for {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceError(f"Unexpected response shape for {path}")
        return payload


def _story_from_hit(hit: dict[str, Any]) -> Story:
    return Story(
        id=int(hit["objectID"]),
        title=hit.get("title") or "",
        url=hit.get("url") or None,
        author=hit.get("author") or "unknown",
        points=hit.get("points") or 0,
        num_comments=hit.get("num_comments") or 0,
        created_at=datetime.fromtimestamp(hit["created_at_i"], tz=UTC),
        text=html_to_text(hit.get("story_text")) or None,
    )


def _story_from_item(item: dict[str, Any]) -> Story:
    return Story(
        id=int(item["id"]),
        title=item.get("title") or "",
        url=item.get("url") or None,
        author=item.get("author") or "unknown",
        points=item.get("points") or 0,
        num_comments=_count_comments(item),
        created_at=datetime.fromtimestamp(item["created_at_i"], tz=UTC),
        text=html_to_text(item.get("text")) or None,
    )


def _count_comments(item: dict[str, Any]) -> int:
    return sum(1 + _count_comments(child) for child in item.get("children", []))


def _comment_from_item(item: dict[str, Any], depth: int) -> Comment | None:
    """Deleted and dead comments come back with no text or author; drop them."""
    text = html_to_text(item.get("text"))
    author = item.get("author")
    if not text or not author:
        return None
    children = [
        c for child in item.get("children", []) if (c := _comment_from_item(child, depth + 1))
    ]
    return Comment(
        id=int(item["id"]),
        author=author,
        text=text,
        links=extract_links(item.get("text")),
        depth=depth,
        children=children,
    )


def _order_by(comments: list[Comment], ranked_ids: list[int]) -> list[Comment]:
    """Sort comments by the given id order; any not listed keep their order, at the end."""
    position = {comment_id: index for index, comment_id in enumerate(ranked_ids)}
    return sorted(comments, key=lambda comment: position.get(comment.id, len(position)))


def _day_window(start: datetime) -> str:
    """The Algolia numeric filter for the 24 hours from `start`."""
    end = start + timedelta(days=1)
    return f"created_at_i>={int(start.timestamp())},created_at_i<{int(end.timestamp())}"
