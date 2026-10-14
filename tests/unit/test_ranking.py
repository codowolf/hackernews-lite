from hackernews_lite.domain.ranking import rank_of
from tests.conftest import make_comments


def test_a_top_level_comment_has_its_position_and_no_reply_rank() -> None:
    comments = make_comments()  # 11 (with reply 12), 21, 31
    assert rank_of(11, comments) == (1, None)
    assert rank_of(21, comments) == (2, None)
    assert rank_of(31, comments) == (3, None)


def test_a_reply_has_its_threads_rank_and_its_own_position() -> None:
    assert rank_of(12, make_comments()) == (1, 1)


def test_a_deeper_reply_counts_the_branch_it_sits_in() -> None:
    from hackernews_lite.domain.models import Comment

    deep = Comment(id=3, author="c", text="x", depth=2)
    second = Comment(id=2, author="b", text="x", depth=1, children=[deep])
    first = Comment(id=1, author="a", text="x", depth=1)
    top = Comment(id=10, author="t", text="x", children=[first, second])
    assert rank_of(3, [top]) == (1, 2)  # inside the thread's second direct reply


def test_a_comment_that_is_not_in_the_thread_has_no_rank() -> None:
    assert rank_of(999, make_comments()) == (None, None)
