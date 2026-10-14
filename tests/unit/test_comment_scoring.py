from hackernews_lite.config import Curation, Weights
from hackernews_lite.domain.models import CommentScoreSet, CommentSignals, ScoredComment
from hackernews_lite.prep.comment_scoring import (
    Candidate,
    ScoringResult,
    candidates_from,
    composite,
    pick_by_score,
    select_picks,
)
from hackernews_lite.prep.stage import StageStatus
from hackernews_lite.storage.story_store import StoryStore
from tests.conftest import make_comments, make_story


def signals(**kw: float) -> CommentSignals:
    base = {
        "first_hand": 0.0,
        "corrects": 0.0,
        "substance": 0.0,
        "adds_info": 0.0,
        "standalone": 1.0,
        "hostile": 0.0,
    }
    base.update(kw)
    return CommentSignals(**base)


def score_set(**by_id: CommentSignals) -> CommentScoreSet:
    return CommentScoreSet(
        model="m",
        questions_version=1,
        scores={int(k[1:]): ScoredComment(text_hash="h", signals=v) for k, v in by_id.items()},
    )


def test_composite_is_about_one_for_a_perfect_comment_and_negative_for_a_flame() -> None:
    perfect = signals(first_hand=1, corrects=1, substance=1, adds_info=1)
    flame = signals(hostile=1, standalone=0)
    assert abs(composite(perfect, Weights()) - 1.0) < 1e-9
    assert composite(flame, Weights()) < 0


def test_picks_are_ranked_best_first_and_weak_ones_dropped() -> None:
    candidates = candidates_from(make_comments())
    scores = score_set(
        c11=signals(first_hand=0.9, substance=0.8),
        c21=signals(substance=0.1),
        c31=signals(corrects=0.9, substance=0.9, adds_info=0.5),
    )
    picks = select_picks(candidates, scores, Curation())
    assert [p.comment_id for p in picks] == [31, 11]  # 21 is below min_score


def test_weights_change_the_ranking() -> None:
    candidates = candidates_from(make_comments())
    scores = score_set(
        c11=signals(first_hand=0.9, substance=0.5),
        c31=signals(corrects=0.9, substance=0.5),
    )
    expert_first = Curation(weights=Weights(first_hand=5, corrects=0.5))
    pushback_first = Curation(weights=Weights(first_hand=0.5, corrects=5))
    assert select_picks(candidates, scores, expert_first)[0].comment_id == 11
    assert select_picks(candidates, scores, pushback_first)[0].comment_id == 31


def test_at_most_max_per_thread_from_one_thread() -> None:
    candidates = candidates_from(make_comments())  # 11 has reply 12
    strong = signals(first_hand=1, substance=1)
    scores = score_set(c11=strong, c12=strong)
    assert len(select_picks(candidates, scores, Curation(max_per_thread=1))) == 1
    assert len(select_picks(candidates, scores, Curation(max_per_thread=2))) == 2


def test_intro_matches_strongest_signal_and_ends_with_says() -> None:
    candidates = candidates_from(make_comments())
    scores = score_set(
        c11=signals(first_hand=0.9, substance=0.9), c12=signals(corrects=0.9, substance=0.9)
    )
    picks = {p.comment_id: p for p in select_picks(candidates, scores, Curation())}
    assert picks[11].intro == "A commenter with first-hand experience says"
    assert picks[12].intro == "A reply pushing back says"
    assert picks[11].reply_ids == [12]


def test_candidate_limits() -> None:
    assert [c.comment.id for c in candidates_from(make_comments())] == [11, 12, 21, 31]
    assert isinstance(candidates_from(make_comments())[1], Candidate)


class FakeScorer:
    model = "fake"
    questions_version = 1

    def __init__(self) -> None:
        self.scored: list[int] = []

    def score(self, *, article_title: str, comments: list) -> ScoringResult:  # type: ignore[type-arg]
        self.scored.extend(c.id for c in comments)
        return ScoringResult(
            signals={c.id: signals(first_hand=0.9, substance=0.9) for c in comments},
            input_tokens=100,
        )


def test_stage_scores_once_then_repicks_for_free(store: StoryStore) -> None:
    story = make_story()
    store.save_comments(story.id, make_comments())
    scorer = FakeScorer()

    first = pick_by_score(story, store, scorer, Curation())
    assert first.status is StageStatus.DONE
    assert sorted(scorer.scored) == [11, 12, 21, 31]

    scorer.scored.clear()
    again = pick_by_score(story, store, scorer, Curation())
    assert again.status is StageStatus.CACHED and scorer.scored == []

    retuned = pick_by_score(story, store, scorer, Curation(max_picks=1))
    assert retuned.status is StageStatus.DONE and scorer.scored == []  # new picks, no new scoring
    assert len(store.load_picks(story.id, "jev").picks) == 1  # type: ignore[union-attr]
    assert store.load_picks(story.id, "llm") is None  # kept separate from the Claude picks


def test_stage_skips_threads_with_too_few_comments(store: StoryStore) -> None:
    story = make_story()
    store.save_comments(story.id, make_comments()[:1])
    assert pick_by_score(story, store, FakeScorer(), Curation()).status is StageStatus.SKIPPED


def test_candidates_and_picks_carry_their_rank() -> None:
    candidates = candidates_from(make_comments())  # 11, 12 (reply to 11), 21, 31
    assert [(c.comment.id, c.rank, c.reply_rank) for c in candidates] == [
        (11, 1, None),
        (12, 1, 1),
        (21, 2, None),
        (31, 3, None),
    ]
    strong = signals(first_hand=1, substance=1)
    picks = select_picks(candidates, score_set(c12=strong, c31=strong), Curation())
    ranks = {p.comment_id: (p.rank, p.reply_rank) for p in picks}
    assert ranks == {12: (1, 1), 31: (3, None)}
