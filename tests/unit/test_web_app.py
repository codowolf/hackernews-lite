from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hackernews_lite.config import Settings
from hackernews_lite.domain.errors import SpeechError
from hackernews_lite.domain.models import (
    Article,
    ArticlePrep,
    ArticleStatus,
    CommentPick,
    CommentPicks,
    FeedSnapshot,
    Story,
)
from hackernews_lite.feeds import FeedService
from hackernews_lite.speech.audio_cache import AudioCache
from hackernews_lite.storage.story_store import StoryStore
from hackernews_lite.web.app import create_app
from hackernews_lite.web.jobs import JobMode, JobRunner
from tests.conftest import FakeHn, make_story


class FakeTts:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    def voices(self) -> list[str]:
        return ["v1", "v2"]

    def synthesize(self, text: str, voice: str) -> bytes:
        if self.fail:
            raise SpeechError("no model")
        self.calls.append((text, voice))
        return b"ID3-fake-mp3"

    def cost_usd(self, characters: int) -> float:
        return 0.0


@pytest.fixture
def tts() -> FakeTts:
    return FakeTts()


class Rig:
    """The app plus the fakes behind it, so tests can look at what the server did."""

    def __init__(self, client: TestClient, hn: FakeHn, store: StoryStore, jobs: JobRunner) -> None:
        self.client, self.hn, self.store, self.jobs = client, hn, store, jobs
        self.prepared: list[int] = []
        self.refreshed: list[int] = []
        self.deepened: list[int] = []
        self.new_stories: list[int] = []


@pytest.fixture
def rig(settings: Settings, store: StoryStore, tts: FakeTts) -> Rig:
    story = make_story(1, points=500)
    store.save_story(story)
    store.save_article(story.id, Article(status=ArticleStatus.OK, text="x", word_count=1))
    store.save_prep(
        story.id,
        ArticlePrep(
            gist="A gist.", key_points=["One.", "Two."], model="m", prompt_version=1, input_hash="h"
        ),
    )
    pick = CommentPick(
        comment_id=11,
        author="bob",
        intro="A commenter says",
        spoken_text="upgrades hurt",
        reason="expert",
    )
    for method in ("llm", "jev"):
        store.save_picks(
            story.id,
            CommentPicks(method=method, prompt_version=1, model="m", input_hash="h", picks=[pick]),
        )
    unprepared = make_story(2, url="https://github.com/o/r", points=900)
    store.save_story(unprepared)
    store.save_article(
        unprepared.id, Article(status=ArticleStatus.DEFERRED, reason="code repository")
    )
    # A fresh saved feed, in the order Hacker News gave: 2 before 1.
    store.save_feed(FeedSnapshot(id="24h", fetched_at=datetime.now(UTC), story_ids=[2, 1]))

    hn = FakeHn([story, unprepared], [])
    holder: dict[str, Rig] = {}

    def prepare(story_id: int, mode: JobMode) -> None:
        holder["rig"].prepared.append(story_id)
        if mode is JobMode.REFRESH:
            holder["rig"].refreshed.append(story_id)
        if mode is JobMode.DEEPEN:
            holder["rig"].deepened.append(story_id)

    jobs = JobRunner(prepare)
    app = create_app(
        settings=settings,
        store=store,
        audio=AudioCache(store, tts),
        feeds=FeedService(hn=hn, store=store, feeds=settings.feeds, daily=settings.daily),
        jobs=jobs,
        on_new_stories=lambda stories: holder["rig"].new_stories.extend(s.id for s in stories),
    )
    holder["rig"] = Rig(TestClient(app), hn, store, jobs)
    return holder["rig"]


@pytest.fixture
def client(rig: Rig) -> TestClient:
    return rig.client


