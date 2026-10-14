"""Fetching a page with a real Chrome, for sites that turn away plain downloads (bot checks)."""

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from hackernews_lite.domain.errors import FetchError
from hackernews_lite.sources.article_fetcher import (
    ArticleFetcher,
    FallbackArticleFetcher,
    FetchedPage,
    HttpArticleFetcher,
)

# Headless Chrome announces itself as "HeadlessChrome", which is exactly what bot checks look for.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
TIMEOUT_SECONDS = 45
_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
_CHROME_COMMANDS = ("google-chrome", "chromium", "chromium-browser")
_CHALLENGE_TITLES = ("attention required", "just a moment", "access denied")

Runner = Callable[[list[str]], str]  # runs a command and returns what it printed


def find_chrome() -> str | None:
    for path in _CHROME_PATHS:
        if Path(path).exists():
            return path
    return next((found for name in _CHROME_COMMANDS if (found := shutil.which(name))), None)


def _run(command: list[str]) -> str:
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"The browser took longer than {TIMEOUT_SECONDS}s") from exc
    return done.stdout


class BrowserArticleFetcher:
    def __init__(self, chrome: str, run: Runner = _run) -> None:
        self._chrome = chrome
        self._run = run

    def fetch(self, url: str) -> FetchedPage:
        with tempfile.TemporaryDirectory(prefix="hn-chrome-") as profile:
            html = self._run(
                [
                    self._chrome,
                    "--headless=new",
                    "--disable-gpu",
                    f"--user-data-dir={profile}",
                    f"--user-agent={BROWSER_USER_AGENT}",
                    "--disable-blink-features=AutomationControlled",
                    "--virtual-time-budget=20000",
                    "--dump-dom",
                    url,
                ]
            )
        head = html[:2000].lower()
        if not html.strip():
            raise FetchError(f"The browser returned nothing for {url}")
        if any(f"<title>{title}" in head for title in _CHALLENGE_TITLES):
            raise FetchError(f"The browser was also stopped by a bot check at {url}")
        return FetchedPage(url=url, html=html)


def build_article_fetcher() -> ArticleFetcher:
    """Plain downloads first; Chrome (if installed) for pages that refuse them."""
    plain = HttpArticleFetcher()
    chrome = find_chrome()
    return FallbackArticleFetcher(plain, BrowserArticleFetcher(chrome)) if chrome else plain
