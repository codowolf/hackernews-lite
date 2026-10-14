"""Stage: a cheap read of how the thread reacted, with no Claude tokens.

Each of the top comments is labeled agree, disagree, mixed or neutral by a classifier (Jev). The
counts and the top agreeing and disagreeing comments follow from the labels, in Hacker News's order.
What it cannot do is find debates or give a take; that is the deep analysis, made on request.
"""

from dataclasses import dataclass
from typing import Protocol

from hackernews_lite.domain.hashing import content_hash
from hackernews_lite.domain.models import (
    Comment,
    CommentPick,
    CommentStance,
    Stance,
    StanceCounts,
    Story,
)
from hackernews_lite.domain.text import make_speakable, strip_markers, trim_to_words
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.storage.story_store import StoryStore

MIN_COMMENTS = 5
LABELED = 15  # the top comments in Hacker News's order that get a label
PICKS_PER_SIDE = 3
MIN_CONFIDENCE = 0.5  # a comment must be labeled at least this surely to be shown as agreeing
SPOKEN_WORDS = 110
INTROS = {
    Stance.AGREE: "A commenter who agrees with the story says",
    Stance.DISAGREE: "A commenter who pushes back says",
}


@dataclass(frozen=True)
class StanceLabel:
    stance: Stance
    confidence: float  # 0 to 1, how sure the classifier is


@dataclass(frozen=True)
class LabelResult:
    labels: dict[int, StanceLabel]
    input_tokens: int


class StanceLabeler(Protocol):
    model: str
    questions_version: int

    def label(
        self, *, article_title: str, article_summary: str, comments: list[Comment]
    ) -> LabelResult: ...


def analyze_stance_quick(
    story: Story, store: StoryStore, labeler: StanceLabeler, *, force: bool = False
) -> StageResult:
    comments = store.load_comments(story.id) or []
    if len(comments) < MIN_COMMENTS:
        return StageResult(StageStatus.SKIPPED, "too few comments to read a mood")

    existing = store.load_stance(story.id)
    if existing and existing.depth == "deep" and not force:
        return StageResult(StageStatus.SKIPPED, "a deep analysis was already made")

    top = comments[:LABELED]
    prep = store.load_prep(story.id)
    summary = strip_markers(prep.gist) if prep else (story.text or "")[:500]
    input_hash = content_hash(
        labeler.model, labeler.questions_version, story.title, summary,
        *((c.id, c.text) for c in top),
    )  # fmt: skip
    if existing and existing.depth == "quick" and existing.input_hash == input_hash and not force:
        return StageResult(StageStatus.CACHED)

    result = labeler.label(article_title=story.title, article_summary=summary, comments=top)
    labeled = [
        (rank, c, result.labels[c.id]) for rank, c in enumerate(top, 1) if c.id in result.labels
    ]
    counts = StanceCounts()
    for _, _, label in labeled:
        setattr(counts, label.stance.value, getattr(counts, label.stance.value) + 1)

    store.save_stance(
        story.id,
        CommentStance(
            model=labeler.model,
            prompt_version=labeler.questions_version,
            input_hash=input_hash,
            headline=_headline(counts),
            top_counts=counts,
            all_counts=counts,
            top_agree=_picks(labeled, Stance.AGREE),
            top_disagree=_picks(labeled, Stance.DISAGREE),
            debates=[],
            depth="quick",
        ),
    )
    return StageResult(StageStatus.DONE, f"{counts.total} labeled")


def _headline(counts: StanceCounts) -> str:
    parts = [f"{counts.agree} agree", f"{counts.disagree} disagree"]
    if counts.mixed:
        parts.append(f"{counts.mixed} are mixed")
    if counts.neutral:
        parts.append(f"{counts.neutral} take no side")
    return f"Of the top {counts.total} comments, " + ", ".join(parts) + "."


def _picks(labeled: list[tuple[int, Comment, StanceLabel]], stance: Stance) -> list[CommentPick]:
    picks: list[CommentPick] = []
    for rank, comment, label in labeled:  # already in Hacker News's order
        if label.stance is not stance or label.confidence < MIN_CONFIDENCE:
            continue
        picks.append(
            CommentPick(
                comment_id=comment.id,
                author=comment.author,
                intro=INTROS[stance],
                spoken_text=make_speakable(trim_to_words(comment.text, SPOKEN_WORDS)),
                reason=f"Ranked #{rank} by Hacker News. Labeled {stance.value} "
                f"({label.confidence:.0%} sure).",
                rank=rank,
            )
        )
        if len(picks) == PICKS_PER_SIDE:
            break
    return picks
