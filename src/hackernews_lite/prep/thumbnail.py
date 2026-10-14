"""Stage: save a small preview image for a story, so the list has something to look at."""

import io

from PIL import Image, UnidentifiedImageError

from hackernews_lite.domain.errors import FetchError, ImageError, ImageTooSmallError
from hackernews_lite.domain.models import Story, Thumbnail, ThumbnailStatus
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.sources.article_fetcher import ArticleFetcher, deferred_reason
from hackernews_lite.sources.images import ImageFetcher, find_image_url, youtube_thumbnail
from hackernews_lite.storage.story_store import StoryStore

MIN_WIDTH, MIN_HEIGHT = 200, 100  # smaller is an icon or a tracking pixel
MAX_WIDTH = 800  # plenty for a phone card, small on disk
WEBP_QUALITY = 78
# Nothing useful to show: no page to read (pdf, social), or a text-only card unreadable when small.
NO_PAGE_TO_READ = {"pdf", "social post", "code repository"}


def shrink_to_webp(data: bytes) -> bytes:
    """Decode any common image format and re-encode it as a small WebP."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageError(f"Not a usable image: {exc}") from exc
    if image.width < MIN_WIDTH or image.height < MIN_HEIGHT:
        raise ImageTooSmallError(f"{image.width}x{image.height} is too small for a preview")
    flat = Image.new("RGB", image.size, (255, 255, 255))  # transparent areas become white
    flat.paste(image, mask=image.getchannel("A"))
    flat.thumbnail((MAX_WIDTH, MAX_WIDTH))
    out = io.BytesIO()
    flat.save(out, format="WEBP", quality=WEBP_QUALITY)
    return out.getvalue()


def prepare_thumbnail(
    story: Story,
    store: StoryStore,
    pages: ArticleFetcher,
    images: ImageFetcher,
    *,
    force: bool = False,
) -> StageResult:
    existing = store.load_thumbnail(story.id)
    if existing and existing.status is not ThumbnailStatus.FAILED and not force:
        return StageResult(StageStatus.CACHED)  # failures are retried on the next run

    if story.url is None:
        return _none(store, story, "text post")
    try:
        image_url = youtube_thumbnail(story.url) or _image_from_page(story, store, pages)
        if image_url is None:
            return _none(store, story, "the page has no preview image")
        webp = shrink_to_webp(images.fetch(image_url))
    except ImageTooSmallError as exc:
        return _none(store, story, str(exc))
    except (FetchError, ImageError) as exc:
        store.save_thumbnail(story.id, Thumbnail(status=ThumbnailStatus.FAILED, reason=str(exc)))
        return StageResult(StageStatus.FAILED, str(exc))
    store.save_thumbnail(story.id, Thumbnail(status=ThumbnailStatus.OK, source_url=image_url), webp)
    return StageResult(StageStatus.DONE, f"{len(webp) // 1024} KB")


def _image_from_page(story: Story, store: StoryStore, pages: ArticleFetcher) -> str | None:
    assert story.url is not None
    if deferred_reason(story.url) in NO_PAGE_TO_READ:
        return None
    html = store.load_raw_article(story.id)
    base = story.url
    if html is None:
        page = pages.fetch(
            story.url
        )  # skipped as an article, but its page may still have a picture
        html, base = page.html, page.url
    return find_image_url(html, base)


def _none(store: StoryStore, story: Story, reason: str) -> StageResult:
    store.save_thumbnail(story.id, Thumbnail(status=ThumbnailStatus.NONE, reason=reason))
    return StageResult(StageStatus.SKIPPED, reason)
