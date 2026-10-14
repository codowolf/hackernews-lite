from pathlib import Path

from hackernews_lite.storage.user_state import UserState


def test_a_new_story_has_no_state(tmp_path: Path) -> None:
    state = UserState(tmp_path / "user_state.json")
    assert state.state_of(1) is None
    assert state.counts() == {"archived": 0, "ignored": 0}


def test_marking_moves_a_story_between_the_two_lists(tmp_path: Path) -> None:
    state = UserState(tmp_path / "user_state.json")
    state.mark(1, "archived")
    assert state.state_of(1) == "archived" and state.ids("archived") == [1]

    state.mark(1, "ignored")  # changing your mind moves it, it is never in both
    assert state.state_of(1) == "ignored"
    assert state.ids("archived") == [] and state.ids("ignored") == [1]


def test_restore_puts_a_story_back_and_is_harmless_if_it_was_not_marked(tmp_path: Path) -> None:
    state = UserState(tmp_path / "user_state.json")
    state.mark(1, "ignored")
    state.restore(1)
    state.restore(99)
    assert state.state_of(1) is None and state.counts() == {"archived": 0, "ignored": 0}


def test_most_recently_added_comes_first(tmp_path: Path) -> None:
    state = UserState(tmp_path / "user_state.json")
    for story_id in (3, 1, 2):
        state.mark(story_id, "archived")
    assert state.ids("archived") == [2, 1, 3]


def test_choices_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "data" / "user_state.json"
    first = UserState(path)
    first.mark(1, "archived")
    first.mark(2, "ignored")

    again = UserState(path)
    assert again.state_of(1) == "archived" and again.state_of(2) == "ignored"
    assert again.counts() == {"archived": 1, "ignored": 1}
    assert not list(path.parent.glob("*.tmp"))  # the atomic write left no temp file behind


def test_a_damaged_file_is_treated_as_empty_and_repaired_on_the_next_change(
    tmp_path: Path,
) -> None:
    path = tmp_path / "user_state.json"
    path.write_text("{ not json")
    state = UserState(path)
    assert state.counts() == {"archived": 0, "ignored": 0}
    state.mark(5, "archived")
    assert UserState(path).state_of(5) == "archived"
