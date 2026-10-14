import io

import pytest
from PIL import Image

from hackernews_lite.domain.errors import ImageError, ImageTooSmallError
from hackernews_lite.domain.models import ThumbnailStatus
from hackernews_lite.prep.stage import StageStatus
from hackernews_lite.prep.thumbnail import prepare_thumbnail, shrink_to_webp
from hackernews_lite.sources.images import find_image_url, youtube_thumbnail
from hackernews_lite.storage.story_store import StoryStore
from tests.conftest import FakeFetcher, make_story


def png(width: int = 1600, height: int = 900, mode: str = "RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), (200, 80, 20, 255)[: 4 if mode == "RGBA" else 3]).save(
        out, "PNG"
    )
    return out.getvalue()


class FakeImages:
    def __init__(self, data: bytes | None = None) -> None:
        self.data = data if data is not None else png()
        self.calls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        if self.data == b"fail":
            raise ImageError("boom")
        return self.data


PAGE = '<html><head><meta property="og:image" content="/img/card.png"></head><body></body></html>'


def test_finds_open_graph_image_and_makes_it_absolute() -> None:
    assert find_image_url(PAGE, "https://example.com/post") == "https://example.com/img/card.png"


def test_prefers_og_over_twitter_and_falls_back() -> None:
    both = '<meta name="twitter:image" content="https://t/x.png"><meta property="og:image" content="https://o/y.png">'
    assert find_image_url(both, "https://e.com") == "https://o/y.png"
    only_twitter = '<meta name="twitter:image" content="https://t/x.png">'
    assert find_image_url(only_twitter, "https://e.com") == "https://t/x.png"
    assert find_image_url("<html><head></head></html>", "https://e.com") is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        ),
        ("https://youtu.be/dQw4w9WgXcQ", "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg"),
        ("https://example.com/watch?v=abc", None),
    ],
)
def test_youtube_thumbnails(url: str, expected: str | None) -> None:
    assert youtube_thumbnail(url) == expected


def test_shrink_limits_width_and_outputs_webp() -> None:
    webp = shrink_to_webp(png(2400, 1200))
    image = Image.open(io.BytesIO(webp))
    assert image.format == "WEBP" and image.width == 800 and image.height == 400


def test_shrink_handles_transparency_and_rejects_junk() -> None:
    assert Image.open(io.BytesIO(shrink_to_webp(png(400, 300, "RGBA")))).mode == "RGB"
    with pytest.raises(ImageError):
        shrink_to_webp(b"not an image")


def test_stage_saves_image_from_the_saved_page(store: StoryStore) -> None:
    story = make_story()
    store.save_raw_article(story.id, PAGE)
    images, pages = FakeImages(), FakeFetcher()

    result = prepare_thumbnail(story, store, pages, images)

    assert result.status is StageStatus.DONE
    assert (
        images.calls == ["https://example.com/img/card.png"] and pages.calls == []
    )  # no new page fetch
    assert store.thumbnail_path(story.id).exists()
    assert store.load_thumbnail(story.id).status is ThumbnailStatus.OK  # type: ignore[union-attr]
    assert prepare_thumbnail(story, store, pages, images).status is StageStatus.CACHED


def test_stage_fetches_the_page_when_there_is_no_saved_article(store: StoryStore) -> None:
    story = make_story(url="https://example.com/blocked-for-article")
    pages, images = FakeFetcher(PAGE), FakeImages()
    assert prepare_thumbnail(story, store, pages, images).status is StageStatus.DONE
    assert pages.calls == ["https://example.com/blocked-for-article"]


def test_code_repositories_use_the_tile_not_their_text_card(store: StoryStore) -> None:
    story = make_story(url="https://github.com/org/repo")
    pages, images = FakeFetcher(PAGE), FakeImages()
    assert prepare_thumbnail(story, store, pages, images).status is StageStatus.SKIPPED
    assert pages.calls == [] and images.calls == []


def test_text_posts_pdfs_and_pages_without_images_are_recorded_as_none(store: StoryStore) -> None:
    images = FakeImages()
    for story_id, url, html in [
        (1, None, ""),
        (2, "https://example.com/paper.pdf", ""),
        (3, "https://example.com/plain", "<html><head></head></html>"),
    ]:
        story = make_story(story_id, url=url)
        result = prepare_thumbnail(story, store, FakeFetcher(html), images)
        assert result.status is StageStatus.SKIPPED
        assert store.load_thumbnail(story_id).status is ThumbnailStatus.NONE  # type: ignore[union-attr]
        assert not store.thumbnail_path(story_id).exists()
    assert images.calls == []


def test_failures_are_recorded_and_retried(store: StoryStore) -> None:
    story = make_story()
    store.save_raw_article(story.id, PAGE)
    assert (
        prepare_thumbnail(story, store, FakeFetcher(), FakeImages(b"fail")).status
        is StageStatus.FAILED
    )
    assert store.load_thumbnail(story.id).status is ThumbnailStatus.FAILED  # type: ignore[union-attr]
    assert prepare_thumbnail(story, store, FakeFetcher(), FakeImages()).status is StageStatus.DONE


def test_tiny_images_are_treated_as_no_image(store: StoryStore) -> None:
    with pytest.raises(ImageTooSmallError):
        shrink_to_webp(png(40, 40))
    story = make_story()
    store.save_raw_article(story.id, PAGE)
    result = prepare_thumbnail(story, store, FakeFetcher(), FakeImages(png(1, 1)))
    assert result.status is StageStatus.SKIPPED
    assert store.load_thumbnail(story.id).status is ThumbnailStatus.NONE  # type: ignore[union-attr]
