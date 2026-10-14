"""Shared fakes. Tests never touch the network or spend money."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from hackernews_lite.config import Effort, Settings, load_settings
from hackernews_lite.domain.errors import SourceError
from hackernews_lite.domain.models import Comment, Story
from hackernews_lite.llm.client import LlmResponse, Usage
from hackernews_lite.sources.article_fetcher import FetchedPage
from hackernews_lite.storage.story_store import StoryStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"

_SENTENCES = [
    "We ran Kubernetes for six years across three product teams before reconsidering it.",
    "Cluster upgrades consumed roughly one engineer every quarter, and each one carried real risk.",
    "Networking problems were the hardest to debug because the failures were rarely reproducible.",
    "Moving back to plain virtual machines cut our infrastructure bill by about a third.",
    "Deploys became slower to set up, but they became far easier to reason about afterwards.",
    "We kept containers for packaging, and dropped the orchestration layer entirely.",
    "Autoscaling mattered less than we had assumed, since our traffic was predictable all year.",
    "The team now spends its time on product work instead of on cluster maintenance chores.",
]
ARTICLE_HTML = (
    "<html><head><title>Why we left Kubernetes</title></head><body><article>"
    + "".join(f"<p>{sentence} {sentence.lower()}</p>" for sentence in _SENTENCES)
    + "</article></body></html>"
)


def make_story(
    story_id: int = 1, url: str | None = "https://example.com/post", **kw: object
) -> Story:
    fields: dict[str, object] = {
        "id": story_id,
        "title": "Why we left Kubernetes",
        "url": url,
        "author": "alice",
        "points": 300,
        "num_comments": 5,
        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
    }
    fields.update(kw)
    return Story.model_validate(fields)


def make_comments() -> list[Comment]:
    reply = Comment(id=12, author="carol", text="Agreed, we saw the same thing.", depth=1)
    return [
        Comment(
            id=11,
            author="bob",
            text="I ran this in production for two years and the upgrades were the real cost.",
            children=[reply],
        ),
        Comment(id=21, author="dave", text="Tried it once. Did not like it at all."),
        Comment(id=31, author="erin", text="Nobody mentions the networking complexity here."),
    ]


class FakeHn:
    def __init__(self, stories: list[Story], comments: list[Comment]) -> None:
        self._stories = stories
        self._comments = comments
        self.calls: list[tuple[str, int]] = []  # which feed query was made, for tests
        self.by_day: dict[date, list[Story]] | None = None
        self.failing_days: set[date] = set()

    def front_page(self, *, limit: int) -> list[Story]:
        self.calls.append(("front_page", limit))
        return self._stories[:limit]

    def top_stories(self, *, limit: int, min_points: int, window_hours: int) -> list[Story]:
        self.calls.append((f"window:{window_hours}h>={min_points}", limit))
        return self._stories[:limit]

    def best(self, *, limit: int) -> list[Story]:
        self.calls.append(("best", limit))
        return self._stories[:limit]

    def day_stories(self, day: date, *, min_points: int) -> list[Story]:
        self.calls.append((f"day:{day.isoformat()}>={min_points}", 0))
        if day in self.failing_days:
            raise SourceError(f"offline for {day}")
        if self.by_day is None:
            return list(self._stories)  # no per-day setup: every day shows the same stories
        return list(self.by_day.get(day, []))

    def thread(self, story_id: int) -> tuple[Story, list[Comment]]:
        story = next(s for s in self._stories if s.id == story_id)
        return story, self._comments


class FakeFetcher:
    def __init__(self, html: str | None = ARTICLE_HTML) -> None:
        self._html = html
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        from hackernews_lite.domain.errors import FetchError

        self.calls.append(url)
        if self._html is None:
            raise FetchError("boom")
        return FetchedPage(url=url, html=self._html)


class FakeLlm:
    """Returns canned outputs by output type and counts calls."""

    def __init__(self, outputs: dict[type[BaseModel], BaseModel]) -> None:
        self._outputs = outputs
        self.calls = 0

    def parse[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        user: str,
        output_type: type[T],
        max_tokens: int,
        effort: Effort,
        story_id: int | None = None,
    ) -> LlmResponse[T]:
        self.calls += 1
        parsed = self._outputs[output_type]
        assert isinstance(parsed, output_type)
        return LlmResponse(parsed=parsed, usage=Usage(1000, 200))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    loaded = load_settings(PROJECT_ROOT / "config.toml")
    return loaded.model_copy(
        update={
            "paths": loaded.paths.model_copy(
                update={"data_dir": tmp_path / "data", "prompts_dir": PROJECT_ROOT / "prompts"}
            )
        }
    )


@pytest.fixture
def store(settings: Settings) -> StoryStore:
    return StoryStore(settings.paths.data_dir)
