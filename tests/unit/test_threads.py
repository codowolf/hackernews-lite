from hackernews_lite.domain.models import Comment
from hackernews_lite.domain.threads import thread_slice


def c(comment_id: int, *children: Comment) -> Comment:
    return Comment(
        id=comment_id, author=f"u{comment_id}", text=f"text {comment_id}", children=list(children)
    )


def tree() -> list[Comment]:
    return [
        c(
            1, c(2, c(4, c(6)), c(5)), c(3)
        ),  # a thread: 1 has replies 2 and 3; 2 has 4 and 5; 4 has 6
        c(10, c(11)),
    ]


def shape(comment_id: int) -> list[tuple[int, int, str]]:
    found = thread_slice(tree(), comment_id)
    assert found is not None
    return [(n.comment.id, n.depth, n.relation) for n in found.nodes]


def test_the_whole_thread_is_returned_depth_first_with_the_comment_marked() -> None:
    assert shape(4) == [
        (1, 0, "ancestor"),
        (2, 1, "ancestor"),
        (4, 2, "target"),
        (6, 3, "reply"),  # below the comment
        (5, 2, "other"),  # a sibling: context around it
        (3, 1, "other"),
    ]


def test_a_top_level_comment_is_its_own_target_and_all_below_are_replies() -> None:
    assert shape(1) == [
        (1, 0, "target"), (2, 1, "reply"), (4, 2, "reply"),
        (6, 3, "reply"), (5, 2, "reply"), (3, 1, "reply"),
    ]  # fmt: skip


def test_the_thread_says_where_it_sits_in_the_ranking() -> None:
    found = thread_slice(tree(), 11)
    assert found is not None and (found.rank, found.total) == (2, 2)
    assert [n.comment.id for n in found.nodes] == [10, 11]


def test_a_comment_that_is_not_there_gives_nothing() -> None:
    assert thread_slice(tree(), 999) is None
    assert thread_slice([], 1) is None


def lone_tree() -> list[Comment]:
    """Top-level comments 1..6; the second has a reply, the rest none."""
    return [c(1), c(2, c(20), c(21)), c(3), c(4), c(5), c(6)]


def test_a_comment_nobody_replied_to_is_shown_among_its_neighbours() -> None:
    found = thread_slice(lone_tree(), 4)
    assert found is not None and found.around
    assert [(n.comment.id, n.relation) for n in found.nodes] == [
        (1, "other"), (2, "other"), (20, "other"), (21, "other"), (3, "other"),
        (4, "target"), (5, "other"), (6, "other"),
    ]  # fmt: skip
    assert (found.rank, found.total) == (4, 6)


def test_a_comment_with_replies_is_not_padded_with_neighbours() -> None:
    found = thread_slice(lone_tree(), 2)
    assert found is not None and not found.around
    assert [n.comment.id for n in found.nodes] == [2, 20, 21]


def test_a_reply_without_replies_still_shows_its_own_thread_only() -> None:
    found = thread_slice(lone_tree(), 20)
    assert found is not None and not found.around
    assert [n.comment.id for n in found.nodes] == [2, 20, 21]


def test_neighbours_stop_at_the_size_budget() -> None:
    from hackernews_lite.domain.threads import AROUND_BUDGET

    many = [c(i) for i in range(1, 200)]
    found = thread_slice(many, 100)
    assert found is not None and len(found.nodes) == AROUND_BUDGET
    ids = [n.comment.id for n in found.nodes]
    assert ids == sorted(ids) and 100 in ids and abs(ids.index(100) - len(ids) / 2) <= 1
