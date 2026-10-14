from hackernews_lite.domain.text import html_to_text, is_faithful, make_speakable


def test_html_to_text_splits_paragraphs_and_unescapes() -> None:
    raw = 'First &amp; foremost.<p>Second <a href="https://x.io">link</a> here.<p>Third'
    assert html_to_text(raw) == "First & foremost.\n\nSecond link here.\n\nThird"


def test_html_to_text_handles_none_and_empty() -> None:
    assert html_to_text(None) == ""
    assert html_to_text("") == ""


def test_make_speakable_replaces_urls_and_strips_markdown() -> None:
    assert (
        make_speakable("See https://example.com/a?b=1 for **details**") == "See a link for details"
    )


def test_is_faithful_accepts_trimmed_text() -> None:
    original = "I ran this for two years. The upgrades were painful. Networking was worse."
    assert is_faithful("The upgrades were painful.", original)


def test_is_faithful_rejects_reworded_text() -> None:
    original = "I ran this for two years. The upgrades were painful."
    assert not is_faithful("This person thinks that kubernetes is a nightmare overall", original)


def test_is_faithful_rejects_empty() -> None:
    assert not is_faithful("", "anything")


def test_links_keep_their_full_address_though_hn_shows_them_cut_short() -> None:
    from hackernews_lite.domain.text import extract_links, short_link

    raw = (
        'See <a href="https://github.com/o/r/blob/master/doc/custom-build.md" rel="nofollow">'
        "https://github.com/o/r/blob/master/doc/custom-b...</a> and "
        '<a href="https://typeof.net/Iosevka/customizer?a=1&amp;b=2">https://typeof.net/x</a>'
        '<a href="javascript:alert(1)">bad</a><a name="x">no address</a>'
    )
    links = extract_links(raw)
    assert [(link.text, link.href) for link in links] == [
        (
            "https://github.com/o/r/blob/master/doc/custom-b...",
            "https://github.com/o/r/blob/master/doc/custom-build.md",
        ),
        ("https://typeof.net/x", "https://typeof.net/Iosevka/customizer?a=1&b=2"),
    ]  # only web addresses; nothing that could run code
    assert extract_links(None) == [] and extract_links("plain") == []
    assert short_link("https://www.example.com/") == "example.com"
    assert short_link("https://example.com/" + "a" * 60).endswith("…")


def test_a_clearly_shorter_part_counts_as_an_excerpt_but_small_changes_do_not() -> None:
    from hackernews_lite.domain.text import is_excerpt

    long = " ".join(f"word{i}" for i in range(300))
    assert is_excerpt(" ".join(long.split()[:108]), long)  # cut at the limit
    assert not is_excerpt(long, long)
    short = "I tried it. See https://example.com/some/page for more."
    assert not is_excerpt(make_speakable(short), short)  # a link became "a link"
    assert not is_excerpt(
        "One two three four five six seven eight.", "One two three four five six seven eight nine."
    )
