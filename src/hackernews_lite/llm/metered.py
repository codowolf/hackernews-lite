"""Decorator that adds budget enforcement and cost logging to any LlmClient."""

from datetime import UTC, datetime

from pydantic import BaseModel

from hackernews_lite.config import Effort, ModelPrice
from hackernews_lite.domain.errors import HnListenerError
from hackernews_lite.llm.client import LlmClient, LlmResponse, Usage
from hackernews_lite.storage.cost_log import CostEvent, CostLog


def llm_cost_usd(usage: Usage, price: ModelPrice) -> float:
    return (usage.input_tokens * price.input + usage.output_tokens * price.output) / 1_000_000


class MeteredLlm:
    def __init__(
        self,
        inner: LlmClient,
        pricing: dict[str, ModelPrice],
        log: CostLog,
        *,
        billable: bool = True,
    ) -> None:
        """`billable=False` (subscription use) logs tokens but applies no dollars or cap."""
        self._billable = billable
        self._inner = inner
        self._pricing = pricing
        self._log = log

    def parse[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        user: str,
        output_type: type[T],
        max_tokens: int,
        effort: Effort,
        story_id: int | None = None,
    ) -> LlmResponse[T]:
        if self._billable:
            if model not in self._pricing:
                raise HnListenerError(f"No price for model '{model}' in config.toml [pricing]")
            self._log.ensure_within_budget()
        response = self._inner.parse(
            model=model,
            system=system,
            user=user,
            output_type=output_type,
            max_tokens=max_tokens,
            effort=effort,
            story_id=story_id,
        )
        usage = response.usage
        self._log.record(
            CostEvent(
                at=datetime.now(UTC).isoformat(timespec="seconds"),
                kind="llm",
                model=model,
                units=f"{usage.input_tokens} in / {usage.output_tokens} out tokens"
                + ("" if self._billable else " (subscription)"),
                usd=llm_cost_usd(usage, self._pricing[model]) if self._billable else 0.0,
                story_id=story_id,
            )
        )
        return response
