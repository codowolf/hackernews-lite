"""Stage: pick comments by scoring each one, then selecting with weights set in config.

Two steps on purpose. Scoring is the part that costs money, so its results are saved per comment.
Selecting is pure arithmetic over those scores, so re-tuning the weights is free and instant.
"""

from dataclasses import dataclass
from typing import Protocol

from hackernews_lite.config import Curation, Weights
from hackernews_lite.domain.hashing import content_hash
from hackernews_lite.domain.models import (
    Comment,
    CommentPick,
    CommentPicks,
    CommentScoreSet,
    CommentSignals,
    ScoredComment,
    Story,
)
from hackernews_lite.domain.text import make_speakable, trim_to_words
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.storage.story_store import StoryStore

METHOD = "jev"
MIN_COMMENTS = 3
MAX_TOP_LEVEL = 40  # top-level comments considered
MAX_REPLIES = 2  # replies considered per top-level comment
SPOKEN_WORDS = 110


@dataclass(frozen=True)
class ScoringResult:
    signals: dict[int, CommentSignals]
    input_tokens: int


class CommentScorer(Protocol):
    model: str
    questions_version: int

    def score(self, *, article_title: str, comments: list[Comment]) -> ScoringResult: ...


@dataclass(frozen=True)
class Candidate:
    comment: Comment
    is_reply: bool
    thread_id: int  # id of the top-level comment this belongs to
    rank: int  # position of that top-level comment in Hacker News's order, from 1
    reply_rank: int | None = None  # for a reply: its position among the thread's direct replies


def candidates_from(comments: list[Comment]) -> list[Candidate]:
    found: list[Candidate] = []
    for rank, top in enumerate(comments[:MAX_TOP_LEVEL], start=1):
        found.append(Candidate(top, is_reply=False, thread_id=top.id, rank=rank))
        found.extend(
            Candidate(r, is_reply=True, thread_id=top.id, rank=rank, reply_rank=position)
            for position, r in enumerate(top.children[:MAX_REPLIES], start=1)
        )
    return found


def composite(signals: CommentSignals, weights: Weights) -> float:
    """Weighted sum of the signals, scaled so a perfect comment is about 1."""
    total = sum(
        getattr(weights, name) * getattr(signals, name) for name in CommentSignals.model_fields
    )
    best = sum(w for w in weights.model_dump().values() if w > 0)
    return total / best if best else 0.0


def select_picks(
    candidates: list[Candidate], scores: CommentScoreSet, curation: Curation
) -> list[CommentPick]:
    ranked: list[tuple[float, Candidate]] = []
    for candidate in candidates:
        scored = scores.scores.get(candidate.comment.id)
        if scored is None:
            continue
        value = composite(scored.signals, curation.weights)
        if value >= curation.min_score:
            ranked.append((value, candidate))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    values = {c.comment.id: v for v, c in ranked}
    picks: list[CommentPick] = []
    per_thread: dict[int, int] = {}
    for value, candidate in ranked:
        if per_thread.get(candidate.thread_id, 0) >= curation.max_per_thread:
            continue
        per_thread[candidate.thread_id] = per_thread.get(candidate.thread_id, 0) + 1
        signals = scores.scores[candidate.comment.id].signals
        replies = sorted(
            (c for c in candidate.comment.children if c.id in values),
            key=lambda c: values[c.id],
            reverse=True,
        )
        picks.append(
            CommentPick(
                rank=candidate.rank,
                reply_rank=candidate.reply_rank,
                comment_id=candidate.comment.id,
                author=candidate.comment.author,
                intro=_intro(signals, is_reply=candidate.is_reply),
                spoken_text=make_speakable(trim_to_words(candidate.comment.text, SPOKEN_WORDS)),
                reason=_reason(signals, value),
                reply_ids=[c.id for c in replies[:2]],
            )
        )
        if len(picks) == curation.max_picks:
            break
    return picks


def pick_by_score(
    story: Story,
    store: StoryStore,
    scorer: CommentScorer,
    curation: Curation,
    *,
    force: bool = False,
) -> StageResult:
    comments = store.load_comments(story.id) or []
    if len(comments) < MIN_COMMENTS:
        return StageResult(StageStatus.SKIPPED, "too few comments")
    candidates = candidates_from(comments)

    scores = store.load_scores(story.id)
    if (
        scores is None
        or scores.model != scorer.model
        or scores.questions_version != scorer.questions_version
    ):
        scores = CommentScoreSet(model=scorer.model, questions_version=scorer.questions_version)
    stale = [
        c.comment
        for c in candidates
        if force
        or _text_hash(c.comment) != getattr(scores.scores.get(c.comment.id), "text_hash", None)
    ]
    if stale:
        result = scorer.score(article_title=story.title, comments=stale)
        for comment in stale:
            if comment.id in result.signals:
                scores.scores[comment.id] = ScoredComment(
                    text_hash=_text_hash(comment), signals=result.signals[comment.id]
                )
        store.save_scores(story.id, scores)

    picks = select_picks(candidates, scores, curation)
    input_hash = content_hash(
        curation.model_dump_json(), scorer.model, scorer.questions_version, _scores_digest(scores)
    )
    existing = store.load_picks(story.id, METHOD)
    if existing and existing.input_hash == input_hash and not stale:
        return StageResult(StageStatus.CACHED)
    store.save_picks(
        story.id,
        CommentPicks(
            method=METHOD,
            prompt_version=scorer.questions_version,
            model=scorer.model,
            input_hash=input_hash,
            picks=picks,
        ),
    )
    scored_note = f"scored {len(stale)} comments, " if stale else "re-picked from saved scores, "
    return StageResult(StageStatus.DONE, f"{scored_note}{len(picks)} picks")


def _text_hash(comment: Comment) -> str:
    return content_hash(comment.text)


def _scores_digest(scores: CommentScoreSet) -> str:
    return content_hash(
        *(
            f"{cid}:{s.text_hash}:{s.signals.model_dump_json()}"
            for cid, s in sorted(scores.scores.items())
        )
    )


_INTROS = {
    # (is_reply) -> phrase for the strongest signal; every intro ends with "says"
    "first_hand": (
        "A commenter with first-hand experience says",
        "A reply from someone with first-hand experience says",
    ),
    "corrects": ("A commenter pushing back on the article says", "A reply pushing back says"),
    "adds_info": ("A commenter adding context says", "A reply adding context says"),
    "substance": ("Another commenter says", "A reply says"),
}


def _intro(signals: CommentSignals, *, is_reply: bool) -> str:
    strongest = max(("first_hand", "corrects", "adds_info"), key=lambda n: getattr(signals, n))
    key = strongest if getattr(signals, strongest) >= 0.5 else "substance"
    return _INTROS[key][1 if is_reply else 0]


def _reason(signals: CommentSignals, value: float) -> str:
    parts = sorted(
        (
            (name, getattr(signals, name))
            for name in ("first_hand", "corrects", "substance", "adds_info")
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )[:2]
    return f"score {value:.2f}: " + ", ".join(
        f"{name.replace('_', '-')} {score:.2f}" for name, score in parts
    )
