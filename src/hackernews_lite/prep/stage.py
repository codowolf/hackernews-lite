"""Shared result type for pipeline stages."""

from dataclasses import dataclass
from enum import StrEnum


class StageStatus(StrEnum):
    DONE = "done"  # ran and wrote output
    CACHED = "cached"  # output already valid for this input; nothing spent
    SKIPPED = "skipped"  # not applicable (for example, no article to summarize)
    FAILED = "failed"  # tried and failed; the detail says why


@dataclass(frozen=True)
class StageResult:
    status: StageStatus
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.status.value}: {self.detail}" if self.detail else self.status.value
