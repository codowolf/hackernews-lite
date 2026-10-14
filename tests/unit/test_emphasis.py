import pytest

from hackernews_lite.domain.text import (
    clean_markers,
    make_speakable,
    strip_markers,
    validate_emphasis,
)


class TestStripMarkers:
    def test_removes_markers_and_keeps_the_words(self) -> None:
        assert strip_markers("Saved **$672 million** in tax.") == "Saved $672 million in tax."

    def test_plain_text_is_untouched(self) -> None:
        assert strip_markers("Nothing here.") == "Nothing here."


class TestCleanMarkers:
    def test_keeps_a_few_short_phrases(self) -> None:
        text = (
            "Leaked files suggest Deloitte **backdated documents** to hide "
            "**$672 million** of assets from the tax authority in Cyprus."
        )
        assert clean_markers(text) == text

    def test_a_whole_sentence_is_not_a_highlight(self) -> None:
        text = "**Deloitte backdated documents to hide hundreds of millions of dollars in assets.**"
        assert clean_markers(text) == strip_markers(text)

    def test_a_long_phrase_is_unwrapped_but_short_ones_stay(self) -> None:
        text = (
            "**One two three four five six seven eight nine** and the **real point** "
            "and some extra words so the share stays low enough to keep it."
        )
        cleaned = clean_markers(text)
        assert "**real point**" in cleaned
        assert (
            "**One two" not in cleaned and "One two three four five six seven eight nine" in cleaned
        )

    def test_at_most_three_highlights_survive(self) -> None:
        text = (
            "**a b** c d e f g **h i** j k l m n "
            "**o p** q r s t u **v w** x y z and more words here."
        )
        assert clean_markers(text).count("**") == 6  # three spans, two markers each

    def test_unbalanced_markers_fall_back_to_plain_text(self) -> None:
        assert clean_markers("Half **open phrase and more") == "Half open phrase and more"
        assert clean_markers("A **fine one** but a stray ** here") == "A fine one but a stray  here"

    def test_mostly_bold_text_is_not_kept_bold(self) -> None:
        assert clean_markers("**all of it** **is bold**") == "all of it is bold"

    def test_empty_spans_are_ignored(self) -> None:
        assert (
            clean_markers("Nothing **** here and more words to read.")
            == "Nothing  here and more words to read."
        )

    def test_the_words_are_never_changed(self) -> None:
        text = (
            "We ran **Kubernetes for six years** and then left "
            "**for plain VMs** entirely last year."
        )
        assert strip_markers(clean_markers(text)) == strip_markers(text)


class TestValidateEmphasis:
    QUOTE = (
        "I ran this in production for two years "
        "and the upgrades were the real cost, not the compute."
    )

    def test_keeps_exact_phrases_in_reading_order(self) -> None:
        got = validate_emphasis(["the real cost", "in production"], self.QUOTE)
        assert got == ["in production", "the real cost"]

    @pytest.mark.parametrize(
        "phrase",
        [
            "In Production",  # not an exact match
            "production",  # one word is too little
            "made up words that never appear",
            "I ran this in production for two years and the upgrades",  # nine words
            "",
            "   ",
        ],
    )
    def test_rejects_invented_tiny_or_long_phrases(self, phrase: str) -> None:
        assert validate_emphasis([phrase], self.QUOTE) == []

    def test_overlapping_phrases_keep_only_the_first(self) -> None:
        got = validate_emphasis(
            ["in production for two years", "two years and the upgrades"], self.QUOTE
        )
        assert got == ["in production for two years"]

    def test_at_most_three(self) -> None:
        text = (
            "alpha beta gamma delta epsilon zeta eta theta iota kappa "
            "lambda mu nu xi omicron pi rho sigma tau"
        )
        phrases = ["alpha beta", "delta epsilon", "eta theta", "kappa lambda", "nu xi"]
        assert len(validate_emphasis(phrases, text)) == 3

    def test_a_quote_is_never_mostly_bold(self) -> None:
        text = "real cost matters most here"
        assert validate_emphasis(["real cost matters most here"], text) == []
        assert validate_emphasis(["real cost matters"], text) == []  # 3 of 5 words is too much


class TestSpeechNeverReadsMarkers:
    def test_asterisks_are_removed_for_the_voice(self) -> None:
        assert make_speakable("Saved **$672 million** in tax.") == "Saved $672 million in tax."