def test_stories_follow_the_feed_order_and_flag_what_is_ready(client: TestClient) -> None:
    body = client.get("/api/stories?feed=24h").json()
    assert body["feed"] == {
        "id": "24h",
        "label": "24h",
        "title": "Last 24 hours",
        "grouped": False,
    }
    stories = body["stories"]
    assert [s["id"] for s in stories] == [2, 1]  # Hacker News's order is kept, not "ready first"
    assert not stories[0]["ready"]
    assert stories[1]["ready"] and stories[1]["picks"] == ["llm", "jev"]
    assert stories[1]["gist"] == "A gist." and stories[1]["domain"] == "example.com"
    assert (
        stories[1]["created_at"].startswith("2026-10-12")
        and not stories[1]["hot_points"]
        and not stories[1]["hot_comments"]
    )


def test_story_detail_has_ordered_segments_with_comment_metadata(client: TestClient) -> None:
    detail = client.get("/api/stories/1?picks=jev").json()
    assert [s["kind"] for s in detail["segments"]] == ["gist", "key_points", "comment"]
    gist, points, comment = detail["segments"]
    assert gist["display"] == "A gist."  # clean text, not the spoken "title. gist"
    assert points["bullets"] == ["One.", "Two."] and points["display"] == ""
    assert points["text"].startswith("Key points. First,")  # spoken script keeps its scaffolding
    assert comment["display"] == "upgrades hurt" and comment["intro"] == "A commenter says"
    assert comment["author"] == "bob" and comment["reason"] == "expert"
    assert comment["link"].endswith("id=11")
    assert detail["hn_url"].endswith("id=1")


def test_without_comment_picks_the_top_comments_are_shown_as_they_are(
    rig: Rig, store: StoryStore
) -> None:
    from tests.conftest import make_comments

    story = make_story(3)
    store.save_story(story)
    store.save_comments(story.id, make_comments())
    store.save_prep(
        story.id,
        ArticlePrep(gist="G.", key_points=["One."], model="m", prompt_version=1, input_hash="h"),
    )  # summarized, but no Jev key means no picks
    kinds_and_authors = [
        (s["kind"], s.get("author"))
        for s in rig.client.get("/api/stories/3?picks=jev").json()["segments"]
    ]
    assert kinds_and_authors[:2] == [("gist", None), ("key_points", None)]
    assert [a for k, a in kinds_and_authors if k == "comment"] == ["bob", "dave", "erin"]


def test_deferred_story_explains_why_it_has_no_summary(client: TestClient) -> None:
    detail = client.get("/api/stories/2").json()
    assert detail["segments"] == []
    assert "code repository" in detail["article_note"]


def test_audio_is_made_once_then_served_from_cache(client: TestClient, tts: FakeTts) -> None:
    first = client.get("/api/audio/1/0?picks=llm&voice=v1")
    second = client.get("/api/audio/1/0?picks=llm&voice=v1")
    assert first.status_code == 200 and first.headers["content-type"] == "audio/mpeg"
    assert first.content == second.content == b"ID3-fake-mp3"
    assert len(tts.calls) == 1


def test_a_different_voice_makes_a_new_clip(client: TestClient, tts: FakeTts) -> None:
    client.get("/api/audio/1/0?voice=v1")
    client.get("/api/audio/1/0?voice=v2")
    assert [voice for _, voice in tts.calls] == ["v1", "v2"]


def test_unknown_voice_story_or_segment_are_rejected(client: TestClient) -> None:
    assert client.get("/api/audio/1/0?voice=nope").status_code == 400
    assert client.get("/api/audio/999/0?voice=v1").status_code == 404
    assert client.get("/api/audio/1/99?voice=v1").status_code == 404
    assert client.get("/api/stories/999").status_code == 404


def test_tts_failure_is_reported_not_crashed(client: TestClient, tts: FakeTts) -> None:
    tts.fail = True
    response = client.get("/api/audio/1/0?voice=v1")
    assert response.status_code == 503 and "no model" in response.json()["error"]


