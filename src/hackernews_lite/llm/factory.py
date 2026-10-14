"""Builds the configured LlmClient. One of the few places that names vendors."""

from hackernews_lite.config import Settings
from hackernews_lite.llm.anthropic_client import AnthropicLlm
from hackernews_lite.llm.claude_code_client import ClaudeCodeLlm
from hackernews_lite.llm.client import LlmClient
from hackernews_lite.llm.metered import MeteredLlm
from hackernews_lite.storage.cost_log import CostLog


def build_llm(settings: Settings, log: CostLog) -> LlmClient:
    if settings.llm.provider == "anthropic":
        return MeteredLlm(AnthropicLlm(), settings.pricing, log)
    return MeteredLlm(ClaudeCodeLlm(), settings.pricing, log, billable=False)
