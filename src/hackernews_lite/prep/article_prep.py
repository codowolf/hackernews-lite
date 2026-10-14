"""Stage: LLM gist and key points for an article."""

from pydantic import BaseModel, Field

from hackernews_lite.config import LlmStage
from hackernews_lite.domain.hashing import content_hash
from hackernews_lite.domain.models import ArticlePrep, ArticleStatus, Story
from hackernews_lite.domain.text import clean_markers
from hackernews_lite.llm.client import LlmClient
from hackernews_lite.prep.prompts import Prompt
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.storage.story_store import StoryStore

MAX_ARTICLE_CHARS = 24_000  # about 6k tokens: plenty for a gist and key points


class ArticlePrepOutput(BaseModel):
    gist: str = Field(
        description="One or two sentences: what this is and why it might matter. "
        "Wrap its one to three defining phrases in **double asterisks**."
    )
    key_points: list[str] = Field(
        description="Three to five takeaways, each one spoken sentence, "
        "with its defining phrase wrapped in **double asterisks**."
    )


def prep_article(
    story: Story,
    store: StoryStore,
    llm: LlmClient,
    prompt: Prompt,
    stage: LlmStage,
    *,
    force: bool = False,
) -> StageResult:
    article = store.load_article(story.id)
    if article is None or article.status is not ArticleStatus.OK:
        return StageResult(StageStatus.SKIPPED, "no article text")

    truncated = len(article.text) > MAX_ARTICLE_CHARS
    text = article.text[:MAX_ARTICLE_CHARS]
    input_hash = content_hash(prompt.version, prompt.text, stage.model, story.title, text)

    existing = store.load_prep(story.id)
    if existing and existing.input_hash == input_hash and not force:
        return StageResult(StageStatus.CACHED)

    note = (
        "\n\n[The article was cut off here for length; say so if it matters.]" if truncated else ""
    )
    user = f"Title: {story.title}\n\nArticle:\n{text}{note}"
    response = llm.parse(
        model=stage.model,
        system=prompt.text,
        user=user,
        output_type=ArticlePrepOutput,
        max_tokens=stage.max_tokens,
        effort=stage.effort,
        story_id=story.id,
    )
    store.save_prep(
        story.id,
        ArticlePrep(
            gist=clean_markers(response.parsed.gist),
            key_points=[clean_markers(point) for point in response.parsed.key_points],
            model=stage.model,
            prompt_version=prompt.version,
            input_hash=input_hash,
        ),
    )
    return StageResult(StageStatus.DONE, "article cut for length" if truncated else "")
