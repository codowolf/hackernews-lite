import pytest

from hackernews_lite.config import LlmStage
from hackernews_lite.domain.errors import LlmOutputError
from hackernews_lite.domain.models import Comment, Stance
from hackernews_lite.prep.comment_curation import PickOutput
from hackernews_lite.prep.comment_stance import (
    DebateOutput,
    LabelOutput,
    StanceOutput,
    analyze_stance,
    render_for_stance,
    validate_stance,
)
from hackernews_lite.prep.prompts import Prompt
from hackernews_lite.prep.stage import StageStatus
from hackernews_lite.storage.story_store import StoryStore
from tests.conftest import FakeLlm, make_story


def thread() -> list[Comment]:
    """Six top-level comments; #1 has a back-and-forth underneath."""
    reply2 = Comment(id=102, author="dave", text="No, the numbers say otherwise.", depth=2)
    reply1 = Comment(
        id=101, author="carol", text="I disagree with that reading.", depth=1, children=[reply2]
    )
    tops = [
        Comment(
            id=1,
            author="alice",
            text="The article is right and the cost is real.",
            children=[reply1],
        ),
        Comment(id=2, author="bob", text="This is overblown and misses the point entirely."),
        Comment(id=3, author="erin", text="Agreed, we saw the same thing at work."),
        Comment(id=4, author="frank", text="Does anyone know the license?"),
        Comment(id=5, author="gina", text="Partly right but too one-sided."),
        Comment(id=6, author="hal", text="Totally wrong, and here is why in detail."),
    ]
    return tops


def output(**kw: object) -> StanceOutput:
    fields: dict[str, object] = {
        "headline": "Most agree, a vocal minority disagrees.",
        "labels": [
            LabelOutput(comment_id=1, stance=Stance.AGREE),
            LabelOutput(comment_id=2, stance=Stance.DISAGREE),
            LabelOutput(comment_id=3, stance=Stance.AGREE),
            LabelOutput(comment_id=4, stance=Stance.NEUTRAL),
            LabelOutput(comment_id=5, stance=Stance.MIXED),
            LabelOutput(comment_id=6, stance=Stance.DISAGREE),
            LabelOutput(comment_id=999, stance=Stance.AGREE),  # not a real comment
        ],
        "top_agree": [
            PickOutput(
                comment_id=1, intro="A commenter says", spoken_text="the cost is real", reason="r"
            ),
        ],
        "top_disagree": [
            PickOutput(
                comment_id=6, intro="A critic says", spoken_text="here is why in detail", reason="r"
            ),
        ],
        "debates": [
            DebateOutput(
                thread_id=1,
                topic="Is the cost real",
                side_a="It is real.",
                side_b="The numbers say otherwise.",
                verdict="Side A has the better evidence.",
                leans="a",
                confidence="medium",
            )
        ],
    }
    fields.update(kw)
    return StanceOutput.model_validate(fields)


def test_counts_come_from_labels_and_ignore_unknown_ids() -> None:
    stance = validate_stance(output(), thread())
    assert stance.all_counts.model_dump() == {"agree": 2, "disagree": 2, "mixed": 1, "neutral": 1}
    assert stance.all_counts.total == 6  # id 999 was not counted


def test_top_counts_only_cover_the_highest_ranked() -> None:
    many = thread() + [
        Comment(id=20 + i, author="x", text="filler comment text") for i in range(12)
    ]
    labels = [LabelOutput(comment_id=c.id, stance=Stance.DISAGREE) for c in many]
    stance = validate_stance(output(labels=labels), many)
    assert stance.top_counts.total == 15 and stance.all_counts.total == 18


def test_a_pick_labeled_the_opposite_way_is_dropped() -> None:
    bad = [PickOutput(comment_id=2, intro="Someone says", spoken_text="overblown", reason="r")]
    stance = validate_stance(output(top_agree=bad), thread())  # comment 2 is labeled disagree
    assert stance.top_agree == []


def test_picks_must_quote_the_real_comment() -> None:
    reworded = [
        PickOutput(
            comment_id=3,
            intro="A reader says",
            spoken_text="completely different invented words here",
            reason="r",
        )
    ]
    stance = validate_stance(output(top_agree=reworded), thread())
    assert stance.top_agree[0].spoken_text == "Agreed, we saw the same thing at work."


def test_debates_need_a_real_thread_with_replies() -> None:
    def debate(thread_id: int) -> DebateOutput:
        return DebateOutput(
            thread_id=thread_id,
            topic="t",
            side_a="a",
            side_b="b",
            verdict="v",
            leans="neither",
            confidence="low",
        )

    stance = validate_stance(
        output(debates=[debate(2), debate(999), debate(1), debate(1)]), thread()
    )
    assert [d.thread_id for d in stance.debates] == [1]  # 2 has no replies, 999 is fake, 1 once
    assert stance.debates[0].authors == ["alice", "carol", "dave"]


def test_no_labels_or_no_headline_is_an_error() -> None:
    with pytest.raises(LlmOutputError):
        validate_stance(output(labels=[LabelOutput(comment_id=999, stance=Stance.AGREE)]), thread())
    with pytest.raises(LlmOutputError):
        validate_stance(output(headline="  "), thread())


def test_rendering_shows_rank_ids_and_nested_replies() -> None:
    text = render_for_stance(make_story(), None, thread())
    assert "[id=1] rank 1, alice" in text
    assert "    [id=101] carol (reply)" in text
    assert "        [id=102] dave (reply)" in text


PROMPT = Prompt(name="comment_stance", version=1, text="p")
STAGE = LlmStage(model="m", effort="medium", max_tokens=100)


def test_stage_saves_once_and_is_cached(store: StoryStore) -> None:
    story = make_story()
    store.save_comments(story.id, thread())
    llm = FakeLlm({StanceOutput: output()})

    assert analyze_stance(story, store, llm, PROMPT, STAGE).status is StageStatus.DONE
    saved = store.load_stance(story.id)
    assert saved is not None and saved.model == "m" and saved.headline.startswith("Most agree")
    assert analyze_stance(story, store, llm, PROMPT, STAGE).status is StageStatus.CACHED
    assert llm.calls == 1


def test_stage_skips_tiny_threads(store: StoryStore) -> None:
    story = make_story()
    store.save_comments(story.id, thread()[:3])
    llm = FakeLlm({StanceOutput: output()})
    assert analyze_stance(story, store, llm, PROMPT, STAGE).status is StageStatus.SKIPPED
    assert llm.calls == 0