def test_index_and_static_files_are_served(client: TestClient) -> None:
    assert "Hackernews Lite" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert Path(__file__)  # keep the import used


def test_reaction_and_debate_cards_carry_their_own_data(
    client: TestClient, store: StoryStore
) -> None:
    from hackernews_lite.domain.models import CommentStance, Debate, StanceCounts

    agree = CommentPick(
        comment_id=31, author="erin", intro="A reader says", spoken_text="I agree", reason="clear"
    )
    store.save_stance(
        1,
        CommentStance(
            model="m",
            prompt_version=1,
            input_hash="h",
            headline="Mostly agree.",
            top_counts=StanceCounts(agree=3, disagree=1),
            all_counts=StanceCounts(agree=5, disagree=2),
            top_agree=[agree],
            top_disagree=[],
            debates=[
                Debate(
                    thread_id=11,
                    topic="Cost",
                    side_a="High.",
                    side_b="Low.",
                    verdict="High.",
                    leans="a",
                    confidence="medium",
                    authors=["bob", "carol"],
                )
            ],
        ),
    )
    segments = client.get("/api/stories/1?picks=llm").json()["segments"]
    by_kind = {s["kind"]: s for s in segments}

    assert [s["kind"] for s in segments][:5] == [
        "gist",
        "key_points",
        "reaction",
        "agree",
        "debate",
    ]
    assert by_kind["reaction"]["reaction"]["top"]["agree"] == 3
    assert by_kind["reaction"]["reaction"]["top_total"] == 4
    assert by_kind["agree"]["author"] == "erin" and by_kind["agree"]["display"] == "I agree"
    debate = by_kind["debate"]["debate"]
    assert debate["leans"] == "a" and debate["authors"] == ["bob", "carol"]
    assert debate["link"].endswith("id=11")
    assert (
        by_kind["comment"]["author"] == "bob"
    )  # the curated pick is still there, after the new ones


def thumb_flag(client: TestClient, story_id: int) -> bool:
    stories = {s["id"]: s for s in client.get("/api/stories?feed=24h").json()["stories"]}
    return bool(stories[story_id]["thumb"])


def test_thumbnail_endpoint_and_flag(client: TestClient, store: StoryStore) -> None:
    from hackernews_lite.domain.models import Thumbnail, ThumbnailStatus

    assert thumb_flag(client, 1) is False
    assert client.get("/api/stories/1/thumb").status_code == 404

    store.save_thumbnail(1, Thumbnail(status=ThumbnailStatus.OK), b"RIFF....WEBP")
    assert thumb_flag(client, 1) is True
    response = client.get("/api/stories/1/thumb")
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    assert "max-age" in response.headers["cache-control"]


# ---------- feeds and on-demand preparation ----------


def test_config_lists_the_feeds_and_whether_preparing_is_possible(client: TestClient) -> None:
    config = client.get("/api/config").json()
    assert [f["id"] for f in config["feeds"]] == ["daily", "top", "24h", "48h", "week", "best"]
    assert config["default_feed"] == "daily" and config["can_prepare"] is True


def test_a_feed_that_was_never_loaded_is_fetched_when_opened(rig: Rig) -> None:
    body = rig.client.get("/api/stories?feed=week").json()
    assert body["feed"]["title"] == "Last week"
    assert [call for call, _ in rig.hn.calls] == ["window:168h>=200"]
    assert sorted(rig.new_stories) == [1, 2]  # preview images are fetched for what was just loaded
    assert rig.store.load_feed("week") is not None


def test_each_feed_asks_hacker_news_the_right_question(rig: Rig) -> None:
    for feed in ("top", "48h", "best"):
        rig.client.get(f"/api/stories?feed={feed}")
    assert [call for call, _ in rig.hn.calls] == ["front_page", "window:48h>=100", "best"]


def test_a_fresh_feed_is_not_fetched_again(rig: Rig) -> None:
    rig.client.get("/api/stories?feed=24h")
    assert rig.hn.calls == []


