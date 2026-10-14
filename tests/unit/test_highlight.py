from hackernews_lite.config import LlmStage
from hackernews_lite.domain.models import CommentPick, CommentPicks, CommentStance, StanceCounts
from hackernews_lite.prep.highlight import HighlightItem, HighlightOutput, add_highlights
from hackernews_lite.prep.prompts import Prompt
from hackernews_lite.prep.stage import StageStatus
from hackernews_lite.storage.story_store import StoryStore
from tests.conftest import FakeLlm, make_story

PROMPT = Prompt(name="highlight", version=1, text="p")
STAGE = LlmStage(model="m", effort="low", max_tokens=100)


def pick(comment_id: int, text: str) -> CommentPick:
    return CommentPick(
        comment_id=comment_id, author="a", intro="Someone says", spoken_text=text, reason="r"
    )


def save(
    store: StoryStore,
    *,
    jev: list[CommentPick] | None = None,
    quick: list[CommentPick] | None = None,
) -> None:
    story = make_story()
    if jev is not None:
        store.save_picks(
            story.id,
            CommentPicks(method="jev", prompt_version=1, model="j", input_hash="h", picks=jev),
        )
    if quick is not None:
        store.save_stance(
            story.id,
            CommentStance(
                model="j",
                prompt_version=1,
                input_hash="h",
                headline="H.",
                top_counts=StanceCounts(),
                all_counts=StanceCounts(),
                top_agree=quick,
                top_disagree=[],
                debates=[],
                depth="quick",
            ),
        )


def llm_returning(*items: HighlightItem) -> FakeLlm:
    return FakeLlm({HighlightOutput: HighlightOutput(items=list(items))})


def test_good_phrases_are_kept_and_made_into_emphasis(store: StoryStore) -> None:
    long_quote = (
        "We ran this for two years across several teams and most of the time it was fine. "
        "The upgrades were the real cost of running this in production, and nobody budgeted "
        "for them. Everything else was noise compared with that."
    )
    save(store, jev=[pick(1, long_quote)])
    llm = llm_returning(
        HighlightItem(comment_id=1, emphasis=["the real cost", "nobody budgeted for them"])
    )

    result = add_highlights(make_story(), store, llm, PROMPT, STAGE)

    assert result.status is StageStatus.DONE and llm.calls == 1
    saved = store.load_picks(1, "jev")
    assert saved is not None and saved.highlighted
    assert saved.picks[0].emphasis == ["the real cost", "nobody budgeted for them"]


def test_invented_phrases_are_dropped(store: StoryStore) -> None:
    save(store, jev=[pick(1, "The upgrades were the real cost.")])
    llm = llm_returning(
        HighlightItem(comment_id=1, emphasis=["a phrase that is not there", "real cost"])
    )
    add_highlights(make_story(), store, llm, PROMPT, STAGE)
    assert store.load_picks(1, "jev").picks[0].emphasis == ["real cost"]  # type: ignore[union-attr]


def test_picks_from_the_quick_reaction_are_highlighted_too_in_one_call(store: StoryStore) -> None:
    save(
        store,
        jev=[pick(1, "First quote has a key point here.")],
        quick=[pick(2, "Second quote has another point.")],
    )
    llm = llm_returning(
        HighlightItem(comment_id=1, emphasis=["key point"]),
        HighlightItem(comment_id=2, emphasis=["another point"]),
    )
    add_highlights(make_story(), store, llm, PROMPT, STAGE)
    assert llm.calls == 1  # both sets of quotes go together
    assert store.load_stance(1).top_agree[0].emphasis == ["another point"]  # type: ignore[union-attr]


def test_it_runs_once_even_if_the_model_found_nothing(store: StoryStore) -> None:
    save(store, jev=[pick(1, "Nothing stands out in this quote at all.")])
    llm = llm_returning()
    add_highlights(make_story(), store, llm, PROMPT, STAGE)
    again = add_highlights(make_story(), store, llm, PROMPT, STAGE)
    assert again.status is StageStatus.CACHED and llm.calls == 1


def test_nothing_to_highlight_means_no_call(store: StoryStore) -> None:
    llm = llm_returning()
    assert add_highlights(make_story(), store, llm, PROMPT, STAGE).status is StageStatus.SKIPPED
    assert llm.calls == 0


def test_a_deep_analysis_already_has_its_own_highlights(store: StoryStore) -> None:
    story = make_story()
    store.save_stance(
        story.id,
        CommentStance(
            model="h",
            prompt_version=2,
            input_hash="h",
            headline="H.",
            top_counts=StanceCounts(),
            all_counts=StanceCounts(),
            top_agree=[pick(1, "A deep pick.")],
            top_disagree=[],
            debates=[],
            depth="deep",
        ),
    )
    llm = llm_returning()
    assert add_highlights(story, store, llm, PROMPT, STAGE).status is StageStatus.SKIPPED
    assert llm.calls == 0
