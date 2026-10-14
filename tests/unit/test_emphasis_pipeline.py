"""Emphasis through the stages, the spoken script and the API."""

from fastapi.testclient import TestClient

from hackernews_lite.config import LlmStage
from hackernews_lite.domain.models import (
    ArticlePrep,
    CommentPick,
    CommentPicks,
    CommentStance,
    Debate,
    SegmentKind,
    Stance,
    StanceCounts,
)
from hackernews_lite.prep.article_prep import ArticlePrepOutput, prep_article
from hackernews_lite.prep.comment_curation import PickOutput, validate_picks
from hackernews_lite.prep.comment_stance import (
    DebateOutput,
    LabelOutput,
    StanceOutput,
    validate_stance,
)
from hackernews_lite.prep.prompts import Prompt
from hackernews_lite.prep.segments import build_segments
from hackernews_lite.storage.story_store import StoryStore
from tests.conftest import FakeLlm, make_comments, make_story
from tests.unit.test_comment_stance import thread
from tests.unit.test_web_app import rig, tts  # noqa: F401  (fixtures)

REAL_11 = "I ran this in production for two years and the upgrades were the real cost."


def pick(comment_id: int, spoken: str, emphasis: list[str]) -> PickOutput:
    return PickOutput(
        comment_id=comment_id,
        intro="A commenter says",
        spoken_text=spoken,
        reason="r",
        emphasis=emphasis,
    )


class TestVerbatimQuotes:
    def test_valid_phrases_are_kept_on_the_pick(self) -> None:
        picks = validate_picks([pick(11, REAL_11, ["the real cost"])], make_comments())
        assert picks[0].emphasis == ["the real cost"]

    def test_a_phrase_not_in_the_quote_is_dropped(self) -> None:
        picks = validate_picks(
            [pick(11, REAL_11, ["something invented here"])],
            make_comments(),
        )
        assert picks[0].emphasis == []

    def test_when_the_model_rewords_the_quote_emphasis_follows_the_real_text(self) -> None:
        reworded = pick(21, "completely different invented words here", ["Did not like"])
        picks = validate_picks([reworded], make_comments())
        assert picks[0].spoken_text == "Tried it once. Did not like it at all."
        assert picks[0].emphasis == ["Did not like"]  # it does occur in the real comment


class TestModelWrittenText:
    def test_article_prep_cleans_markers(self, store: StoryStore) -> None:
        from hackernews_lite.domain.models import Article, ArticleStatus

        story = make_story()
        store.save_article(
            story.id, Article(status=ArticleStatus.OK, text="x " * 300, word_count=300)
        )
        llm = FakeLlm(
            {
                ArticlePrepOutput: ArticlePrepOutput(
                    gist="A **whole sentence is bolded by mistake here.**",
                    key_points=["Costs fell **38 percent** after the move.", "Half **open marker"],
                )
            }
        )
        prep_article(
            story,
            store,
            llm,
            Prompt("article_prep", 3, "p"),
            LlmStage(model="m", effort="low", max_tokens=9),
        )

        saved = store.load_prep(story.id)
        assert saved is not None
        assert saved.gist == "A whole sentence is bolded by mistake here."
        assert saved.key_points == ["Costs fell **38 percent** after the move.", "Half open marker"]

    def test_stance_text_is_cleaned_and_the_topic_stays_plain(self) -> None:
        out = StanceOutput(
            headline=(
                "Commenters mostly **accept the allegations** as unsurprising, "
                "while a few **dispute the numbers** and the sourcing."
            ),
            labels=[LabelOutput(comment_id=1, stance=Stance.AGREE)],
            debates=[
                DebateOutput(
                    thread_id=1,
                    topic="Is the **cost** real",
                    side_a=(
                        "The cost of upgrades is **real and recurring**, "
                        "and it grows every single year."
                    ),
                    side_b="Half **open and the rest of this sentence is long enough to read",
                    verdict=(
                        "Side A has the **better evidence** here, "
                        "though the sample is small and anecdotal."
                    ),
                    leans="a",
                    confidence="medium",
                )
            ],
        )
        stance = validate_stance(out, thread())
        assert stance.headline.count("**") == 4
        debate = stance.debates[0]
        assert debate.topic == "Is the cost real"
        assert debate.side_a.count("**") == 2
        assert debate.side_b == "Half open and the rest of this sentence is long enough to read"
        assert debate.verdict.count("**") == 2


