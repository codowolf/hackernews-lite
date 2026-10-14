"""Downloading the linked article, and deciding up front which links v1 does not handle."""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx2 as httpx

from hackernews_lite.domain.errors import BlockedError, FetchError

USER_AGENT = "Mozilla/5.0 (compatible; hackernews-lite/0.1; personal use)"
MAX_BYTES = 5_000_000
_BLOCKED_STATUSES = {401, 403, 429, 451, 503}  # the site said no, so a real browser may get in

_VIDEO_HOSTS = {"youtube.com", "youtu.be", "vimeo.com", "twitch.tv"}
_SOCIAL_HOSTS = {"twitter.com", "x.com", "mastodon.social", "bsky.app", "threads.net"}
_REPO_HOSTS = {"github.com", "gitlab.com"}


@dataclass(frozen=True)
class FetchedPage:
    url: str
    html: str


class ArticleFetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage: ...


def deferred_reason(url: str) -> str | None:
    """Why a link is skipped in v1, or None to fetch it (see README, "Not Supported Yet")."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")
    if parsed.path.lower().endswith(".pdf"):
        return "pdf"
    if host in _VIDEO_HOSTS:
        return "video"
    if host in _SOCIAL_HOSTS:
        return "social post"
    if host in _REPO_HOSTS:
        return "code repository"
    return None


class HttpArticleFetcher:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=20.0,
            follow_redirects=True,
        )

    def fetch(self, url: str) -> FetchedPage:
        try:
            response = self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error = BlockedError if exc.response.status_code in _BLOCKED_STATUSES else FetchError
            raise error(f"Could not fetch {url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"Could not fetch {url}: {exc}") from exc
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            raise FetchError(f"{url} is {content_type or 'an unknown type'}, not a web page")
        if len(response.content) > MAX_BYTES:
            raise FetchError(f"{url} is larger than {MAX_BYTES} bytes")
        return FetchedPage(url=str(response.url), html=response.text)


class FallbackArticleFetcher:
    """Tries the quick fetcher first, and a slower one only when the site turns it away."""

    def __init__(self, primary: ArticleFetcher, fallback: ArticleFetcher) -> None:
        self._primary = primary
        self._fallback = fallback

    def fetch(self, url: str) -> FetchedPage:
        try:
            return self._primary.fetch(url)
        except BlockedError as blocked:
            try:
                return self._fallback.fetch(url)
            except FetchError as exc:
                raise FetchError(
                    f"{blocked} (a real browser could not get it either: {exc})"
                ) from exc
