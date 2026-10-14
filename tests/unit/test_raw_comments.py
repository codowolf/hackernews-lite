from hackernews_lite.domain.models import Comment
from hackernews_lite.prep.raw_comments import RAW_COMMENTS, raw_ranked_picks


def test_top_comments_are_shown_as_they_are_in_hacker_news_order() -> None:
    comments = [
        Comment(id=i, author=f"u{i}", text=f"Comment number {i}. " * 3) for i in range(1, 4)
    ]
    picks = raw_ranked_picks(comments)

    assert picks.method == "raw"
    assert [p.comment_id for p in picks.picks] == [1, 2, 3]
    assert [p.rank for p in picks.picks] == [1, 2, 3]
    assert picks.picks[0].spoken_text.startswith("Comment number 1.")
    assert "Ranked #1" in picks.picks[0].reason and picks.picks[0].emphasis == []


def test_only_the_top_few_are_taken_and_long_ones_are_trimmed() -> None:
    long_text = "word " * 400
    comments = [Comment(id=i, author="a", text=long_text) for i in range(1, 30)]
    picks = raw_ranked_picks(comments)
    assert len(picks.picks) == RAW_COMMENTS
    assert len(picks.picks[0].spoken_text.split()) <= 111


def test_urls_are_not_left_in_what_would_be_spoken() -> None:
    picks = raw_ranked_picks([Comment(id=1, author="a", text="see https://example.com/x for more")])
    assert "https://" not in picks.picks[0].spoken_text
