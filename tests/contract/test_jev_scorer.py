"""Runs JevScorer against canned responses shaped like the TypeSafe wire format."""

import json

import httpx2 as httpx
import pytest
from typesafe_sdk import TypeSafeClient

from hackernews_lite.domain.errors import ScoringError
from hackernews_lite.scoring.jev import JevScorer
from tests.conftest import make_comments


def response_body() -> dict[str, object]:
    def noul(value: float) -> dict[str, object]:
        return {"type": "noul", "noul": value}

    return {
        "model": "jev-1.13.0",
        "answers": {
            "first_hand": noul(0.9),
            "corrects": noul(0.1),
            "adds_info": noul(0.4),
            "standalone": noul(0.8),
            "hostile": noul(0.05),
            "substance": {
                "type": "score",
                "score": 1.5,
                "confidence": 0.6,
                "legend": {"0": "low", "1": "some", "2": "high"},
                "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5},
            },
        },
        "usage": {"input_tokens": 300, "output_tokens": 20},
    }


def scorer_returning(status: int, body: dict[str, object]) -> JevScorer:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    client = TypeSafeClient(api_key="test-key", transport=httpx.MockTransport(handler), retry=None)
    scorer = JevScorer(client=client)
    scorer.seen = seen  # type: ignore[attr-defined]
    return scorer


def test_scores_each_comment_and_sums_tokens() -> None:
    scorer = scorer_returning(200, response_body())
    comments = make_comments()
    result = scorer.score(article_title="T", comments=comments)

    assert set(result.signals) == {c.id for c in comments}
    one = result.signals[11]
    assert one.first_hand == 0.9 and one.hostile == 0.05
    assert one.substance == 0.75  # score 1.5 on a 0 to 2 scale
    assert result.input_tokens == 300 * len(comments)


def test_request_carries_the_comment_and_all_six_questions() -> None:
    scorer = scorer_returning(200, response_body())
    scorer.score(article_title="Title", comments=make_comments()[:1])
    sent = json.loads(scorer.seen[0].content)  # type: ignore[attr-defined]
    assert sent["state"]["article_title"] == "Title"
    assert "upgrades" in sent["state"]["comment"]
    assert set(sent["questions"]) == {
        "first_hand",
        "corrects",
        "adds_info",
        "standalone",
        "hostile",
        "substance",
    }


def test_api_errors_become_scoring_errors() -> None:
    scorer = scorer_returning(401, {"error": "bad key"})
    with pytest.raises(ScoringError):
        scorer.score(article_title="T", comments=make_comments()[:1])