def test_a_stale_feed_is_refreshed(rig: Rig) -> None:
    old = datetime.now(UTC) - timedelta(hours=3)
    rig.store.save_feed(FeedSnapshot(id="24h", fetched_at=old, story_ids=[1]))
    body = rig.client.get("/api/stories?feed=24h").json()
    assert [call for call, _ in rig.hn.calls] == ["window:24h>=50"]
    assert [s["id"] for s in body["stories"]] == [1, 2]  # the fake returns both, in its order


def test_unknown_feed_is_a_404(client: TestClient) -> None:
    assert client.get("/api/stories?feed=nope").status_code == 404


def test_if_hacker_news_is_down_the_saved_list_is_still_shown(rig: Rig) -> None:
    from hackernews_lite.domain.errors import SourceError

    def fail(**_: object) -> list[object]:
        raise SourceError("offline")

    rig.store.save_feed(
        FeedSnapshot(id="24h", fetched_at=datetime.now(UTC) - timedelta(hours=3), story_ids=[1])
    )
    rig.hn.top_stories = fail  # type: ignore[method-assign,assignment]
    body = rig.client.get("/api/stories?feed=24h").json()
    assert [s["id"] for s in body["stories"]] == [1]
    rig.hn.top_stories = fail  # type: ignore[method-assign,assignment]
    assert rig.client.get("/api/stories?feed=week").status_code == 503  # nothing saved to show


def test_preparing_a_story_runs_the_job_and_reports_it(rig: Rig) -> None:
    first = rig.client.post("/api/stories/2/prepare").json()
    assert first["story_id"] == 2 and first["state"] in ("queued", "running", "done")

    deadline = datetime.now(UTC) + timedelta(seconds=5)
    while datetime.now(UTC) < deadline and rig.jobs.get(2).state.value != "done":  # type: ignore[union-attr]
        pass
    assert rig.prepared == [2]
    assert [j["state"] for j in rig.client.get("/api/jobs").json()] == ["done"]


def test_preparing_an_unknown_story_is_a_404(client: TestClient) -> None:
    assert client.post("/api/stories/999/prepare").status_code == 404


def day_rig(rig: Rig) -> Rig:
    """Two days of stories on the fake Hacker News: today has 3, yesterday has 2."""
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)

    def on(day: date, ids: list[int], points: list[int]) -> list[Story]:
        at = datetime(day.year, day.month, day.day, 8, tzinfo=UTC)
        return [
            make_story(i, points=p, created_at=at + timedelta(minutes=i))
            for i, p in zip(ids, points, strict=True)
        ]

    rig.hn.by_day = {
        today: on(today, [11, 12, 13], [500, 300, 100]),
        yesterday: on(yesterday, [21, 22], [800, 90]),
    }
    return rig


def test_the_daily_list_groups_stories_by_day_newest_first(rig: Rig) -> None:
    day_rig(rig)
    body = rig.client.get("/api/stories?feed=daily&mode=top10&days=2").json()

    assert body["feed"] == {
        "id": "daily",
        "label": "Daily",
        "title": "Best of each day",
        "grouped": True,
    }
    assert body["mode"] == "top10" and body["has_more_days"] is True
    assert [s["id"] for s in body["stories"]] == [
        11,
        12,
        13,
        21,
        22,
    ]  # today first, best first within a day
    today, yesterday = body["stories"][0]["day"], body["stories"][3]["day"]
    assert today > yesterday
    assert body["day_pools"] == {today: 3, yesterday: 2}


def test_the_mode_decides_how_much_of_each_day(rig: Rig) -> None:
    day_rig(rig)

    def shown(mode: str) -> list[int]:
        return [
            s["id"]
            for s in rig.client.get(f"/api/stories?feed=daily&mode={mode}&days=2").json()["stories"]
        ]

    for mode in ("top5", "top10", "top20"):  # every day here has fewer stories than the smallest
        assert shown(mode) == [11, 12, 13, 21, 22]


