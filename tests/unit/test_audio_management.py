"""Voice files are made only when someone listens, and can be cleared to free disk space."""

# ruff: noqa: F811, F401  (the shared fixtures are imported from test_web_app and used by name)

from fastapi.testclient import TestClient

from hackernews_lite.storage.story_store import StoryStore
from tests.unit.test_web_app import FakeTts, client, rig, tts


def test_storage_reports_and_clears_a_stories_clips(store: StoryStore) -> None:
    assert store.audio_bytes(1) == 0 and store.clear_audio(1) == 0  # nothing yet: not an error

    store.save_audio(1, "aaa", b"x" * 1000)
    store.save_audio(1, "bbb", b"y" * 500)
    store.save_audio(2, "ccc", b"z" * 300)  # another story's clip must be left alone

    assert store.audio_bytes(1) == 1500
    assert store.clear_audio(1) == 1500
    assert store.audio_bytes(1) == 0 and not store.audio_path(1, "aaa").exists()
    assert store.audio_bytes(2) == 300


def test_opening_a_story_makes_no_audio(client: TestClient, tts: FakeTts) -> None:
    detail = client.get("/api/stories/1").json()
    assert detail["audio_bytes"] == 0
    assert client.get("/api/stories?feed=24h").status_code == 200
    assert tts.calls == []  # text is the default; the voice is made only when a clip is requested


def test_listening_creates_clips_and_the_size_shows_up(client: TestClient, tts: FakeTts) -> None:
    client.get("/api/audio/1/0?voice=v1")
    client.get("/api/audio/1/1?voice=v1")
    assert len(tts.calls) == 2
    assert client.get("/api/stories/1").json()["audio_bytes"] == 2 * len(b"ID3-fake-mp3")


def test_clearing_removes_the_clips_and_they_are_made_again_on_demand(
    client: TestClient,
    tts: FakeTts,
) -> None:
    client.get("/api/audio/1/0?voice=v1")
    response = client.delete("/api/stories/1/audio")
    assert response.status_code == 200 and response.json() == {"freed_bytes": len(b"ID3-fake-mp3")}
    assert client.get("/api/stories/1").json()["audio_bytes"] == 0

    assert client.get("/api/audio/1/0?voice=v1").status_code == 200
    assert len(tts.calls) == 2  # the clip had to be made again


def test_clearing_a_story_with_no_clips_frees_nothing(client: TestClient) -> None:
    assert client.delete("/api/stories/1/audio").json() == {"freed_bytes": 0}


def test_clearing_an_unknown_story_is_a_404(client: TestClient) -> None:
    assert client.delete("/api/stories/999/audio").status_code == 404
