"""Labels how a comment relates to the article with Jev: one cheap question per comment."""

from concurrent.futures import ThreadPoolExecutor

from typesafe_sdk import Choice, ChoiceAnswer, TypeSafeClient, TypeSafeError

from hackernews_lite.domain.errors import ScoringError
from hackernews_lite.domain.models import Comment, Stance
from hackernews_lite.prep.comment_stance_quick import LabelResult, StanceLabel

QUESTIONS_VERSION = 1  # bump when the question below changes, so saved labels are not reused
MAX_COMMENT_CHARS = 1200

QUESTION = Choice(
    instructions="How does this comment relate to the article's main claim or the post's position?",
    criteria={
        "agree": "Supports, confirms, or builds on the main claim",
        "disagree": (
            "Challenges, contradicts, doubts, or criticizes the claim, the author, or the source"
        ),
        "mixed": "Partly agrees and partly disagrees",
        "neutral": "A tangent, a question, a joke, or otherwise takes no side",
    },
)


class JevStanceLabeler:
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

    def label(
        self, *, article_title: str, article_summary: str, comments: list[Comment]
    ) -> LabelResult:
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            outcomes = list(
                pool.map(lambda c: self._label_one(article_title, article_summary, c), comments)
            )
        labels = {c.id: label for c, (label, _) in zip(comments, outcomes, strict=True)}
        return LabelResult(labels=labels, input_tokens=sum(tokens for _, tokens in outcomes))

    def _label_one(self, title: str, summary: str, comment: Comment) -> tuple[StanceLabel, int]:
        try:
            response = self._client.system_one(
                state={
                    "article_title": title,
                    "article_summary": summary,
                    "comment": comment.text[:MAX_COMMENT_CHARS],
                },
                questions={"stance": QUESTION},
                model=self.model,
            )
        except TypeSafeError as exc:
            raise ScoringError(f"Jev request failed: {exc}") from exc
        answer = response.answers["stance"]
        if not isinstance(answer, ChoiceAnswer):
            raise ScoringError(f"Expected a choice from Jev, got {type(answer).__name__}")
        try:
            stance = Stance(answer.choice)
        except ValueError as exc:
            raise ScoringError(f"Jev returned an unknown stance '{answer.choice}'") from exc
        return StanceLabel(stance, float(answer.confidence)), response.usage.input_tokens or 0
