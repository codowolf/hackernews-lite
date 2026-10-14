"""Decorator that adds budget enforcement and cost logging to any CommentScorer."""

from datetime import UTC, datetime

from hackernews_lite.config import ModelPrice
from hackernews_lite.domain.errors import HnListenerError
from hackernews_lite.domain.models import Comment
from hackernews_lite.prep.comment_scoring import CommentScorer, ScoringResult
from hackernews_lite.prep.comment_stance_quick import LabelResult, StanceLabeler
from hackernews_lite.storage.cost_log import CostEvent, CostLog


class MeteredScorer:
    def __init__(self, inner: CommentScorer, pricing: dict[str, ModelPrice], log: CostLog) -> None:
        self._inner = inner
        self._pricing = pricing
        self._log = log
        self.model = inner.model
        self.questions_version = inner.questions_version

    def score(self, *, article_title: str, comments: list[Comment]) -> ScoringResult:
        if self.model not in self._pricing:
            raise HnListenerError(f"No price for model '{self.model}' in config.toml [pricing]")
        self._log.ensure_within_budget()
        result = self._inner.score(article_title=article_title, comments=comments)
        self._log.record(
            CostEvent(
                at=datetime.now(UTC).isoformat(timespec="seconds"),
                kind="scoring",
                model=self.model,
                units=f"{result.input_tokens} in tokens, {len(comments)} comments",
                usd=result.input_tokens * self._pricing[self.model].input / 1_000_000,
            )
        )
        return result


class MeteredLabeler:
    """Budget check and cost logging for any StanceLabeler."""

    def __init__(self, inner: StanceLabeler, pricing: dict[str, ModelPrice], log: CostLog) -> None:
        self._inner = inner
        self._pricing = pricing
        self._log = log
        self.model = inner.model
        self.questions_version = inner.questions_version

    def label(
        self, *, article_title: str, article_summary: str, comments: list[Comment]
    ) -> LabelResult:
        if self.model not in self._pricing:
            raise HnListenerError(f"No price for model '{self.model}' in config.toml [pricing]")
        self._log.ensure_within_budget()
        result = self._inner.label(
            article_title=article_title, article_summary=article_summary, comments=comments
        )
        self._log.record(
            CostEvent(
                at=datetime.now(UTC).isoformat(timespec="seconds"),
                kind="stance",
                model=self.model,
                units=f"{result.input_tokens} in tokens, {len(comments)} comments",
                usd=result.input_tokens * self._pricing[self.model].input / 1_000_000,
            )
        )
        return result
