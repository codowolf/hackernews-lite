"""Stage: get the article text for a story (fetch, extract, or use the post body)."""

from hackernews_lite.domain.errors import ExtractionError, FetchError
from hackernews_lite.domain.models import Article, ArticleStatus, Story
from hackernews_lite.domain.text import word_count
from hackernews_lite.prep.extractor import extract_article
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.sources.article_fetcher import ArticleFetcher, deferred_reason
from hackernews_lite.storage.story_store import StoryStore


def prepare_article(
    story: Story, store: StoryStore, fetcher: ArticleFetcher, *, force: bool = False
) -> StageResult:
    existing = store.load_article(story.id)
    if existing and existing.status is not ArticleStatus.FAILED and not force:
        return StageResult(StageStatus.CACHED)  # failures are retried on the next run

    if story.url is None:
        if not story.text:
            return _record(
                store, story, Article(status=ArticleStatus.DEFERRED, reason="no link and no text")
            )
        article = Article(
            status=ArticleStatus.OK,
            title=story.title,
            text=story.text,
            word_count=word_count(story.text),
        )
        return _record(store, story, article, StageStatus.DONE, "post body")

    reason = deferred_reason(story.url)
    if reason:
        return _record(store, story, Article(status=ArticleStatus.DEFERRED, reason=reason))

    try:
        page = fetcher.fetch(story.url)
        store.save_raw_article(story.id, page.html)
        article = extract_article(page.html, page.url)
    except (FetchError, ExtractionError) as exc:
        article = Article(status=ArticleStatus.FAILED, reason=str(exc))
        store.save_article(story.id, article)
        return StageResult(StageStatus.FAILED, str(exc))
    return _record(store, story, article, StageStatus.DONE, f"{article.word_count} words")


def _record(
    store: StoryStore,
    story: Story,
    article: Article,
    status: StageStatus = StageStatus.SKIPPED,
    detail: str = "",
) -> StageResult:
    store.save_article(story.id, article)
    if article.status is ArticleStatus.DEFERRED:
        return StageResult(StageStatus.SKIPPED, f"not supported yet: {article.reason}")
    return StageResult(status, detail)
