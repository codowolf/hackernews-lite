"""The LLM seam. Stages depend on this interface, never on a vendor SDK."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from hackernews_lite.config import Effort


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LlmResponse[T: BaseModel]:
    parsed: T
    usage: Usage


class LlmClient(Protocol):
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
        """Run one prompt and return the reply validated as `output_type`."""
        ...
