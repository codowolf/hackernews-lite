"""Stage: bold key phrases for quotes picked without an LLM. A tiny call that sees only the quotes.

The picks themselves are chosen cheaply (Jev), so this is where they get the highlights the reader
likes. It sends about a dozen short quotes, not the thread, so it costs very few tokens.
"""

from pydantic import BaseModel, Field

from hackernews_lite.config import LlmStage
from hackernews_lite.domain.models import CommentPick, Story
from hackernews_lite.domain.text import validate_emphasis
from hackernews_lite.llm.client import LlmClient
from hackernews_lite.prep.prompts import Prompt
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.storage.story_store import StoryStore


class HighlightItem(BaseModel):
    comment_id: int
    emphasis: list[str] = Field(default_factory=list)


class HighlightOutput(BaseModel):
    items: list[HighlightItem]


def add_highlights(
    story: Story,
    store: StoryStore,
    llm: LlmClient,
    prompt: Prompt,
    stage: LlmStage,
    *,
    force: bool = False,
) -> StageResult:
    jev_picks = store.load_picks(story.id, "jev")
    stance = store.load_stance(story.id)
    stance_quick = stance if stance and stance.depth == "quick" else None  # deep ones come marked

    pending: list[CommentPick] = []
    if jev_picks and (force or not jev_picks.highlighted):
        pending += jev_picks.picks
    if stance_quick and (force or not stance_quick.highlighted):
        pending += stance_quick.top_agree + stance_quick.top_disagree
    if not pending:
        return StageResult(
            StageStatus.CACHED if (jev_picks or stance_quick) else StageStatus.SKIPPED
        )

    quotes = {pick.comment_id: pick.spoken_text for pick in pending}
    user = "\n\n".join(f"[id={comment_id}]\n{text}" for comment_id, text in quotes.items())
    response = llm.parse(
        model=stage.model,
        system=prompt.text,
        user=user,
        output_type=HighlightOutput,
        max_tokens=stage.max_tokens,
        effort=stage.effort,
        story_id=story.id,
    )
    suggested = {item.comment_id: item.emphasis for item in response.parsed.items}
    for pick in pending:
        pick.emphasis = validate_emphasis(suggested.get(pick.comment_id, []), pick.spoken_text)

    if jev_picks:
        jev_picks.highlighted = True
        store.save_picks(story.id, jev_picks)
    if stance_quick:
        stance_quick.highlighted = True
        store.save_stance(story.id, stance_quick)
    return StageResult(StageStatus.DONE, f"{len(quotes)} quotes")
