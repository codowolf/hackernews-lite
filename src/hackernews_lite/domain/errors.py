"""Typed errors, so the pipeline can report each failure per story and per stage."""


class HnListenerError(Exception):
    """Base class for every error this project raises on purpose."""


class SourceError(HnListenerError):
    """The Hacker News API returned something unusable."""


class FetchError(HnListenerError):
    """An article page could not be downloaded."""


class BlockedError(FetchError):
    """The site refused the download (a bot check, a login wall, or too many requests)."""


class ExtractionError(HnListenerError):
    """An article page was downloaded but no readable text could be extracted."""


class LlmOutputError(HnListenerError):
    """The model refused, was cut off, or returned output that failed validation."""


class BudgetExceededError(HnListenerError):
    """The monthly spend cap has been reached; no more paid calls are made."""


class SpeechError(HnListenerError):
    """A text-to-speech provider failed or is not set up."""


class ScoringError(HnListenerError):
    """The comment-scoring service failed."""


class ImageError(HnListenerError):
    """A thumbnail image could not be downloaded or decoded."""


class ImageTooSmallError(ImageError):
    """The image is a tracking pixel or icon, not a real preview."""
