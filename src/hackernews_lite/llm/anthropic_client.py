"""Anthropic implementation of LlmClient."""

import anthropic
from pydantic import BaseModel

from hackernews_lite.config import Effort
from hackernews_lite.domain.errors import LlmOutputError
from hackernews_lite.llm.client import LlmResponse, Usage


class AnthropicLlm:
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        # With no argument the SDK reads ANTHROPIC_API_KEY (or a logged-in profile) itself.
        self._client = client or anthropic.Anthropic()

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
        try:
            message = self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_type,
                output_config={"effort": effort},
            )
        except anthropic.APIError as exc:
            raise LlmOutputError(f"Anthropic call failed: {exc}") from exc

        if message.stop_reason == "refusal":
            raise LlmOutputError("The model declined this request.")
        if message.stop_reason == "max_tokens":
            raise LlmOutputError("The reply was cut off at max_tokens; raise it in config.toml.")
        if message.parsed_output is None:
            raise LlmOutputError("The reply did not match the expected structure.")
        usage = Usage(message.usage.input_tokens, message.usage.output_tokens)
        return LlmResponse(parsed=message.parsed_output, usage=usage)
