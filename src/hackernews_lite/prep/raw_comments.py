"""The top comments as they are, in Hacker News's order, when no picker has chosen any."""

from hackernews_lite.domain.models import Comment, CommentPick, CommentPicks
from hackernews_lite.domain.text import make_speakable, trim_to_words

RAW_COMMENTS = 8
RAW_WORDS = 110


def raw_ranked_picks(comments: list[Comment]) -> CommentPicks:
    """The top comments in Hacker News's order, trimmed, with nothing chosen or rewritten."""
    picks = [
        CommentPick(
            comment_id=comment.id,
            author=comment.author,
            intro="A top comment says",
            spoken_text=make_speakable(trim_to_words(comment.text, RAW_WORDS)),
            reason=f"Ranked #{rank} by Hacker News.",
            rank=rank,
        )
        for rank, comment in enumerate(comments[:RAW_COMMENTS], start=1)
    ]
    return CommentPicks(method="raw", prompt_version=0, model="none", input_hash="", picks=picks)