def test_the_daily_list_defaults_to_the_configured_mode_and_days(rig: Rig) -> None:
    day_rig(rig)
    body = rig.client.get("/api/stories").json()  # no feed given: Daily is the default
    assert body["feed"]["id"] == "daily" and body["mode"] == "top5"
    assert len(body["day_pools"]) == 4  # page_days days, whether or not they had stories


def test_archived_stories_stay_out_of_the_daily_list(rig: Rig) -> None:
    day_rig(rig)
    rig.client.get("/api/stories?feed=daily&days=2")  # loading the days is what saves the stories
    assert rig.client.post("/api/stories/11/ignore").status_code == 200
    ids = [s["id"] for s in rig.client.get("/api/stories?feed=daily&days=2").json()["stories"]]
    assert 11 not in ids and 12 in ids


def test_an_unknown_mode_is_a_404(rig: Rig) -> None:
    day_rig(rig)
    assert rig.client.get("/api/stories?feed=daily&mode=nope").status_code == 404


def test_the_daily_list_is_a_503_when_hacker_news_is_down_and_nothing_is_saved(rig: Rig) -> None:
    today = datetime.now(UTC).date()
    rig.hn.failing_days = {today - timedelta(days=n) for n in range(30)}
    assert rig.client.get("/api/stories?feed=daily").status_code == 503


def test_the_daily_list_asks_for_pictures_for_at_most_the_newest_stories(rig: Rig) -> None:
    today = datetime.now(UTC).date()
    days = [today - timedelta(days=n) for n in range(3)]
    rig.hn.by_day = {
        day: [
            make_story(
                1000 * n + i,
                points=100 + i,
                created_at=datetime(day.year, day.month, day.day, 1, tzinfo=UTC),
            )
            for i in range(20)
        ]
        for n, day in enumerate(days, start=1)
    }  # 3 days of 20 stories: 60 on screen
    rig.client.get("/api/stories?feed=daily&mode=top20&days=3")
    assert len(rig.new_stories) == 40


def test_config_tells_the_app_about_the_daily_modes(client: TestClient) -> None:
    daily = client.get("/api/config").json()["daily"]
    assert [m["id"] for m in daily["modes"]] == ["top5", "top10", "top20"]
    assert (daily["default_mode"], daily["page_days"], daily["max_days"], daily["min_points"]) == (
        "top5",
        4,
        30,
        30,
    )


def test_ordinary_feeds_have_no_day_headings(client: TestClient) -> None:
    body = client.get("/api/stories?feed=24h").json()
    assert body["feed"]["grouped"] is False and body["mode"] is None
    assert all(s["day"] is None for s in body["stories"])
    assert [f["grouped"] for f in client.get("/api/config").json()["feeds"]] == [
        True,
        False,
        False,
        False,
        False,
        False,
    ]


