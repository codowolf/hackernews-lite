"""Pure data models shared by every stage. This package imports no vendor and does no I/O."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"


class Story(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    url: str | None = None
    author: str
    points: int
    num_comments: int
    created_at: datetime
    text: str | None = None  # body of Ask HN and other text posts, as plain text
    fetched_at: datetime | None = None  # when points and comments were read

    @property
    def hn_url(self) -> str:
        return HN_ITEM_URL.format(id=self.id)


class Link(BaseModel):
    """A link inside a comment. `text` is what Hacker News showed (long addresses are cut short)."""

    text: str
    href: str  # the real, full address


class Comment(BaseModel):
    id: int
    author: str
    text: str  # plain text; paragraphs separated by blank lines
    links: list[Link] = Field(default_factory=list)  # for display only; the analysis never sees it
    depth: int = 0
    children: list[Comment] = Field(default_factory=list)

    def walk(self) -> list[Comment]:
        """This comment and all its descendants, depth first."""
        found = [self]
        for child in self.children:
            found.extend(child.walk())
        return found


class ArticleStatus(StrEnum):
    OK = "ok"
    DEFERRED = "deferred"  # a link type v1 does not handle (see README)
    FAILED = "failed"  # fetch or extraction failed


class Article(BaseModel):
    status: ArticleStatus
    reason: str | None = None
    title: str | None = None
    text: str = ""
    word_count: int = 0

    @property
    def is_short(self) -> bool:
        """Short enough to just read in full instead of summarizing (about 2 minutes at 1.5x)."""
        return self.status is ArticleStatus.OK and self.word_count <= SHORT_ARTICLE_WORDS


SHORT_ARTICLE_WORDS = 450


class FeedSnapshot(BaseModel):
    """The ordered story ids a feed showed when it was last refreshed."""

    id: str
    fetched_at: datetime
    story_ids: list[int]


class ThumbnailStatus(StrEnum):
    OK = "ok"
    NONE = "none"  # the page has no preview image (or the link type cannot have one)
    FAILED = "failed"  # could not fetch it; tried again next run


class Thumbnail(BaseModel):
    status: ThumbnailStatus
    source_url: str | None = None
    reason: str | None = None


class ArticlePrep(BaseModel):
    gist: str
    key_points: list[str]
    model: str
    prompt_version: int
    input_hash: str


class CommentPick(BaseModel):
    comment_id: int
    author: str
    intro: str  # one short lead-in spoken before the comment
    spoken_text: str  # the commenter's own words, trimmed for listening
    reason: str  # why it was picked; shown in the UI, never spoken
    reply_ids: list[int] = Field(default_factory=list)  # replies worth hearing on "go deeper"
    # Exact phrases of spoken_text to show in bold (1 to 3). The quote itself is never changed.
    emphasis: list[str] = Field(default_factory=list)
    # Where it sits in Hacker News's ranking (votes are hidden, so this stands in for them):
    # 1-based position among the top-level comments, or of its thread for a reply.
    rank: int | None = None
    reply_rank: int | None = None  # for a reply: its position among the thread's direct replies


class CommentPicks(BaseModel):
    method: str = "llm"  # which picker made these: "llm" or "jev"
    prompt_version: int
    model: str
    input_hash: str
    picks: list[CommentPick]
    highlighted: bool = (
        False  # the highlight step has looked at these quotes (even if it found none)
    )


class CommentSignals(BaseModel):
    """How a comment rates on each question, all on a 0 to 1 scale."""

    first_hand: float  # describes the commenter's own experience or expertise
    corrects: float  # corrects, contradicts or caveats the article
    substance: float  # specific and well argued, not a one-liner
    adds_info: float  # adds facts, numbers, examples or alternatives
    standalone: float  # makes sense without the comments above it
    hostile: float  # attack, flame or pile-on


class ScoredComment(BaseModel):
    text_hash: str  # of the text that was scored, so edits are noticed
    signals: CommentSignals


class CommentScoreSet(BaseModel):
    """Raw scores per comment. Kept on disk so picks can be re-tuned without paying again."""

    model: str
    questions_version: int
    scores: dict[int, ScoredComment] = Field(default_factory=dict)


class Stance(StrEnum):
    """A comment's position toward the article's main claim (or the post's position)."""

    AGREE = "agree"
    DISAGREE = "disagree"
    MIXED = "mixed"
    NEUTRAL = "neutral"


class StanceCounts(BaseModel):
    agree: int = 0
    disagree: int = 0
    mixed: int = 0
    neutral: int = 0

    @property
    def total(self) -> int:
        return self.agree + self.disagree + self.mixed + self.neutral


class Debate(BaseModel):
    """A thread where commenters disagree with each other, with the model's own read on it."""

    thread_id: int  # the top-level comment the debate sits under
    topic: str
    side_a: str
    side_b: str
    verdict: str  # the model's opinion on which side holds up; always labeled as such
    leans: Literal["a", "b", "neither"]
    confidence: Literal["low", "medium", "high"]
    authors: list[str] = Field(default_factory=list)


class CommentStance(BaseModel):
    model: str
    prompt_version: int
    input_hash: str
    headline: str  # one sentence on how the thread received the article
    top_counts: StanceCounts  # the highest-ranked comments (HN order, its best proxy for votes)
    all_counts: StanceCounts  # every top-level comment that was analyzed
    top_agree: list[CommentPick]
    top_disagree: list[CommentPick]
    debates: list[Debate]
    # "quick": a cheap read (labels from Jev, no debates). "deep": the model read the thread and
    # wrote the headline, debates and its take. Deep is made only when the reader asks for it.
    depth: Literal["quick", "deep"] = "deep"
    highlighted: bool = False


class SegmentKind(StrEnum):
    GIST = "gist"
    KEY_POINTS = "key_points"
    REACTION = "reaction"
    AGREE = "agree"
    DISAGREE = "disagree"
    DEBATE = "debate"
    COMMENT = "comment"


class Segment(BaseModel):
    """One playable piece of audio. The player skips, queues and caches at this level."""

    model_config = ConfigDict(frozen=True)

    story_id: int
    kind: SegmentKind
    order: int
    text: str
    ref_comment_id: int | None = None
