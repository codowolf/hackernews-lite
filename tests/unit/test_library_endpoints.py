"""Archive, ignore and restore: what the reader puts aside, and the two tabs that list it."""

# The app fixtures live in test_web_app; importing them makes pytest see them here, which is
# exactly what ruff reports as an unused import and a redefinition.
# ruff: noqa: F401, F811

from fastapi.testclient import TestClient

from tests.unit.test_web_app import Rig, client, rig, tts


def ids(client: TestClient, feed: str) -> list[int]:
    return [s["id"] for s in client.get(f"/api/stories?feed={feed}").json()["stories"]]


def test_config_offers_the_archived_and_ignored_tabs(client: TestClient) -> None:
    library = client.get("/api/config").json()["library"]
    assert [(f["id"], f["label"]) for f in library] == [
        ("archived", "Archived"),
        ("ignored", "Ignored"),
    ]


def test_archiving_hides_a_story_from_the_feed_and_lists_it_under_archived(
    client: TestClient,
) -> None:
    assert client.post("/api/stories/1/archive").json() == {"story_id": 1, "state": "archived"}

    assert ids(client, "24h") == [2]
    archived = client.get("/api/stories?feed=archived").json()
    assert [s["id"] for s in archived["stories"]] == [1]
    assert archived["stories"][0]["state"] == "archived"
    assert archived["feed"]["title"] == "Archived stories"
    assert archived["counts"] == {"archived": 1, "ignored": 0}


def test_ignoring_hides_a_story_everywhere_but_the_ignored_tab(client: TestClient) -> None:
    client.post("/api/stories/2/ignore")

    assert ids(client, "24h") == [1]
    assert ids(client, "archived") == []
    assert ids(client, "ignored") == [2]
    assert client.get("/api/stories?feed=24h").json()["counts"] == {"archived": 0, "ignored": 1}


def test_restoring_brings_a_story_back_to_its_feed(client: TestClient) -> None:
    client.post("/api/stories/1/ignore")
    assert client.post("/api/stories/1/restore").json() == {"story_id": 1, "state": None}

    assert ids(client, "24h") == [2, 1]
    assert ids(client, "ignored") == []


def test_changing_your_mind_moves_a_story_between_the_tabs(client: TestClient) -> None:
    client.post("/api/stories/1/archive")
    client.post("/api/stories/1/ignore")
    assert ids(client, "archived") == [] and ids(client, "ignored") == [1]


def test_the_tabs_list_the_newest_choice_first(client: TestClient) -> None:
    client.post("/api/stories/1/archive")
    client.post("/api/stories/2/archive")
    assert ids(client, "archived") == [2, 1]


def test_unknown_stories_are_a_404_for_every_action(client: TestClient) -> None:
    for action in ("archive", "ignore", "restore"):
        assert client.post(f"/api/stories/999/{action}").status_code == 404


def test_every_feed_hides_what_was_put_aside(rig: Rig) -> None:
    rig.client.post("/api/stories/1/archive")
    for feed in ("top", "48h", "week", "best"):  # each is fetched fresh from the fake source
        assert 1 not in ids(rig.client, feed)
