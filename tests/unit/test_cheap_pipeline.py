"""The default path: Jev picks and labels the comments, Claude only summarizes and highlights."""

from hackernews_lite.config import Settings
from hackernews_lite.domain.models import Comment, CommentSignals, Stance
from hackernews_lite.pipeline import Pipeline
from hackernews_lite.prep.article_prep import ArticlePrepOutput
from hackernews_lite.prep.comment_scoring import ScoringResult
from hackernews_lite.prep.comment_stance_quick import LabelResult, StanceLabel
from hackernews_lite.prep.highlight import HighlightItem, HighlightOutput
from hackernews_lite.prep.stage import StageStatus
from hackernews_lite.storage.story_store import StoryStore
from tests.conftest import ARTICLE_HTML, FakeFetcher, FakeHn, FakeLlm, make_story
from tests.unit.test_comment_stance import output as deep_output
from tests.unit.test_comment_stance import thread


class FakeScorer:
    model = "fake-jev"
    questions_version = 1

    def __init__(self) -> None:
        self.calls = 0

    def score(self, *, article_title: str, comments: list[Comment]) -> ScoringResult:
        self.calls += 1
        good = CommentSignals(
            first_hand=0.9, corrects=0.1, substance=0.9, adds_info=0.5, standalone=1, hostile=0
        )
        return ScoringResult(signals={c.id: good for c in comments}, input_tokens=10)


class FakeLabeler:
    model = "fake-jev"
    questions_version = 1

    def __init__(self) -> None:
        self.calls = 0

    def label(
        self, *, article_title: str, article_summary: str, comments: list[Comment]
    ) -> LabelResult:
        self.calls += 1
        stances = [Stance.AGREE, Stance.DISAGREE]
        labels = {c.id: StanceLabel(stances[i % 2], 0.9) for i, c in enumerate(comments)}
        return LabelResult(labels=labels, input_tokens=10)


def cheap(settings: Settings, store: StoryStore, *, with_jev: bool = True):  # type: ignore[no-untyped-def]
    story = make_story()
    llm = FakeLlm(
        {
            ArticlePrepOutput: ArticlePrepOutput(gist="A gist.", key_points=["One.", "Two."]),
            HighlightOutput: HighlightOutput(
                items=[HighlightItem(comment_id=1, emphasis=["real"])]
            ),
            **{type(deep_output()): deep_output()},
        }
    )
    scorer, labeler = (FakeScorer(), FakeLabeler()) if with_jev else (None, None)
    pipeline = Pipeline(
        settings=settings, hn=FakeHn([story], thread()), fetcher=FakeFetcher(ARTICLE_HTML),
        llm=llm, store=store, scorer=scorer, labeler=labeler,
    )  # fmt: skip
    return pipeline, story, llm, scorer, labeler


def test_the_shipped_defaults_are_the_cheap_ones(settings: Settings) -> None:
    assert settings.curation.methods == ["jev"] and settings.curation.stance == "quick"
    assert settings.llm.stance.model == "claude-haiku-5-5"  # even the deep analysis is Haiku
    assert settings.prepare.list_top <= 5


def test_a_story_costs_claude_two_small_calls_and_nothing_else(
    settings: Settings, store: StoryStore
) -> None:
    pipeline, story, llm, scorer, labeler = cheap(settings, store)
    outcome = pipeline.run_story(story)

    statuses = {name: result.status for name, result in outcome.stages.items()}
    assert statuses["prep"] is StageStatus.DONE
    assert statuses["comments_jev"] is StageStatus.DONE
    assert statuses["stance"] is StageStatus.DONE
    assert statuses["highlight"] is StageStatus.DONE
    assert "comments" not in statuses  # Claude does not pick comments
    assert llm.calls == 2  # the summary, and the tiny highlight call. Nothing reads the thread.
    assert scorer.calls == 1 and labeler.calls == 1

    stance = store.load_stance(story.id)
    assert stance is not None and stance.depth == "quick" and stance.debates == []
    assert store.load_picks(story.id, "jev") is not None
    assert store.load_picks(story.id, "llm") is None


def test_running_it_again_spends_nothing(settings: Settings, store: StoryStore) -> None:
    pipeline, story, llm, scorer, labeler = cheap(settings, store)
    pipeline.run_story(story)
    calls = (llm.calls, scorer.calls, labeler.calls)
    pipeline.run_story(story)
    assert (llm.calls, scorer.calls, labeler.calls) == calls


def test_deepening_is_the_only_time_claude_reads_the_thread(
    settings: Settings, store: StoryStore
) -> None:
    pipeline, story, llm, _, labeler = cheap(settings, store)
    pipeline.run_story(story)
    before = llm.calls

    outcome = pipeline.deepen_story(story)

    assert outcome.stages["stance"].status is StageStatus.DONE
    assert llm.calls == before + 1
    stance = store.load_stance(story.id)
    assert stance is not None and stance.depth == "deep" and stance.debates
    pipeline.run_story(story)  # a later normal run must not throw the deep analysis away
    kept = store.load_stance(story.id)
    assert kept is not None and kept.depth == "deep"
    assert labeler.calls == 1


def test_without_a_jev_key_nothing_falls_back_to_claude(
    settings: Settings, store: StoryStore
) -> None:
    pipeline, story, llm, _, _ = cheap(settings, store, with_jev=False)
    outcome = pipeline.run_story(story)
    assert "comments" not in outcome.stages and "stance" not in outcome.stages
    assert llm.calls == 1  # just the summary
    assert store.load_picks(story.id, "jev") is None and store.load_stance(story.id) is None


def test_deepening_without_a_model_fails_clearly(settings: Settings, store: StoryStore) -> None:
    pipeline = Pipeline(
        settings=settings,
        hn=FakeHn([make_story()], thread()),
        fetcher=FakeFetcher(),
        llm=None,
        store=store,
    )
    outcome = pipeline.deepen_story(make_story())
    assert outcome.stages["stance"].status is StageStatus.FAILED
