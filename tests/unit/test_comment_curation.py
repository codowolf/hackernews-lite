from hackernews_lite.prep.comment_curation import (
    PickOutput,
    render_comments_for_model,
    validate_picks,
)
from tests.conftest import make_comments, make_story


def pick(comment_id: int, spoken: str, reply_ids: list[int] | None = None) -> PickOutput:
    return PickOutput(
        comment_id=comment_id,
        intro="A commenter says",
        spoken_text=spoken,
        reason="good",
        reply_ids=reply_ids or [],
    )


def test_unknown_comment_ids_are_dropped() -> None:
    assert validate_picks([pick(999, "made up")], make_comments()) == []


def test_duplicate_picks_are_dropped() -> None:
    text = "the upgrades were the real cost"
    picks = validate_picks([pick(11, text), pick(11, text)], make_comments())
    assert [p.comment_id for p in picks] == [11]


def test_faithful_text_is_kept_trimmed() -> None:
    picks = validate_picks([pick(11, "the upgrades were the real cost")], make_comments())
    assert picks[0].spoken_text == "the upgrades were the real cost"
    assert picks[0].author == "bob"


def test_reworded_text_falls_back_to_the_real_comment() -> None:
    picks = validate_picks(
        [pick(21, "This person strongly dislikes the tool entirely")], make_comments()
    )
    assert picks[0].spoken_text == "Tried it once. Did not like it at all."


def test_reply_ids_must_be_children_of_the_pick() -> None:
    picks = validate_picks([pick(11, "the upgrades were the real cost", [12, 31])], make_comments())
    assert picks[0].reply_ids == [12]


def test_picks_can_target_replies() -> None:
    picks = validate_picks([pick(12, "we saw the same thing")], make_comments())
    assert picks[0].comment_id == 12


def test_rendering_includes_ids_and_replies() -> None:
    rendered = render_comments_for_model(make_story(), make_comments())
    assert "[id=11] bob" in rendered
    assert "[id=12] carol (reply)" in rendered


def test_picks_record_their_place_in_hacker_news_ranking() -> None:
    picks = validate_picks(
        [
            pick(21, "Did not like it at all"),  # the second top-level comment
            pick(12, "we saw the same thing"),  # the first reply under the first comment
        ],
        make_comments(),
    )
    by_id = {p.comment_id: p for p in picks}
    assert (by_id[21].rank, by_id[21].reply_rank) == (2, None)
    assert (by_id[12].rank, by_id[12].reply_rank) == (1, 1)
