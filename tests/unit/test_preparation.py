import pytest

from hackernews_lite.config import Settings
from hackernews_lite.domain.errors import HnListenerError
from hackernews_lite.preparation import make_prepare_work, make_thumbnail_worker
from hackernews_lite.storage.story_store import StoryStore
from hackernews_lite.web.jobs import JobMode
from tests.conftest import FakeFetcher, make_story
from tests.unit.test_pipeline import build
from tests.unit.test_thumbnail import FakeImages


def test_preparing_a_story_produces_something_to_listen_to(
    settings: Settings, store: StoryStore
) -> None:
    pipeline, _, _, story = build(settings, store)
    store.save_story(story)

    make_prepare_work(pipeline, store)(story.id, JobMode.PREPARE)

    assert store.load_prep(story.id) is not None
    assert store.load_picks(story.id, "llm") is not None


def test_a_story_that_is_not_known_cannot_be_prepared(
    settings: Settings, store: StoryStore
) -> None:
    pipeline, _, _, _ = build(settings, store)
    with pytest.raises(HnListenerError, match="not known"):
        make_prepare_work(pipeline, store)(999, JobMode.PREPARE)


def test_nothing_listenable_means_the_job_fails_with_the_reason(
    settings: Settings, store: StoryStore
) -> None:
    # A repo link (no article) and no model to read its thread, so nothing listenable comes out.
    pipeline, _, _, story = build(settings, store, url="https://github.com/o/r")
    store.save_story(story)
    pipeline._llm = None  # as if the model were unavailable
    with pytest.raises(HnListenerError):
        make_prepare_work(pipeline, store)(story.id, JobMode.PREPARE)


def test_thumbnails_are_fetched_in_the_background_for_new_stories_only(
    store: StoryStore,
) -> None:
    from tests.unit.test_thumbnail import PAGE

    story = make_story(1)
    store.save_story(story)
    store.save_raw_article(story.id, PAGE)
    images = FakeImages()
    fetch = make_thumbnail_worker(store, FakeFetcher(PAGE), images)

    fetch([story])
    for _ in range(200):  # the work happens on another thread
        if store.load_thumbnail(story.id):
            break
        import time

        time.sleep(0.02)
    assert store.thumbnail_path(story.id).exists()

    fetch([story])  # already has one: no second download
    assert len(images.calls) == 1
