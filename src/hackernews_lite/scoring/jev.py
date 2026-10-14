"""Scores comments with TypeSafe's Jev model: typed questions in, probabilities out."""

from concurrent.futures import ThreadPoolExecutor

from typesafe_sdk import Noul, NoulAnswer, Score, ScoreAnswer, TypeSafeClient, TypeSafeError

from hackernews_lite.domain.errors import ScoringError
from hackernews_lite.domain.models import Comment, CommentSignals
from hackernews_lite.prep.comment_scoring import ScoringResult

QUESTIONS_VERSION = 1  # bump when the questions below change, so old scores are not reused
MAX_COMMENT_CHARS = 2000

QUESTIONS: dict[str, Noul | Score] = {
    "first_hand": Noul(
        instructions="The commenter describes their own first-hand experience or professional "
        "expertise with this subject, rather than just giving an opinion."
    ),
    "corrects": Noul(
        instructions="The comment corrects, contradicts, or adds an important caveat to the "
        "article."
    ),
    "adds_info": Noul(
        instructions="The comment adds specific facts, numbers, examples, or alternatives that "
        "the article does not have."
    ),
    "standalone": Noul(
        instructions="The comment makes sense on its own, without reading other comments first."
    ),
    "hostile": Noul(
        instructions="The comment is hostile, a personal attack, a flame, or a pile-on."
    ),
    "substance": Score(
        instructions="How substantive is the comment?",
        criteria=[
            "Low effort: a joke, a one-liner, a slogan, or off-topic",
            "Some substance, but a generic opinion",
            "Specific, well reasoned, and informative",
        ],
    ),
}


class JevScorer:
    questions_version = QUESTIONS_VERSION

    def __init__(
        self,
        model: str = "jev-latest",
        workers: int = 8,
        client: TypeSafeClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self._workers = workers
        self._client = client or TypeSafeClient(api_key=api_key)

    def score(self, *, article_title: str, comments: list[Comment]) -> ScoringResult:
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            outcomes = list(pool.map(lambda c: self._score_one(article_title, c), comments))
        signals = {comment.id: sig for comment, (sig, _) in zip(comments, outcomes, strict=True)}
        return ScoringResult(signals=signals, input_tokens=sum(tokens for _, tokens in outcomes))

    def _score_one(self, article_title: str, comment: Comment) -> tuple[CommentSignals, int]:
        try:
            response = self._client.system_one(
                state={"article_title": article_title, "comment": comment.text[:MAX_COMMENT_CHARS]},
                questions=QUESTIONS,
                model=self.model,
            )
        except TypeSafeError as exc:
            raise ScoringError(f"Jev request failed: {exc}") from exc
        answers = response.answers
        signals = CommentSignals(
            first_hand=_noul(answers["first_hand"]),
            corrects=_noul(answers["corrects"]),
            adds_info=_noul(answers["adds_info"]),
            standalone=_noul(answers["standalone"]),
            hostile=_noul(answers["hostile"]),
            substance=_score(answers["substance"]) / 2,  # three levels: 0, 1, 2
        )
        return signals, response.usage.input_tokens or 0


def _noul(answer: object) -> float:
    if not isinstance(answer, NoulAnswer):
        raise ScoringError(f"Expected a yes/no answer from Jev, got {type(answer).__name__}")
    return float(answer.noul)


def _score(answer: object) -> float:
    if not isinstance(answer, ScoreAnswer):
        raise ScoringError(f"Expected a rating from Jev, got {type(answer).__name__}")
    return float(answer.score)