def test_the_page_and_its_assets_must_be_revalidated_so_versions_never_mix(
    client: TestClient,
) -> None:
    for path in ("/", "/static/app.js", "/static/style.css", "/static/index.html"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path
    again = client.get(
        "/static/app.js", headers={"If-None-Match": client.get("/static/app.js").headers["etag"]}
    )
    assert again.status_code == 304  # nothing re-downloaded when nothing changed


def test_the_page_points_at_versioned_script_and_styles(client: TestClient) -> None:
    html = client.get("/").text
    assert 'src="/static/app.js?v=' in html and 'href="/static/style.css?v=' in html
    assert "Hackernews Lite" in html


# ---------- rank, and how fresh the analysis is ----------


def test_comments_show_their_rank_and_the_size_of_the_thread(rig: Rig) -> None:
    from tests.conftest import make_comments

    rig.store.save_comments(1, make_comments())  # three top-level comments
    pick = CommentPick(
        comment_id=21,
        author="dave",
        intro="Someone says",
        spoken_text="not great",
        reason="r",
        rank=2,
    )
    rig.store.save_picks(
        1, CommentPicks(method="llm", prompt_version=1, model="m", input_hash="h", picks=[pick])
    )
    segments = rig.client.get("/api/stories/1?picks=llm").json()["segments"]
    comment = next(s for s in segments if s["kind"] == "comment")
    assert (comment["rank"], comment["reply_rank"], comment["thread_total"]) == (2, None, 3)


def test_the_reaction_card_says_how_many_comments_the_thread_had(rig: Rig) -> None:
    from hackernews_lite.domain.models import CommentStance, StanceCounts
    from tests.conftest import make_comments

    rig.store.save_comments(1, make_comments())
    rig.store.save_stance(
        1,
        CommentStance(
            model="m",
            prompt_version=1,
            input_hash="h",
            headline="Mixed.",
            top_counts=StanceCounts(agree=1, disagree=1),
            all_counts=StanceCounts(agree=2, disagree=1),
            top_agree=[],
            top_disagree=[],
            debates=[],
        ),
    )
    reaction = next(
        s for s in rig.client.get("/api/stories/1").json()["segments"] if s["kind"] == "reaction"
    )
    assert reaction["reaction"]["thread_total"] == 3 and reaction["reaction"]["top_total"] == 2


def test_the_story_says_when_it_was_analyzed_and_how_much_it_read(rig: Rig) -> None:
    from tests.conftest import make_comments

    rig.store.save_comments(1, make_comments())  # 3 top-level plus 1 reply = 4 comments
    detail = rig.client.get("/api/stories/1").json()
    assert detail["analyzed_comments"] == 4
    assert detail["current_comments"] == 5  # what make_story says the story has now
    assert detail["analyzed_at"] is not None


def test_a_story_that_was_never_analyzed_has_no_analysis_time(rig: Rig) -> None:
    assert rig.client.get("/api/stories/2").json()["analyzed_at"] is None


def test_refreshing_an_analysis_asks_the_job_to_redo_everything(rig: Rig) -> None:
    rig.client.post("/api/stories/2/prepare")
    rig.client.post("/api/stories/1/prepare?refresh=true")
    deadline = datetime.now(UTC) + timedelta(seconds=5)
    while datetime.now(UTC) < deadline and len(rig.prepared) < 2:
        pass
    assert rig.refreshed == [1]  # only the refresh request was forced


def test_the_story_page_says_whether_it_is_ready_and_whether_it_is_being_prepared(rig: Rig) -> None:
    prepared = rig.client.get("/api/stories/1").json()
    assert prepared["ready"] is True and prepared["job"] is None

    idle = rig.client.get("/api/stories/2").json()  # a repo link with no analysis
    assert idle["ready"] is False and idle["job"] is None and idle["segments"] == []

    rig.client.post("/api/stories/2/prepare")
    busy = rig.client.get("/api/stories/2").json()
    assert busy["job"] in ("queued", "running", None)  # None if the fake job already finished


# ---------- reading never waits on a button ----------


def test_an_unanalyzed_story_shows_its_top_comments_straight_away(rig: Rig) -> None:
    from tests.conftest import make_comments

    rig.store.save_comments(2, make_comments())  # ingest has run, nothing else has
    detail = rig.client.get("/api/stories/2").json()
    assert detail["ready"] is False  # still waiting for the real analysis...
    kinds = [s["kind"] for s in detail["segments"]]
    assert kinds == ["comment"] * 3  # ...but there is already something to read
    first = detail["segments"][0]
    assert first["rank"] == 1 and "Ranked #1" in first["reason"] and first["display"]


def test_those_raw_comments_can_be_listened_to_too(rig: Rig) -> None:
    from tests.conftest import make_comments

    rig.store.save_comments(2, make_comments())
    assert rig.client.get("/api/audio/2/0?voice=v1").status_code == 200


def test_raw_comments_disappear_once_the_story_is_analyzed(rig: Rig) -> None:
    from tests.conftest import make_comments

    rig.store.save_comments(1, make_comments())  # story 1 already has a summary and picks
    segments = rig.client.get("/api/stories/1?picks=llm").json()["segments"]
    assert all("Ranked #" not in (s["reason"] or "") for s in segments)


def test_a_story_with_no_saved_comments_has_nothing_yet(rig: Rig) -> None:
    assert rig.client.get("/api/stories/2").json()["segments"] == []


def test_opening_a_list_prepares_its_top_stories_in_the_background(rig: Rig) -> None:
    day_rig(rig)
    first = rig.client.get("/api/stories?feed=daily&mode=top20&days=2").json()["stories"]
    unanalyzed = [s for s in first if not s["ready"]]
    assert unanalyzed and all(s["job"] == "queued" for s in unanalyzed)  # all within the top 10

    deadline = datetime.now(UTC) + timedelta(seconds=5)
    while datetime.now(UTC) < deadline and len(rig.prepared) < len(unanalyzed):
        pass
    assert sorted(rig.prepared) == sorted(s["id"] for s in unanalyzed)


def test_background_preparation_stops_after_the_configured_number(
    settings: Settings, rig: Rig
) -> None:
    day_rig(rig)
    settings.prepare.list_top = 2
    stories = rig.client.get("/api/stories?feed=daily&mode=top20&days=2").json()["stories"]
    queued = [s["id"] for s in stories if s["job"] == "queued"]
    assert (
        len(queued) <= 2 and queued == [s["id"] for s in stories if not s["ready"]][: len(queued)]
    )


def test_background_preparation_can_be_switched_off(settings: Settings, rig: Rig) -> None:
    day_rig(rig)
    settings.prepare.list_top = 0
    stories = rig.client.get("/api/stories?feed=daily&mode=top20&days=2").json()["stories"]
    assert all(s["job"] is None for s in stories)


def test_config_says_whether_opening_a_story_prepares_it(client: TestClient) -> None:
    assert client.get("/api/config").json()["prepare_on_open"] is True


# ---------- the deep analysis is only made on request ----------


def test_the_story_says_how_its_reaction_was_made(rig: Rig) -> None:
    from hackernews_lite.domain.models import CommentStance, StanceCounts

    assert rig.client.get("/api/stories/1").json()["depth"] is None  # no reaction yet
    for depth in ("quick", "deep"):
        rig.store.save_stance(1, CommentStance(
            model="m", prompt_version=1, input_hash="h", headline="H.",
            top_counts=StanceCounts(agree=1), all_counts=StanceCounts(agree=1),
            top_agree=[], top_disagree=[], debates=[], depth=depth,
        ))  # fmt: skip
        assert rig.client.get("/api/stories/1").json()["depth"] == depth


def test_asking_for_the_deep_analysis_runs_a_deepen_job(rig: Rig) -> None:
    response = rig.client.post("/api/stories/1/deepen")
    assert response.status_code == 200 and response.json()["story_id"] == 1
    deadline = datetime.now(UTC) + timedelta(seconds=5)
    while datetime.now(UTC) < deadline and not rig.deepened:
        pass
    assert rig.deepened == [1] and rig.refreshed == []


def test_deepening_an_unknown_story_is_a_404(rig: Rig) -> None:
    assert rig.client.post("/api/stories/999/deepen").status_code == 404


def test_a_story_page_knows_its_own_pickers_without_the_list(client: TestClient) -> None:
    """Opening from a link must work for a story that is not in the list on screen."""
    assert client.get("/api/stories/1").json()["picks"] == ["llm", "jev"]
    assert client.get("/api/stories/2").json()["picks"] == []


# ---------- reading a comment in its thread ----------


def threaded(rig: Rig) -> None:
    from hackernews_lite.domain.models import Comment

    reply = Comment(id=102, author="dave", text="A deeper reply.", depth=2)
    mid = Comment(id=101, author="carol", text="A reply.", depth=1, children=[reply])
    top = Comment(id=100, author="alice", text="The top comment.", children=[mid])
    other = Comment(id=200, author="erin", text="Another thread.")
    rig.store.save_comments(1, [top, other])


def test_a_comment_opens_in_its_whole_thread_with_itself_marked(rig: Rig) -> None:
    threaded(rig)
    body = rig.client.get("/api/stories/1/comments/101").json()

    assert (body["rank"], body["thread_total"], body["comment_id"]) == (1, 2, 101)
    assert [(n["id"], n["depth"], n["relation"]) for n in body["nodes"]] == [
        (100, 0, "ancestor"),
        (101, 1, "target"),
        (102, 2, "reply"),
    ]
    assert body["nodes"][0]["author"] == "alice" and body["nodes"][0]["link"].endswith("id=100")


def test_an_unknown_comment_or_story_is_a_404(rig: Rig) -> None:
    threaded(rig)
    assert rig.client.get("/api/stories/1/comments/999").status_code == 404
    assert rig.client.get("/api/stories/999/comments/100").status_code == 404


def test_every_comment_card_says_which_comment_it_shows(rig: Rig) -> None:
    segments = rig.client.get("/api/stories/1?picks=llm").json()["segments"]
    comment = next(s for s in segments if s["kind"] == "comment")
    assert comment["comment_id"] == 11
    assert all(s["comment_id"] is None for s in segments if s["kind"] in ("gist", "key_points"))


def test_a_comment_without_replies_comes_with_the_comments_around_it(rig: Rig) -> None:
    threaded(rig)
    body = rig.client.get("/api/stories/1/comments/200").json()  # erin's has no replies

    assert body["around"] is True and body["rank"] == 2
    assert [(n["id"], n["relation"]) for n in body["nodes"]] == [
        (100, "other"),
        (101, "other"),
        (102, "other"),
        (200, "target"),
    ]
    assert rig.client.get("/api/stories/1/comments/100").json()["around"] is False


def test_the_thread_and_the_cards_keep_every_links_real_address(rig: Rig) -> None:
    from hackernews_lite.domain.models import Comment, Link

    shown = "https://github.com/be5invis/Iosevka/blob/master/doc/custom-b..."
    full = "https://github.com/be5invis/Iosevka/blob/master/doc/custom-build.md"
    rig.store.save_comments(
        1,
        [Comment(id=11, author="bob", text=f"See {shown}", links=[Link(text=shown, href=full)])],
    )

    node = rig.client.get("/api/stories/1/comments/11").json()["nodes"][0]
    assert node["links"] == [{"text": shown, "href": full}]

    segments = rig.client.get("/api/stories/1?picks=llm").json()["segments"]
    comment = next(s for s in segments if s["kind"] == "comment")
    assert comment["links"] == [{"label": "github.com/be5invis/Iosevka/blob/mast…", "href": full}]


def test_a_card_shows_the_rest_of_the_comment_only_when_it_was_shortened(rig: Rig) -> None:
    from hackernews_lite.domain.models import Comment

    def card() -> dict:  # type: ignore[type-arg]
        segments = rig.client.get("/api/stories/1?picks=llm").json()["segments"]
        return next(s for s in segments if s["kind"] == "comment")

    long = " ".join(f"word{i}" for i in range(200))
    rig.store.save_comments(1, [Comment(id=11, author="bob", text=f"upgrades hurt {long}")])
    assert card()["display"] == "upgrades hurt"
    assert card()["full"]["words"] == 202 and card()["full"]["text"].startswith("upgrades hurt")

    rig.store.save_comments(1, [Comment(id=11, author="bob", text="upgrades hurt")])
    assert card()["full"] is None  # nothing was left out
