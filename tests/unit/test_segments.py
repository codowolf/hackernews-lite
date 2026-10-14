from hackernews_lite.domain.models import ArticlePrep, CommentPick, CommentPicks, SegmentKind
from hackernews_lite.prep.segments import build_segments
from tests.conftest import make_story


def test_segments_follow_gist_points_then_comments() -> None:
    prep = ArticlePrep(
        gist="A team left Kubernetes.",
        key_points=["Upgrades were costly.", "VMs were simpler"],
        model="m",
        prompt_version=1,
        input_hash="h",
    )
    picks = CommentPicks(
        prompt_version=1,
        model="m",
        input_hash="h",
        picks=[
            CommentPick(
                comment_id=11,
                author="bob",
                intro="A commenter says",
                spoken_text="See https://x.io now",
                reason="r",
            )
        ],
    )
    segments = build_segments(make_story(), prep, picks)
    assert [s.kind for s in segments] == [
        SegmentKind.GIST,
        SegmentKind.KEY_POINTS,
        SegmentKind.COMMENT,
    ]
    assert [s.order for s in segments] == [0, 1, 2]
    assert segments[1].text == "Key points. First, Upgrades were costly. Second, VMs were simpler."
    assert segments[2].text == "A commenter says: See a link now"
    assert segments[2].ref_comment_id == 11


def test_no_prep_and_no_picks_gives_no_segments() -> None:
    assert build_segments(make_story(), None, None) == []


def test_intro_trailing_punctuation_is_cleaned() -> None:
    from hackernews_lite.prep.segments import _clean_intro

    assert _clean_intro("  A commenter says. ") == "A commenter says"


def test_reaction_comes_before_comments_and_picks_are_not_repeated() -> None:
    from hackernews_lite.domain.models import (
        CommentStance,
        Debate,
        StanceCounts,
    )

    pick = CommentPick(
        comment_id=11, author="bob", intro="A commenter says", spoken_text="yes", reason="r"
    )
    other = CommentPick(
        comment_id=12, author="eve", intro="Another says", spoken_text="more", reason="r"
    )
    stance = CommentStance(
        model="m",
        prompt_version=1,
        input_hash="h",
        headline="Mostly agree.",
        top_counts=StanceCounts(agree=9, disagree=4, mixed=2),
        all_counts=StanceCounts(agree=12, disagree=6),
        top_agree=[pick],
        top_disagree=[],
        debates=[
            Debate(
                thread_id=5,
                topic="Cost",
                side_a="High.",
                side_b="Low.",
                verdict="High.",
                leans="a",
                confidence="high",
            )
        ],
    )
    picks = CommentPicks(prompt_version=1, model="m", input_hash="h", picks=[pick, other])
    segments = build_segments(make_story(), None, picks, stance)

    assert [s.kind for s in segments] == [
        SegmentKind.REACTION,
        SegmentKind.AGREE,
        SegmentKind.DEBATE,
        SegmentKind.COMMENT,
    ]
    assert segments[0].text == (
        "How the thread reacted. Mostly agree. Among the top 15 comments, "
        "9 agree, 4 disagree, 2 are mixed."
    )
    assert segments[1].text.startswith("In agreement. A commenter says: yes")
    assert segments[2].text == (
        "A debate: Cost. One side says: High. The other says: Low. "
        "My take, with high confidence: High."
    )
    assert [s.ref_comment_id for s in segments if s.kind is SegmentKind.COMMENT] == [
        12
    ]  # 11 already read
