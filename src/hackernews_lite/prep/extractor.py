"""Turn a downloaded web page into clean article text."""

import trafilatura

from hackernews_lite.domain.errors import ExtractionError
from hackernews_lite.domain.models import Article, ArticleStatus
from hackernews_lite.domain.text import word_count

MIN_CHARS = 400


def extract_article(html: str, url: str) -> Article:
    text = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=False,
    )
    if not text or len(text) < MIN_CHARS:
        raise ExtractionError(
            "No readable article text found (JS-only page, paywall, or not an article)"
        )
    metadata = trafilatura.extract_metadata(html, default_url=url)
    return Article(
        status=ArticleStatus.OK,
        title=metadata.title if metadata else None,
        text=text,
        word_count=word_count(text),
    )
