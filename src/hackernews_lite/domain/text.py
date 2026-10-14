"""Pure text helpers: HN's HTML to plain text, and plain text to something fit for the ear."""

import html
import re
from html.parser import HTMLParser

from hackernews_lite.domain.models import Link

_URL = re.compile(r"https?://\S+")
_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")
_WORD = re.compile(r"[a-z0-9']+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "br"}:
            self.parts.append("\n\n" if tag == "p" else "\n")
        elif tag == "pre":
            self._in_pre = True
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self._in_pre = False
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = "".join(self._text).strip()
            if text and self._href.startswith(("http://", "https://")):
                self.links.append(Link(text=text, href=self._href))
            self._href = None


def extract_links(raw: str | None) -> list[Link]:
    """The links in a piece of HN's HTML, in order, each with its full address."""
    if not raw:
        return []
    parser = _LinkExtractor()
    parser.feed(raw)
    return parser.links


def short_link(href: str, limit: int = 38) -> str:
    """An address trimmed for a small button: no scheme, no www, cut with an ellipsis."""
    label = re.sub(r"^https?://(www\.)?", "", href).rstrip("/")
    return label if len(label) <= limit else label[: limit - 1] + "…"


def html_to_text(raw: str | None) -> str:
    """Convert the small HTML subset HN uses (p, a, i, pre, code) into plain paragraphs."""
    if not raw:
        return ""
    parser = _TextExtractor()
    parser.feed(raw)
    text = html.unescape("".join(parser.parts))
    text = _SPACES.sub(" ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return _BLANKS.sub("\n\n", text).strip()


def make_speakable(text: str) -> str:
    """Strip what sounds bad read aloud: raw URLs and stray markdown."""
    text = _URL.sub("a link", text)
    text = re.sub(r"[`*_#>]+", "", text)
    text = _SPACES.sub(" ", text)
    return _BLANKS.sub("\n\n", text).strip()


_SPAN = re.compile(r"\*\*([^*\n]+?)\*\*")
MAX_SPANS = 3  # defining phrases per piece of text
MAX_SPAN_WORDS = 8  # a phrase longer than this is a sentence, not a highlight
MAX_BOLD_SHARE = 0.4  # at most this share of the words may be highlighted


def strip_markers(text: str) -> str:
    """Remove `**` highlight markers, keeping the words (for the ear, lists and plain text)."""
    return text.replace("**", "")


def clean_markers(text: str) -> str:
    """Keep only sensible `**phrase**` highlights from model-written text.

    A few short phrases survive. Malformed markers, whole-sentence bolding, too many highlights,
    or highlighting most of the text all fall back to plain text, so the words are never harmed.
    """
    spans = list(_SPAN.finditer(text))
    if _SPAN.sub("", text).count("*"):  # a stray or unbalanced marker: trust none of them
        return text.replace("*", "")
    keep = [m for m in spans if 0 < len(m.group(1).split()) <= MAX_SPAN_WORDS][:MAX_SPANS]
    bold_words = sum(len(m.group(1).split()) for m in keep)
    if bold_words > MAX_BOLD_SHARE * max(len(strip_markers(text).split()), 1):
        return strip_markers(text)
    kept = {m.span() for m in keep}
    return _SPAN.sub(lambda m: m.group(0) if m.span() in kept else m.group(1), text)


def validate_emphasis(phrases: list[str], text: str) -> list[str]:
    """The model's phrases for a verbatim quote, kept only if they are real and sensible.

    Each must appear exactly in `text`, be 2 to 8 words, and not overlap another. At most three
    are kept, in the order they appear, and together they may not cover most of the text.
    """
    found: list[tuple[int, int, str]] = []
    for raw in phrases:
        phrase = raw.strip()
        start = text.find(phrase) if phrase else -1
        if start < 0 or not 2 <= len(phrase.split()) <= MAX_SPAN_WORDS:
            continue
        span = (start, start + len(phrase), phrase)
        if all(span[1] <= other[0] or other[1] <= span[0] for other in found):
            found.append(span)
    found.sort()
    kept = [phrase for _, _, phrase in found[:MAX_SPANS]]
    budget = MAX_BOLD_SHARE * max(len(text.split()), 1)
    while kept and sum(len(p.split()) for p in kept) > budget:
        kept.pop()  # drop the latest highlights until the quote is not mostly bold
    return kept


def trim_to_words(text: str, count: int) -> str:
    """The first `count` words, cut at a sentence end when one is close enough."""
    words = text.split()
    if len(words) <= count:
        return text
    clipped = " ".join(words[:count])
    sentence_end = max(clipped.rfind(". "), clipped.rfind("? "), clipped.rfind("! "))
    return clipped[: sentence_end + 1] if sentence_end > len(clipped) // 2 else clipped + "..."


def word_count(text: str) -> int:
    return len(text.split())


def is_excerpt(shown: str, full: str) -> bool:
    """True if `shown` is a noticeably shorter part of `full`: worth saying the rest exists.

    Small differences do not count: a link became "a link", or a stray phrase was dropped.
    """
    missing = word_count(full) - word_count(shown)
    return missing > max(5, word_count(full) // 10)


def is_faithful(spoken: str, original: str, threshold: float = 0.9) -> bool:
    """True if nearly every word of `spoken` appears in `original`.

    Guards the "read the real comment" promise: trimming is allowed, rewriting is not.
    """
    spoken_words = _WORD.findall(spoken.lower())
    if not spoken_words:
        return False
    original_words = set(_WORD.findall(original.lower()))
    found = sum(1 for word in spoken_words if word in original_words)
    return found / len(spoken_words) >= threshold
