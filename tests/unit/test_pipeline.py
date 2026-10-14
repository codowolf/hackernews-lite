from typing import Any

from hackernews_lite.config import Settings
from hackernews_lite.domain.models import ArticleStatus, Story
from hackernews_lite.pipeline import Pipeline
from hackernews_lite.prep.article_prep import ArticlePrepOutput
from hackernews_lite.prep.comment_curation import CurationOutput, PickOutput
from hackernews_lite.prep.stage import StageStatus
from hackernews_lite.storage.story_store import StoryStore
from tests.conftest import FakeFetcher, FakeHn, FakeLlm, make_comments, make_story


def build(
    settings: Settings,
    store: StoryStore,
    *,
    url: str | None = "https://example.com/post",
    html: str | None = None,
    **story_kw: Any,
) -> tuple[Pipeline, FakeLlm, FakeFetcher, Story]:
    """A pipeline set up the expensive way (Claude picks and reads the thread): the optional path.

    The default, cheap path is covered in test_cheap_pipeline.py.
    """
    from tests.conftest import ARTICLE_HTML

    legacy = {"methods": ["llm"], "stance": "deep"}
    settings = settings.model_copy(update={"curation": settings.curation.model_copy(update=legacy)})

    story = make_story(url=url, **story_kw)
    llm = FakeLlm(
        {
            ArticlePrepOutput: ArticlePrepOutput(gist="A gist.", key_points=["One.", "Two."]),
            CurationOutput: CurationOutput(
                picks=[
                    PickOutput(
                        comment_id=11,
                        intro="A commenter says",
                        spoken_text="the upgrades were the real cost",
                        reason="first-hand",
                    )
                ]
            ),
        }
    )
    fetcher = FakeFetcher(html if html is not None else ARTICLE_HTML)
    pipeline = Pipeline(
        settings=settings,
        hn=FakeHn([story], make_comments()),
        fetcher=fetcher,
        llm=llm,
        store=store,
    )
    return pipeline, llm, fetcher, story


def test_full_run_writes_every_artifact(settings: Settings, store: StoryStore) -> None:
    pipeline, _, _, story = build(settings, store)
    outcome = pipeline.run_story(story)

    assert {n: r.status for n, r in outcome.stages.items()} == {
        "ingest": StageStatus.DONE,
        "article": StageStatus.DONE,
        "prep": StageStatus.DONE,
        "stance": StageStatus.SKIPPED,  # the fixture thread is too small to read a mood
        "comments": StageStatus.DONE,
        "highlight": StageStatus.SKIPPED,  # no cheap picks to highlight on this path
    }
    assert store.load_article(story.id).status is ArticleStatus.OK  # type: ignore[union-attr]
    assert store.load_prep(story.id).gist == "A gist."  # type: ignore[union-attr]
    assert store.load_picks(story.id).picks[0].author == "bob"  # type: ignore[union-attr]
    assert store.load_comments(story.id)


def test_second_run_is_cached_and_spends_nothing(settings: Settings, store: StoryStore) -> None:
    pipeline, llm, fetcher, story = build(settings, store)
    pipeline.run_story(story)
    calls_after_first = llm.calls

    outcome = pipeline.run_story(story)

    assert llm.calls == calls_after_first
    assert len(fetcher.calls) == 1
    assert outcome.stages["article"].status is StageStatus.CACHED
    assert outcome.stages["prep"].status is StageStatus.CACHED
    assert outcome.stages["comments"].status is StageStatus.CACHED


def test_force_redoes_llm_stages(settings: Settings, store: StoryStore) -> None:
    pipeline, llm, _, story = build(settings, store)
    pipeline.run_story(story)
    pipeline.run_story(story, force=True)
    assert llm.calls == 4  # stance is skipped on this small thread, so no extra call


def test_deferred_link_skips_prep_but_still_curates_comments(
    settings: Settings, store: StoryStore
) -> None:
    pipeline, _, fetcher, story = build(settings, store, url="https://github.com/org/repo")
    outcome = pipeline.run_story(story)
    assert fetcher.calls == []
    assert outcome.stages["article"].status is StageStatus.SKIPPED
    assert outcome.stages["prep"].status is StageStatus.SKIPPED
    assert outcome.stages["comments"].status is StageStatus.DONE


def test_failed_fetch_is_recorded_and_does_not_stop_comments(
    settings: Settings, store: StoryStore
) -> None:
    pipeline, _, _, story = build(settings, store)
    pipeline._fetcher = FakeFetcher(None)
    outcome = pipeline.run_story(story)
    assert outcome.stages["article"].status is StageStatus.FAILED
    assert outcome.stages["comments"].status is StageStatus.DONE
    assert store.load_article(story.id).status is ArticleStatus.FAILED  # type: ignore[union-attr]


def test_text_post_uses_its_body_as_the_article(settings: Settings, store: StoryStore) -> None:
    body = "Ask HN body. " * 30
    pipeline, _, fetcher, story = build(settings, store, url=None, text=body)
    pipeline.run_story(story)
    assert fetcher.calls == []
    assert store.load_article(story.id).text == body  # type: ignore[union-attr]


def test_no_llm_mode_spends_nothing(settings: Settings, store: StoryStore) -> None:
    pipeline, llm, _, story = build(settings, store)
    outcome = pipeline.run_story(story, use_llm=False)
    assert llm.calls == 0
    assert set(outcome.stages) == {"ingest", "article"}
