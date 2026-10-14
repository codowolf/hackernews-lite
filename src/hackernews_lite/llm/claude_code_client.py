"""LlmClient that runs headless Claude Code (`claude -p`) on a Claude subscription.

No API key and no per-call bill, but it counts against the subscription's usage limits.
"""

import json
import subprocess
import tempfile
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from hackernews_lite.config import Effort
from hackernews_lite.domain.errors import LlmOutputError
from hackernews_lite.llm.client import LlmResponse, Usage

Runner = Callable[[list[str], str], "subprocess.CompletedProcess[str]"]

TIMEOUT_SECONDS = 240


def _run(command: list[str], prompt: str) -> "subprocess.CompletedProcess[str]":
    # A neutral working directory keeps any project CLAUDE.md out of the prompt.
    with tempfile.TemporaryDirectory() as cwd:
        return subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )


class ClaudeCodeLlm:
    def __init__(self, executable: str = "claude", runner: Runner = _run) -> None:
        self._executable = executable
        self._runner = runner

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
        command = [
            self._executable,
            "-p",
            "--model",
            model,
            "--effort",
            effort,
            "--tools",
            "",  # no file or shell access: this is pure text in, text out
            "--setting-sources",
            "",  # ignore the user's hooks, memory and settings
            "--no-session-persistence",
            "--output-format",
            "json",
            "--system-prompt",
            system,
            "--json-schema",
            json.dumps(output_type.model_json_schema()),
        ]
        try:
            completed = self._runner(command, user)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LlmOutputError(f"Could not run `{self._executable} -p`: {exc}") from exc
        if completed.returncode != 0:
            raise LlmOutputError(f"`claude -p` failed: {completed.stderr.strip()[:300]}")

        try:
            envelope: dict[str, Any] = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LlmOutputError("`claude -p` did not return JSON") from exc
        if envelope.get("is_error"):
            raise LlmOutputError(
                f"`claude -p` reported an error: {str(envelope.get('result'))[:300]}"
            )
        if envelope.get("stop_reason") == "max_tokens":
            raise LlmOutputError("The reply was cut off.")
        structured = envelope.get("structured_output")
        if structured is None:
            raise LlmOutputError("`claude -p` returned no structured output")
        try:
            parsed = output_type.model_validate(structured)
        except ValidationError as exc:
            raise LlmOutputError(f"Reply did not match the expected structure: {exc}") from exc
        return LlmResponse(parsed=parsed, usage=_usage(envelope))


def _usage(envelope: dict[str, Any]) -> Usage:
    usage = envelope.get("usage") or {}
    input_tokens = sum(
        int(usage.get(key) or 0)
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    return Usage(input_tokens=input_tokens, output_tokens=int(usage.get("output_tokens") or 0))
