"""Stage: how did the thread react to the story?

Labels top comments agree or disagree, finds debates, and gives the model's take.

One LLM call reads the thread. The counts are computed here from the labels, never taken from the
model's arithmetic, and every id and quote is checked against the real comments.
"""

from typing import Literal

from pydantic import BaseModel, Field

from hackernews_lite.config import LlmStage
from hackernews_lite.domain.errors import LlmOutputError
from hackernews_lite.domain.hashing import content_hash
from hackernews_lite.domain.models import (
    ArticlePrep,
    Comment,
    CommentPick,
    CommentStance,
    Debate,
    Stance,
    StanceCounts,
    Story,
)
from hackernews_lite.domain.text import clean_markers, strip_markers
from hackernews_lite.llm.client import LlmClient
from hackernews_lite.prep.comment_curation import PickOutput, validate_picks
from hackernews_lite.prep.prompts import Prompt
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.storage.story_store import StoryStore

MIN_COMMENTS = 5
LABELED = 20  # top-level comments the model labels
TOP_N = 15  # "top voted" for the headline counts: the highest-ranked this many
NESTED = 8  # the first threads also show their replies, so debates can be found
MAX_REPLIES = 4  # replies shown under each of those threads
MAX_DEPTH = 3
TOP_TEXT_CHARS = 800
REPLY_TEXT_CHARS = 350
MAX_PICKS = 3
MAX_DEBATES = 3
METHOD_VERSION = 1


class LabelOutput(BaseModel):
    comment_id: int
    stance: Stance


class DebateOutput(BaseModel):
    thread_id: int
    topic: str
    side_a: str
    side_b: str
    verdict: str
    leans: Literal["a", "b", "neither"]
    confidence: Literal["low", "medium", "high"]


class StanceOutput(BaseModel):
    headline: str
    labels: list[LabelOutput]
    top_agree: list[PickOutput] = Field(default_factory=list)
    top_disagree: list[PickOutput] = Field(default_factory=list)
    debates: list[DebateOutput] = Field(default_factory=list)


def analyze_stance(
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
        return StageResult(StageStatus.SKIPPED, "too few comments to read a mood")

    user = render_for_stance(story, store.load_prep(story.id), comments)
    input_hash = content_hash(prompt.version, prompt.text, stage.model, METHOD_VERSION, user)
    existing = store.load_stance(story.id)
    if existing and existing.input_hash == input_hash and not force:
        return StageResult(StageStatus.CACHED)

    response = llm.parse(
        model=stage.model,
        system=prompt.text,
        user=user,
        output_type=StanceOutput,
        max_tokens=stage.max_tokens,
        effort=stage.effort,
        story_id=story.id,
    )
    stance = validate_stance(response.parsed, comments)
    stance = stance.model_copy(
        update={"model": stage.model, "prompt_version": prompt.version, "input_hash": input_hash}
    )
    store.save_stance(story.id, stance)
    return StageResult(
        StageStatus.DONE,
        f"{stance.all_counts.total} labeled, {len(stance.debates)} debates",
    )


def render_for_stance(story: Story, prep: ArticlePrep | None, comments: list[Comment]) -> str:
    lines = [f"Story: {story.title}"]
    if prep:
        lines.append(f"Gist: {prep.gist}")
        lines.extend(f"Key point: {point}" for point in prep.key_points)
    elif story.text:
        lines.append(f"Post: {story.text[:1500]}")
    lines += [
        "",
        "Top-level comments in Hacker News order (rank 1 is highest). Replies are indented.",
        "",
    ]
    for rank, top in enumerate(comments[:LABELED], start=1):
        lines.append(f"[id={top.id}] rank {rank}, {top.author}")
        lines.append(_clip(top.text, TOP_TEXT_CHARS))
        if rank <= NESTED:
            shown = 0
            for reply in top.children:
                for node in reply.walk():
                    if shown >= MAX_REPLIES or node.depth > MAX_DEPTH:
                        continue
                    pad = "    " * node.depth
                    lines.append(f"{pad}[id={node.id}] {node.author} (reply)")
                    lines.append(pad + _clip(node.text, REPLY_TEXT_CHARS).replace("\n", f"\n{pad}"))
                    shown += 1
        lines.append("")
    return "\n".join(lines)


def validate_stance(out: StanceOutput, comments: list[Comment]) -> CommentStance:
    """Turn the model's output into checked data; anything not in the thread is dropped."""
    if not out.headline.strip():
        raise LlmOutputError("The model returned no headline for the thread")
    top_level = {c.id: c for c in comments[:LABELED]}

    labels: dict[int, Stance] = {}
    for item in out.labels:
        if item.comment_id in top_level and item.comment_id not in labels:
            labels[item.comment_id] = item.stance
    if not labels:
        raise LlmOutputError("None of the model's stance labels matched real comments")

    top_counts, all_counts = StanceCounts(), StanceCounts()
    for rank, comment in enumerate(comments[:LABELED]):
        stance = labels.get(comment.id)
        if stance is None:
            continue
        _add(all_counts, stance)
        if rank < TOP_N:
            _add(top_counts, stance)

    return CommentStance(
        model="",
        prompt_version=0,
        input_hash="",
        headline=clean_markers(out.headline.strip()),
        top_counts=top_counts,
        all_counts=all_counts,
        top_agree=_picks(out.top_agree, comments, labels, wrong=Stance.DISAGREE),
        top_disagree=_picks(out.top_disagree, comments, labels, wrong=Stance.AGREE),
        debates=_debates(out.debates, top_level),
    )


def _picks(
    raw: list[PickOutput], comments: list[Comment], labels: dict[int, Stance], *, wrong: Stance
) -> list[CommentPick]:
    # A pick the model itself labeled the opposite way is a mistake; keep the rest.
    consistent = [item for item in raw if labels.get(item.comment_id) is not wrong]
    return validate_picks(consistent[:MAX_PICKS], comments)[:MAX_PICKS]


def _debates(raw: list[DebateOutput], top_level: dict[int, Comment]) -> list[Debate]:
    debates: list[Debate] = []
    seen: set[int] = set()
    for item in raw:
        thread = top_level.get(item.thread_id)
        if thread is None or not thread.children or item.thread_id in seen:
            continue  # unknown thread, or nobody replied, so nothing to argue about
        if not (item.topic.strip() and item.verdict.strip()):
            continue
        seen.add(item.thread_id)
        authors = list(dict.fromkeys(c.author for c in thread.walk()))[:4]
        debates.append(
            Debate(
                thread_id=item.thread_id,
                topic=strip_markers(item.topic).strip(),  # a heading is already prominent
                side_a=clean_markers(item.side_a.strip()),
                side_b=clean_markers(item.side_b.strip()),
                verdict=clean_markers(item.verdict.strip()),
                leans=item.leans,
                confidence=item.confidence,
                authors=authors,
            )
        )
        if len(debates) == MAX_DEBATES:
            break
    return debates


def _add(counts: StanceCounts, stance: Stance) -> None:
    setattr(counts, stance.value, getattr(counts, stance.value) + 1)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " [...]"
