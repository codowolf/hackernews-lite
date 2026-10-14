"""Append-only log of paid calls, and the monthly budget check built on it."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from hackernews_lite.domain.errors import BudgetExceededError


@dataclass(frozen=True)
class CostEvent:
    at: str  # ISO timestamp, UTC
    kind: str  # "llm" or "tts"
    model: str
    units: str  # human-readable usage, for example "1200 in / 300 out tokens"
    usd: float
    story_id: int | None = None


class CostLog:
    def __init__(self, path: Path, monthly_budget_usd: float) -> None:
        self._path = path
        self._budget = monthly_budget_usd

    def record(self, event: CostEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event)) + "\n")

    def events(self) -> list[CostEvent]:
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").splitlines()
        return [CostEvent(**json.loads(line)) for line in lines if line.strip()]

    def month_total(self, now: datetime | None = None) -> float:
        prefix = (now or datetime.now(UTC)).strftime("%Y-%m")
        return sum(e.usd for e in self.events() if e.at.startswith(prefix))

    def ensure_within_budget(self) -> None:
        spent = self.month_total()
        if spent >= self._budget:
            raise BudgetExceededError(
                f"Monthly budget reached: ${spent:.2f} of ${self._budget:.2f}. "
                "Raise budget.monthly_usd in config.toml to continue."
            )