def make_stance_with_markers() -> CommentStance:
    quote = CommentPick(
        comment_id=31,
        author="erin",
        intro="A reader says",
        spoken_text="Agreed, we saw the same thing at work.",
        reason="clear",
        emphasis=["saw the same thing"],
    )
    return CommentStance(
        model="m",
        prompt_version=2,
        input_hash="h",
        headline="Mostly **agree**.",
        top_counts=StanceCounts(agree=3, disagree=1),
        all_counts=StanceCounts(agree=5, disagree=2),
        top_agree=[quote],
        top_disagree=[],
        debates=[
            Debate(
                thread_id=11,
                topic="Cost",
                side_a="It is **high**.",
                side_b="It is low.",
                verdict="**High** wins.",
                leans="a",
                confidence="high",
            )
        ],
    )


class TestSpeech:
    def test_markers_never_reach_the_spoken_script(self) -> None:
        prep = ArticlePrep(
            gist="Moved to **plain VMs**.",
            key_points=["Costs fell **38 percent**.", "Upgrades were **the real cost**."],
            model="m",
            prompt_version=3,
            input_hash="h",
        )
        segments = build_segments(make_story(), prep, None, make_stance_with_markers())
        assert segments and all("*" not in s.text for s in segments)
        by_kind = {s.kind: s.text for s in segments}
        assert by_kind[SegmentKind.GIST].endswith("Moved to plain VMs.")
        assert "Costs fell 38 percent." in by_kind[SegmentKind.KEY_POINTS]
        assert by_kind[SegmentKind.REACTION].startswith("How the thread reacted. Mostly agree.")
        assert "My take, with high confidence: High wins." in by_kind[SegmentKind.DEBATE]

    def test_highlighting_does_not_change_the_audio_cache_key(self) -> None:
        plain = ArticlePrep(
            gist="Moved to plain VMs.",
            key_points=["Costs fell."],
            model="m",
            prompt_version=2,
            input_hash="h",
        )
        bold = plain.model_copy(
            update={"gist": "Moved to **plain VMs**.", "key_points": ["**Costs** fell."]}
        )
        assert [s.text for s in build_segments(make_story(), plain, None)] == [
            s.text for s in build_segments(make_story(), bold, None)
        ]


class TestApi:
    def test_the_api_carries_markers_and_emphasis_for_the_screen(self, rig) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
        rig.store.save_stance(1, make_stance_with_markers())
        prep = ArticlePrep(
            gist="Moved to **plain VMs**.",
            key_points=["Costs fell **38 percent**."],
            model="m",
            prompt_version=3,
            input_hash="h",
        )
        rig.store.save_prep(1, prep)
        client: TestClient = rig.client

        segments = {s["kind"]: s for s in client.get("/api/stories/1?picks=llm").json()["segments"]}
        assert (
            segments["gist"]["display"] == "Moved to **plain VMs**."
        )  # the screen parses the markers
        assert segments["gist"]["text"].endswith("Moved to plain VMs.")  # the voice never sees them
        assert segments["key_points"]["bullets"] == ["Costs fell **38 percent**."]
        assert segments["reaction"]["reaction"]["headline"] == "Mostly **agree**."
        assert segments["agree"]["emphasis"] == ["saw the same thing"]
        assert segments["debate"]["debate"]["verdict"] == "**High** wins."
        assert segments["debate"]["debate"]["topic"] == "Cost"

    def test_list_cards_show_plain_text(self, rig) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
        prep = ArticlePrep(
            gist="Moved to **plain VMs**.",
            key_points=["x"],
            model="m",
            prompt_version=3,
            input_hash="h",
        )
        rig.store.save_prep(1, prep)
        stories = {s["id"]: s for s in rig.client.get("/api/stories?feed=24h").json()["stories"]}
        assert stories[1]["gist"] == "Moved to plain VMs."


def test_curated_picks_are_saved_with_their_emphasis(store: StoryStore) -> None:
    picks = validate_picks([pick(11, REAL_11, ["the real cost"])], make_comments())
    store.save_picks(1, CommentPicks(prompt_version=3, model="m", input_hash="h", picks=picks))
    loaded = store.load_picks(1)
    assert loaded is not None and loaded.picks[0].emphasis == ["the real cost"]
