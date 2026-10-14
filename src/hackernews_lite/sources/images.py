"""Finding and downloading an article's preview image (its Open Graph image)."""

import re
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import parse_qs, urljoin, urlparse

import httpx2 as httpx

from hackernews_lite.domain.errors import ImageError

USER_AGENT = "Mozilla/5.0 (compatible; hackernews-lite/0.1; personal use)"
MAX_IMAGE_BYTES = 6_000_000
# In order of preference: the secure Open Graph image, then Twitter's card images.
_IMAGE_KEYS = ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")


class _MetaImages(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        fields = {key: value for key, value in attrs if value}
        key = (fields.get("property") or fields.get("name") or "").lower()
        if key in _IMAGE_KEYS and "content" in fields:
            self.found.setdefault(key, fields["content"])


def find_image_url(html: str, base_url: str) -> str | None:
    """The page's preview image as an absolute URL, or None."""
    parser = _MetaImages()
    parser.feed(html[:200_000])  # the meta tags are in the head, near the top
    for key in _IMAGE_KEYS:
        if key in parser.found:
            return urljoin(base_url, parser.found[key].strip())
    return None


def youtube_thumbnail(url: str) -> str | None:
    """YouTube links have a predictable thumbnail, so no page fetch is needed."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")
    video_id: str | None = None
    if host == "youtu.be":
        video_id = parsed.path.lstrip("/") or None
    elif host in {"youtube.com", "m.youtube.com"}:
        video_id = parse_qs(parsed.query).get("v", [None])[0]
    if video_id and re.fullmatch(r"[\w-]{6,20}", video_id):
        return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    return None


class ImageFetcher(Protocol):
    def fetch(self, url: str) -> bytes: ...


class HttpImageFetcher:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True
        )

    def fetch(self, url: str) -> bytes:
        try:
            response = self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ImageError(f"Could not download image {url}: {exc}") from exc
        kind = response.headers.get("content-type", "")
        if not kind.startswith("image/") or "svg" in kind:
            raise ImageError(f"{url} is {kind or 'not an image'}, which cannot be used")
        if len(response.content) > MAX_IMAGE_BYTES:
            raise ImageError(f"{url} is larger than {MAX_IMAGE_BYTES} bytes")
        return response.content
