"""Stage: the LLM picks which comments are worth hearing.

The criteria live in prompts/comment_curation.md.
"""

from pydantic import BaseModel, Field

from hackernews_lite.config import LlmStage
from hackernews_lite.domain.errors import LlmOutputError
from hackernews_lite.domain.hashing import content_hash
from hackernews_lite.domain.models import Comment, CommentPick, CommentPicks, Story
from hackernews_lite.domain.ranking import rank_of
from hackernews_lite.domain.text import (
    is_faithful,
    make_speakable,
    trim_to_words,
    validate_emphasis,
)
from hackernews_lite.llm.client import LlmClient
from hackernews_lite.prep.prompts import Prompt
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.storage.story_store import StoryStore

MIN_COMMENTS = 3
MAX_TOP_LEVEL = 40  # comments shown to the model
MAX_REPLIES = 3  # replies shown per top-level comment
TOP_TEXT_CHARS = 1500
REPLY_TEXT_CHARS = 700
MAX_PICKS = 8
FALLBACK_WORDS = 110


class PickOutput(BaseModel):
    comment_id: int
    intro: str = Field(description="A short neutral lead-in, at most 15 words.")
    spoken_text: str = Field(description="The commenter's own words, trimmed. Never reworded.")
    reason: str = Field(description="One line on why this comment is worth hearing.")
    reply_ids: list[int] = Field(default_factory=list, description="Replies worth hearing next.")
    emphasis: list[str] = Field(
        default_factory=list,
        description="One to three exact phrases copied from spoken_text "
        "(2 to 8 words each) that carry its point.",
    )


class CurationOutput(BaseModel):
    picks: list[PickOutput]


def curate_comments(
    story: Story,
    store: StoryStore,
    llm: LlmClient,
    prompt: Prompt,
    stage: LlmStage,
    *,
    force: bool = False,
) -> StageResult:
    comments = store.load_comments(story.id) or []
    if len(comments) < MIN_COMMENTS:
        return StageResult(StageStatus.SKIPPED, "too few comments")

    user = render_comments_for_model(story, comments)
    input_hash = content_hash(prompt.version, prompt.text, stage.model, user)
    existing = store.load_picks(story.id)
    if existing and existing.input_hash == input_hash and not force:
        return StageResult(StageStatus.CACHED)

    response = llm.parse(
        model=stage.model,
        system=prompt.text,
        user=user,
        output_type=CurationOutput,
        max_tokens=stage.max_tokens,
        effort=stage.effort,
        story_id=story.id,
    )
    picks = validate_picks(response.parsed.picks, comments)
    if not picks:
        raise LlmOutputError("None of the model's comment picks matched real comments")
    store.save_picks(
        story.id,
        CommentPicks(
            prompt_version=prompt.version, model=stage.model, input_hash=input_hash, picks=picks
        ),
    )
    return StageResult(StageStatus.DONE, f"{len(picks)} picks")


def render_comments_for_model(story: Story, comments: list[Comment]) -> str:
    lines = [f"Story: {story.title}", "Comments (top-level first, replies indented):", ""]
    for top in comments[:MAX_TOP_LEVEL]:
        lines.append(f"[id={top.id}] {top.author}")
        lines.append(_clip(top.text, TOP_TEXT_CHARS))
        for reply in top.children[:MAX_REPLIES]:
            lines.append(f"    [id={reply.id}] {reply.author} (reply)")
            lines.append("    " + _clip(reply.text, REPLY_TEXT_CHARS).replace("\n", "\n    "))
        lines.append("")
    return "\n".join(lines)


def validate_picks(raw: list[PickOutput], comments: list[Comment]) -> list[CommentPick]:
    """Keep picks that point at real comments, and make sure the spoken text is the commenter's."""
    by_id = {c.id: c for top in comments for c in top.walk()}
    picks: list[CommentPick] = []
    seen: set[int] = set()
    for item in raw:
        comment = by_id.get(item.comment_id)
        if comment is None or item.comment_id in seen:
            continue
        seen.add(item.comment_id)
        spoken = item.spoken_text
        if not is_faithful(spoken, comment.text):
            spoken = trim_to_words(
                comment.text, FALLBACK_WORDS
            )  # the model reworded; use the real text
        child_ids = {child.id for child in comment.children}
        final_text = make_speakable(spoken)
        rank, reply_rank = rank_of(comment.id, comments)
        picks.append(
            CommentPick(
                rank=rank,
                reply_rank=reply_rank,
                comment_id=comment.id,
                author=comment.author,
                intro=item.intro.strip(),
                spoken_text=final_text,
                emphasis=validate_emphasis(item.emphasis, final_text),
                reason=item.reason.strip(),
                reply_ids=[rid for rid in item.reply_ids if rid in child_ids],
            )
        )
        if len(picks) == MAX_PICKS:
            break
    return picks


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " [...]"
