"""Where a comment sits in Hacker News's ranking. Votes are hidden, so position is what we show."""

from hackernews_lite.domain.models import Comment


def rank_of(comment_id: int, comments: list[Comment]) -> tuple[int | None, int | None]:
    """(rank, reply_rank) for a comment, or (None, None) if it is not in the thread.

    `rank` is the 1-based position among the top-level comments, in Hacker News's order. For a reply
    it is the position of the thread it belongs to, and `reply_rank` is where that reply's branch
    sits among the thread's direct replies. A top-level comment has no `reply_rank`.
    """
    for rank, top in enumerate(comments, start=1):
        if top.id == comment_id:
            return rank, None
        for position, child in enumerate(top.children, start=1):
            if any(node.id == comment_id for node in child.walk()):
                return rank, position
    return None, None
