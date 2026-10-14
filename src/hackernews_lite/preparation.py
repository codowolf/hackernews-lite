"""Background work the web app starts on demand: preparing a story, and fetching preview images."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

from hackernews_lite.domain.errors import HnListenerError
from hackernews_lite.domain.models import CommentStance, Story
from hackernews_lite.pipeline import Pipeline, StoryOutcome
from hackernews_lite.prep.stage import StageStatus
from hackernews_lite.prep.thumbnail import prepare_thumbnail
from hackernews_lite.sources.article_fetcher import ArticleFetcher
from hackernews_lite.sources.images import ImageFetcher
from hackernews_lite.storage.story_store import StoryStore
from hackernews_lite.web.jobs import JobMode


def make_prepare_work(pipeline: Pipeline, store: StoryStore) -> Callable[[int, JobMode], None]:
    """What a job does for one story, by mode. Raises if nothing useful came out of it."""

    def work(story_id: int, mode: JobMode) -> None:
        story = store.load_story(story_id)
        if story is None:
            raise HnListenerError(f"Story {story_id} is not known")
        if mode is JobMode.DEEPEN:
            _check(pipeline.deepen_story(story), store.load_stance(story_id), "deep")
            return
        outcome = pipeline.run_story(story, force=mode is JobMode.REFRESH)
        if store.load_prep(story_id) or any(store.load_picks(story_id, m) for m in ("llm", "jev")):
            return  # something to listen to; a failed extra step is not worth failing the job
        raise HnListenerError(_why_not(outcome) or "There was nothing to prepare for this story")

    return work


def _check(outcome: StoryOutcome, stance: CommentStance | None, depth: str) -> None:
    if stance is None or stance.depth != depth:
        raise HnListenerError(_why_not(outcome) or "The deep analysis did not produce a result")


def _why_not(outcome: StoryOutcome) -> str:
    return "; ".join(
        f"{name}: {result.detail}"
        for name, result in outcome.stages.items()
        if result.status is StageStatus.FAILED
    )


def make_thumbnail_worker(
    store: StoryStore, pages: ArticleFetcher, images: ImageFetcher
) -> Callable[[list[Story]], None]:
    """Fetches preview images in the background, so opening a feed is never held up by them."""
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumbs")

    def fetch_all(stories: list[Story]) -> None:
        for story in stories:
            if store.load_thumbnail(story.id) is None:
                pool.submit(_fetch_one, story)

    def _fetch_one(story: Story) -> None:
        with suppress(HnListenerError):  # a picture is a nicety; the card falls back to its tile
            prepare_thumbnail(story, store, pages, images)

    return fetch_all
