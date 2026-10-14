from hackernews_lite.domain.models import Comment, CommentStance, Stance, StanceCounts, Story
from hackernews_lite.prep.comment_stance_quick import (
    LabelResult,
    StanceLabel,
    analyze_stance_quick,
)
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.storage.story_store import StoryStore
from tests.conftest import make_story


def thread(n: int = 8) -> list[Comment]:
    return [
        Comment(id=i, author=f"user{i}", text=f"Comment number {i} says something real.")
        for i in range(1, n + 1)
    ]


class FakeLabeler:
    model = "fake-jev"
    questions_version = 1

    def __init__(self, stances: dict[int, tuple[Stance, float]] | None = None) -> None:
        self.stances = stances or {}
        self.calls = 0

    def label(
        self, *, article_title: str, article_summary: str, comments: list[Comment]
    ) -> LabelResult:
        self.calls += 1
        labels = {
            c.id: StanceLabel(*self.stances.get(c.id, (Stance.NEUTRAL, 0.9))) for c in comments
        }
        return LabelResult(labels=labels, input_tokens=100 * len(comments))


MIXED = {
    1: (Stance.AGREE, 0.9),
    2: (Stance.DISAGREE, 0.8),
    3: (Stance.AGREE, 0.4),  # 3 is not sure enough
    4: (Stance.AGREE, 0.95),
    5: (Stance.MIXED, 0.7),
    6: (Stance.AGREE, 0.9),
    7: (Stance.AGREE, 0.9),
    8: (Stance.DISAGREE, 0.9),
}


def run(
    store: StoryStore, labeler: FakeLabeler, comments: list[Comment] | None = None
) -> tuple[Story, StageResult]:
    story = make_story()
    store.save_comments(story.id, comments or thread())
    return story, analyze_stance_quick(story, store, labeler)


def test_counts_come_from_the_labels(store: StoryStore) -> None:
    story, result = run(store, FakeLabeler(MIXED))
    stance = store.load_stance(story.id)
    assert result.status is StageStatus.DONE and stance is not None
    assert stance.top_counts.model_dump() == {"agree": 5, "disagree": 2, "mixed": 1, "neutral": 0}
    assert stance.depth == "quick" and stance.debates == [] and stance.model == "fake-jev"


def test_the_headline_is_plain_arithmetic_not_a_model_opinion(store: StoryStore) -> None:
    story, _ = run(store, FakeLabeler(MIXED))
    headline = store.load_stance(story.id).headline  # type: ignore[union-attr]
    assert headline == "Of the top 8 comments, 5 agree, 2 disagree, 1 are mixed."


def test_top_comments_are_the_highest_ranked_confident_ones_in_hn_order(store: StoryStore) -> None:
    story, _ = run(store, FakeLabeler(MIXED))
    stance = store.load_stance(story.id)
    assert stance is not None
    assert [p.comment_id for p in stance.top_agree] == [1, 4, 6]  # 3 is too unsure; 7 is the fourth
    assert [p.comment_id for p in stance.top_disagree] == [2, 8]
    first = stance.top_agree[0]
    assert first.rank == 1 and "Ranked #1" in first.reason and first.emphasis == []
    assert first.spoken_text == "Comment number 1 says something real."


def test_only_the_top_fifteen_are_labeled(store: StoryStore) -> None:
    labeler = FakeLabeler()
    story, _ = run(store, labeler, thread(30))
    assert store.load_stance(story.id).top_counts.total == 15  # type: ignore[union-attr]


def test_a_small_thread_is_skipped_without_calling_anything(store: StoryStore) -> None:
    labeler = FakeLabeler()
    _, result = run(store, labeler, thread(3))
    assert result.status is StageStatus.SKIPPED and labeler.calls == 0


def test_the_same_input_is_not_labeled_twice(store: StoryStore) -> None:
    labeler = FakeLabeler(MIXED)
    story, _ = run(store, labeler)
    again = analyze_stance_quick(story, store, labeler)
    assert again.status is StageStatus.CACHED and labeler.calls == 1


def test_a_deep_analysis_is_never_replaced_by_a_quick_one(store: StoryStore) -> None:
    story = make_story()
    store.save_comments(story.id, thread())
    store.save_stance(
        story.id,
        CommentStance(
            model="haiku",
            prompt_version=2,
            input_hash="h",
            headline="Deep.",
            top_counts=StanceCounts(),
            all_counts=StanceCounts(),
            top_agree=[],
            top_disagree=[],
            debates=[],
            depth="deep",
        ),
    )
    labeler = FakeLabeler(MIXED)
    assert analyze_stance_quick(story, store, labeler).status is StageStatus.SKIPPED
    assert labeler.calls == 0 and store.load_stance(story.id).headline == "Deep."  # type: ignore[union-attr]
    assert analyze_stance_quick(story, store, labeler, force=True).status is StageStatus.DONE
