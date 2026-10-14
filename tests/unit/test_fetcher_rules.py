import httpx2 as httpx
import pytest

from hackernews_lite.domain.errors import BlockedError, FetchError
from hackernews_lite.sources.article_fetcher import (
    FallbackArticleFetcher,
    FetchedPage,
    HttpArticleFetcher,
    deferred_reason,
)
from hackernews_lite.sources.browser_fetcher import BrowserArticleFetcher


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://example.com/paper.pdf", "pdf"),
        ("https://www.youtube.com/watch?v=1", "video"),
        ("https://x.com/someone/status/1", "social post"),
        ("https://github.com/org/repo", "code repository"),
        ("https://example.com/blog/post", None),
    ],
)
def test_deferred_reason(url: str, reason: str | None) -> None:
    assert deferred_reason(url) == reason


def _status_client(status: int) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status)))


@pytest.mark.parametrize(("status", "error"), [(403, BlockedError), (404, FetchError)])
def test_only_refusals_count_as_blocked(status: int, error: type[FetchError]) -> None:
    with pytest.raises(error) as caught:
        HttpArticleFetcher(_status_client(status)).fetch("https://example.com/a")
    assert type(caught.value) is error


class Scripted:
    def __init__(self, result: FetchedPage | FetchError) -> None:
        self.result = result
        self.calls = 0

    def fetch(self, url: str) -> FetchedPage:
        self.calls += 1
        if isinstance(self.result, FetchError):
            raise self.result
        return self.result


PAGE = FetchedPage(url="https://example.com/a", html="<html>ok</html>")


def test_a_blocked_page_is_retried_in_the_browser() -> None:
    browser = Scripted(PAGE)
    fetcher = FallbackArticleFetcher(Scripted(BlockedError("403")), browser)
    assert fetcher.fetch(PAGE.url) == PAGE and browser.calls == 1


def test_a_missing_page_does_not_start_a_browser() -> None:
    browser = Scripted(PAGE)
    with pytest.raises(FetchError):
        FallbackArticleFetcher(Scripted(FetchError("404")), browser).fetch(PAGE.url)
    assert browser.calls == 0


def test_when_the_browser_fails_too_both_reasons_are_reported() -> None:
    fetcher = FallbackArticleFetcher(
        Scripted(BlockedError("403 Forbidden")), Scripted(FetchError("bot check"))
    )
    with pytest.raises(FetchError, match=r"403 Forbidden.*bot check"):
        fetcher.fetch(PAGE.url)


def test_the_browser_looks_like_a_normal_one_and_rejects_bot_check_pages() -> None:
    seen: list[str] = []

    def run(command: list[str]) -> str:
        seen.extend(command)
        return "<html><head><title>Attention Required! | Cloudflare</title>"

    with pytest.raises(FetchError, match="bot check"):
        BrowserArticleFetcher("chrome", run).fetch(PAGE.url)
    assert any(arg.startswith("--user-agent=") and "Headless" not in arg for arg in seen)


def test_the_browser_returns_the_page_it_loaded() -> None:
    fetcher = BrowserArticleFetcher("chrome", lambda command: "<html><title>Hi</title></html>")
    assert fetcher.fetch(PAGE.url).html.startswith("<html>")
