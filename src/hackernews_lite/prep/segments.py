"""Pure: turn a story's saved prep and picks into the ordered audio segments the player uses."""

from hackernews_lite.domain.models import (
    ArticlePrep,
    CommentPick,
    CommentPicks,
    CommentStance,
    Debate,
    Segment,
    SegmentKind,
    Story,
)
from hackernews_lite.domain.text import make_speakable


def build_segments(
    story: Story,
    prep: ArticlePrep | None,
    picks: CommentPicks | None,
    stance: CommentStance | None = None,
) -> list[Segment]:
    """Gist, key points, how the thread reacted, then the comments worth hearing."""
    segments: list[Segment] = []

    def add(kind: SegmentKind, text: str, comment_id: int | None = None) -> None:
        segments.append(
            Segment(
                story_id=story.id,
                kind=kind,
                order=len(segments),
                text=make_speakable(text),
                ref_comment_id=comment_id,
            )
        )

    if prep:
        add(SegmentKind.GIST, f"{story.title}. {prep.gist}")
        add(SegmentKind.KEY_POINTS, _key_points_script(prep.key_points))

    used: set[int] = set()
    if stance:
        add(SegmentKind.REACTION, _reaction_script(stance))
        for pick in stance.top_agree:
            add(SegmentKind.AGREE, _comment_script("In agreement", pick), pick.comment_id)
            used.add(pick.comment_id)
        for pick in stance.top_disagree:
            add(SegmentKind.DISAGREE, _comment_script("Pushing back", pick), pick.comment_id)
            used.add(pick.comment_id)
        for debate in stance.debates:
            add(SegmentKind.DEBATE, _debate_script(debate), debate.thread_id)

    if picks:
        for pick in picks.picks:
            if pick.comment_id not in used:  # already read above
                add(SegmentKind.COMMENT, _comment_script("", pick), pick.comment_id)
    return segments


def _comment_script(label: str, pick: CommentPick) -> str:
    lead = f"{_clean_intro(pick.intro)}: {pick.spoken_text}"
    return f"{label}. {lead}" if label else lead


def _reaction_script(stance: CommentStance) -> str:
    counts = stance.top_counts
    parts = [f"{counts.agree} agree", f"{counts.disagree} disagree"]
    if counts.mixed:
        parts.append(f"{counts.mixed} are mixed")
    return (
        f"How the thread reacted. {stance.headline} Among the top {counts.total} comments, "
        + ", ".join(parts)
        + "."
    )


def _debate_script(debate: Debate) -> str:
    take = f"My take, with {debate.confidence} confidence: {debate.verdict}"
    return (
        f"A debate: {debate.topic}. One side says: {debate.side_a} "
        f"The other says: {debate.side_b} {take}"
    )


def _key_points_script(points: list[str]) -> str:
    ordinals = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"]
    spoken = [
        f"{ordinals[i] if i < len(ordinals) else 'Also'}, {point.rstrip('.')}."
        for i, point in enumerate(points)
    ]
    return "Key points. " + " ".join(spoken)


def _clean_intro(intro: str) -> str:
    """Models sometimes end the lead-in with a full stop; the comment must continue it."""
    return intro.strip().rstrip(".:;, ")
