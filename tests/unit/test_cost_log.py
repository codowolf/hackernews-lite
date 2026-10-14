from datetime import UTC, datetime
from pathlib import Path

import pytest

from hackernews_lite.domain.errors import BudgetExceededError
from hackernews_lite.storage.cost_log import CostEvent, CostLog


def event(usd: float, at: str) -> CostEvent:
    return CostEvent(at=at, kind="llm", model="m", units="u", usd=usd)


def test_month_total_ignores_other_months(tmp_path: Path) -> None:
    log = CostLog(tmp_path / "cost.jsonl", monthly_budget_usd=10)
    log.record(event(1.5, "2026-10-01T00:00:00+00:00"))
    log.record(event(2.0, "2026-09-30T23:00:00+00:00"))
    assert log.month_total(datetime(2026, 10, 12, tzinfo=UTC)) == 1.5


def test_budget_is_enforced(tmp_path: Path) -> None:
    log = CostLog(tmp_path / "cost.jsonl", monthly_budget_usd=1)
    log.ensure_within_budget()
    log.record(event(1.0, datetime.now(UTC).isoformat()))
    with pytest.raises(BudgetExceededError):
        log.ensure_within_budget()
