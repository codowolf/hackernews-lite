"""How `claude -p` output is parsed, using canned subprocess results (nothing is executed)."""

import json
import subprocess

import pytest
from pydantic import BaseModel

from hackernews_lite.domain.errors import LlmOutputError
from hackernews_lite.llm.claude_code_client import ClaudeCodeLlm


class Out(BaseModel):
    gist: str


def completed(
    stdout: str, returncode: int = 0, stderr: str = ""
) -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def envelope(**overrides: object) -> str:
    base: dict[str, object] = {
        "is_error": False,
        "stop_reason": "end_turn",
        "structured_output": {"gist": "ok"},
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 1193,
            "cache_read_input_tokens": 0,
            "output_tokens": 106,
        },
    }
    base.update(overrides)
    return json.dumps(base)


def parse(stdout: str, returncode: int = 0) -> object:
    seen: list[list[str]] = []

    def runner(command: list[str], prompt: str) -> "subprocess.CompletedProcess[str]":
        seen.append(command)
        return completed(stdout, returncode)

    llm = ClaudeCodeLlm(runner=runner)
    response = llm.parse(
        model="claude-haiku-5-5",
        system="s",
        user="u",
        output_type=Out,
        max_tokens=100,
        effort="low",
    )
    assert "-p" in seen[0]
    assert seen[0][seen[0].index("--tools") + 1] == ""  # no tools allowed
    return response


def test_parses_structured_output_and_sums_input_tokens() -> None:
    response = parse(envelope())
    assert response.parsed == Out(gist="ok")  # type: ignore[attr-defined]
    assert response.usage.input_tokens == 1195  # type: ignore[attr-defined]
    assert response.usage.output_tokens == 106  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "stdout",
    [
        "not json",
        envelope(is_error=True, result="boom"),
        envelope(structured_output=None),
        envelope(structured_output={"wrong": 1}),
        envelope(stop_reason="max_tokens"),
    ],
)
def test_bad_output_raises(stdout: str) -> None:
    with pytest.raises(LlmOutputError):
        parse(stdout)


def test_nonzero_exit_raises() -> None:
    with pytest.raises(LlmOutputError):
        parse("", returncode=1)
