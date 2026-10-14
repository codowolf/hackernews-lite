"""The local web app: a JSON API over the saved story files, plus the static player."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hackernews_lite.config import Feed, Settings
from hackernews_lite.domain.errors import SourceError, SpeechError
from hackernews_lite.domain.models import (
    ArticlePrep,
    ArticleStatus,
    Comment,
    CommentPick,
    CommentPicks,
    CommentStance,
    Link,
    Segment,
    SegmentKind,
    Story,
)
from hackernews_lite.domain.text import is_excerpt, short_link, strip_markers, word_count
from hackernews_lite.domain.threads import thread_slice
from hackernews_lite.domain.trending import heat
from hackernews_lite.feeds import FeedService, UnknownFeedError
from hackernews_lite.prep.raw_comments import raw_ranked_picks
from hackernews_lite.prep.segments import build_segments
from hackernews_lite.speech.audio_cache import AudioCache
from hackernews_lite.storage.story_store import StoryStore
from hackernews_lite.storage.user_state import STATES, StoryState, UserState
from hackernews_lite.web.jobs import JobMode, JobRunner, JobState

STATIC_DIR = Path(__file__).parent / "static"
# "no-cache" still lets the browser keep a copy, but it must ask the server first (a cheap 304 if
# nothing changed). Without it a browser can pair an old index.html with a new app.js and break.
REVALIDATE = {"Cache-Control": "no-cache"}
THUMBNAILS_PER_REQUEST = 40  # a long Daily list fetches pictures for its newest stories only


class RevalidatingStaticFiles(StaticFiles):
    """Static files browsers must re-check each time, so page, script and styles always match."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.update(REVALIDATE)
        return response


HN_ITEM = "https://news.ycombinator.com/item?id={id}"

Method = Literal["llm", "jev"]
METHODS: tuple[Method, ...] = ("llm", "jev")


class StorySummary(BaseModel):
    id: int
    title: str
    domain: str | None
    points: int
    num_comments: int
    created_at: datetime  # when it was posted on Hacker News
    hot_points: bool  # upvotes are climbing fast: the number is shown in orange
    hot_comments: bool  # a very busy discussion: the number is shown in orange
    thumb: bool  # has a preview image at /api/stories/{id}/thumb
    gist: str | None
    ready: bool  # has something to listen to
    picks: list[str]  # comment pickers that have results
    job: str | None = None  # "queued", "running" or "failed" while it is being prepared
    job_detail: str | None = None
    state: StoryState | None = None  # "archived" or "ignored", only seen in those two tabs
    day: str | None = None  # UTC date (YYYY-MM-DD) of a story in a grouped feed, else None


class FeedInfo(BaseModel):
    id: str
    label: str
    title: str
    grouped: bool = False  # the list reads as one group per day; the app shows day headings


def _feed_info(feed: Feed) -> FeedInfo:
    return FeedInfo(id=feed.id, label=feed.label, title=feed.title, grouped=feed.grouped)


# Two built-in lists beside the configured feeds: what the reader has put aside.
LIBRARY: dict[StoryState, FeedInfo] = {
    "archived": FeedInfo(id="archived", label="Archived", title="Archived stories"),
    "ignored": FeedInfo(id="ignored", label="Ignored", title="Ignored stories"),
}


class StoryList(BaseModel):
    feed: FeedInfo
    updated_at: datetime
    stories: list[StorySummary]
    pending_thumbs: bool  # some preview images are still being fetched; ask again shortly
    counts: dict[str, int] = {}  # how many stories are archived and ignored, for the tab badges
    mode: str | None = None  # the Daily list's mode (top10, top20, ...)
    has_more_days: bool = False  # older days can be asked for (the Daily list)
    day_pools: dict[str, int] = {}  # per day: how many stories reached the minimum points


class StateView(BaseModel):
    story_id: int
    state: StoryState | None  # None once restored


class JobView(BaseModel):
    story_id: int
    state: str
    detail: str


class ReactionView(BaseModel):
    headline: str
    top: dict[str, int]  # agree, disagree, mixed, neutral among the highest-ranked comments
    top_total: int
    all: dict[str, int]
    all_total: int
    thread_total: int  # how many top-level comments the thread had when it was analyzed


class DebateView(BaseModel):
    topic: str
    side_a: str
    side_b: str
    verdict: str
    leans: str
    confidence: str
    authors: list[str]
    link: str


class LinkView(BaseModel):
    label: str  # a short form of the address, for a small button
    href: str  # the full address


class FullCommentView(BaseModel):
    """A card shows an excerpt of a long comment; this is the rest, for reading in place."""

    text: str
    words: int
    links: list[Link] = []


class SegmentView(BaseModel):
    order: int
    kind: SegmentKind
    text: str  # exactly what is spoken
    display: str  # what is shown on screen: the clean text, without spoken scaffolding
    bullets: list[str] = []  # key points, shown as a list
    intro: str | None = None  # a comment's lead-in, shown above the comment
    author: str | None = None
    reason: str | None = None
    link: str | None = None  # the comment on Hacker News
    comment_id: int | None = None  # the comment this card shows (a debate's thread for a debate)
    links: list[LinkView] = []  # the links in the comment, which the shown excerpt may have cut
    full: FullCommentView | None = None  # only when `display` is a shortened part of the comment
    rank: int | None = None  # its place in Hacker News's ranking (comment votes are not public)
    reply_rank: int | None = None  # for a reply: its place among the thread's direct replies
    thread_total: int | None = None  # how many top-level comments the ranking covers
    # Highlights: model-written fields (display, bullets, reaction, debate) carry **markers**;
    # a verbatim quote lists the exact phrases of `display` to show in bold instead.
    emphasis: list[str] = []
    reaction: ReactionView | None = None
    debate: DebateView | None = None


class ThreadNodeView(BaseModel):
    id: int
    author: str
    text: str
    depth: int
    relation: Literal["target", "ancestor", "reply", "other"]
    link: str  # the comment on Hacker News
    links: list[Link] = []  # what each address in `text` really points to


class ThreadView(BaseModel):
    story_id: int
    comment_id: int  # the comment that was asked about, which the page highlights
    rank: int  # where the thread sits in Hacker News's ranking
    thread_total: int
    around: bool  # no replies: shown among the comments around it
    nodes: list[ThreadNodeView]


class StoryDetail(BaseModel):
    id: int
    title: str
    url: str | None
    hn_url: str
    article_note: str | None
    thumb: bool
    audio_bytes: int  # disk used by this story's saved voice clips (0 until someone listens)
    analyzed_at: datetime | None = None  # when the summary, picks and reaction were made
    analyzed_comments: int = 0  # how many comments (replies included) that analysis read
    current_comments: int = 0  # how many the story has now, as last seen
    ready: bool = False  # has a summary or comment picks to read and listen to
    depth: Literal["quick", "deep"] | None = None  # how the thread's reaction was made
    picks: list[str] = []  # the comment pickers that have results for this story
    job: str | None = None  # "queued", "running" or "failed" while it is being prepared
    job_detail: str | None = None
    segments: list[SegmentView]


class AudioCleared(BaseModel):
    freed_bytes: int


class DayModeInfo(BaseModel):
    id: str
    label: str


class DailyInfo(BaseModel):
    modes: list[DayModeInfo]
    default_mode: str
    page_days: int
    max_days: int
    min_points: int


class AppConfig(BaseModel):
    voices: list[str]
    default_voice: str
    picks: list[str]
    feeds: list[FeedInfo]
    library: list[FeedInfo]  # the Archived and Ignored tabs
    default_feed: str
    can_prepare: bool
    prepare_on_open: bool  # opening an unanalyzed story starts its analysis
    daily: DailyInfo


def create_app(
    *,
    settings: Settings,
    store: StoryStore,
    audio: AudioCache,
    feeds: FeedService,
    jobs: JobRunner | None = None,
    on_new_stories: Callable[[list[Story]], None] | None = None,
    user_state: UserState | None = None,
) -> FastAPI:
    app = FastAPI(title="Hackernews Lite")
    state = user_state or UserState(settings.paths.data_dir / "user_state.json")

    def load_story(story_id: int) -> Story:
        story = store.load_story(story_id)
        if story is None:
            raise HTTPException(404, "Unknown story")
        return story

    def picks_for(story_id: int, method: Method) -> CommentPicks | None:
        saved = store.load_picks(story_id, method)
        if saved is not None:
            return saved
        # No picks of this kind (not analyzed yet, or no Jev key): show the top comments as they
        # are, so there is always something to read.
        comments = store.load_comments(story_id)
        return raw_ranked_picks(comments) if comments else None

    def segments_for(story: Story, picks: Method) -> list[Segment]:
        return build_segments(
            story,
            store.load_prep(story.id),
            picks_for(story.id, picks),
            store.load_stance(story.id),
        )

    @app.get("/api/config")
    def get_config() -> AppConfig:
        return AppConfig(
            voices=audio.voices,
            default_voice=settings.speech.voice,
            picks=list(METHODS),
            feeds=[_feed_info(f) for f in settings.feeds.items],
            library=[LIBRARY[s] for s in STATES],
            default_feed=settings.feeds.default,
            can_prepare=jobs is not None,
            prepare_on_open=jobs is not None and settings.prepare.on_open,
            daily=DailyInfo(
                modes=[DayModeInfo(id=m.id, label=m.label) for m in settings.daily.modes],
                default_mode=settings.daily.default_mode,
                page_days=settings.daily.page_days,
                max_days=settings.daily.max_days,
                min_points=settings.daily.min_points,
            ),
        )

    def _story_list(
        info: FeedInfo, updated_at: datetime, summaries: list[StorySummary]
    ) -> StoryList:
        return StoryList(
            feed=info,
            updated_at=updated_at,
            stories=summaries,
            pending_thumbs=any(store.load_thumbnail(s.id) is None for s in summaries),
            counts=dict(state.counts()),
        )

    def _library_list(which: StoryState) -> StoryList:
        """Archived or ignored stories, most recently put aside first."""
        summaries = [
            summary for story_id in state.ids(which) if (summary := _summary(story_id)) is not None
        ]
        return _story_list(LIBRARY[which], datetime.now(UTC), summaries)

    @app.get("/api/stories")
    def list_stories(
        feed: str | None = None, mode: str | None = None, days: int | None = None
    ) -> StoryList:
        feed_id = feed or settings.feeds.default
        library = next((s for s in STATES if s == feed_id), None)
        if library is not None:
            return _library_list(library)
        try:
            definition = feeds.definition(feed_id)
        except UnknownFeedError as exc:
            raise HTTPException(404, str(exc)) from exc
        if definition.grouped:
            return _daily_list(
                definition, mode or settings.daily.default_mode, days or settings.daily.page_days
            )

        snapshot = feeds.snapshot(feed_id)
        if snapshot is None or feeds.is_stale(snapshot):
            try:
                fresh = feeds.refresh(feed_id)
            except SourceError as exc:
                if snapshot is None:  # nothing saved to fall back on
                    raise HTTPException(503, f"Could not load {definition.title}: {exc}") from exc
            else:
                if on_new_stories:
                    on_new_stories(fresh)
                snapshot = feeds.snapshot(feed_id)
        assert snapshot is not None

        summaries = [
            summary
            for story_id in snapshot.story_ids
            if state.state_of(story_id) is None  # archived and ignored stories stay out of feeds
            and (summary := _summary(story_id)) is not None
        ]
        _prepare_ahead(summaries)
        return _story_list(_feed_info(definition), snapshot.fetched_at, summaries)

    def _daily_list(feed: Feed, mode_id: str, days: int) -> StoryList:
        """The Daily list: each day on its own, showing what the chosen mode selects."""
        try:
            daily = feeds.daily(mode_id, days)
        except UnknownFeedError as exc:
            raise HTTPException(404, str(exc)) from exc
        except SourceError as exc:
            raise HTTPException(503, f"Could not load {feed.title}: {exc}") from exc

        summaries: list[StorySummary] = []
        for group in daily.groups:
            for story in group.stories:
                if state.state_of(story.id) is not None:
                    continue  # archived and ignored stories stay out of the lists
                if (summary := _summary(story.id)) is not None:
                    summary.day = group.day.isoformat()
                    summaries.append(summary)
        if on_new_stories:  # preview images for the newest stories only, to stay polite to sites
            on_new_stories([s for g in daily.groups for s in g.stories][:THUMBNAILS_PER_REQUEST])
        _prepare_ahead(summaries)
        listing = _story_list(_feed_info(feed), daily.updated_at, summaries)
        listing.mode = mode_id
        listing.has_more_days = daily.has_more
        listing.day_pools = {group.day.isoformat(): group.pool for group in daily.groups}
        return listing

    def _prepare_ahead(summaries: list[StorySummary]) -> None:
        """Get the first stories of a list ready in the background, behind anything you open."""
        if jobs is None or settings.prepare.list_top <= 0:
            return
        for summary in summaries[: settings.prepare.list_top]:
            if not summary.ready and summary.job is None:  # not analyzed, and nothing tried yet
                jobs.submit(summary.id, background=True)
                summary.job = "queued"

    def _active_job(story_id: int) -> tuple[str | None, str | None]:
        """The job state worth showing (queued, running or failed) and why it failed, if it did."""
        job = jobs.get(story_id) if jobs else None
        if job is None or job.state is JobState.DONE:
            return None, None
        return job.state.value, job.detail if job.state is JobState.FAILED else None

    def _summary(story_id: int) -> StorySummary | None:
        story = store.load_story(story_id)
        if story is None:
            return None
        prep = store.load_prep(story_id)
        available = [m for m in METHODS if store.load_picks(story_id, m)]
        hot = heat(story, settings.trending)
        job = jobs.get(story_id) if jobs else None
        running = job is not None and job.state in (
            JobState.QUEUED,
            JobState.RUNNING,
            JobState.FAILED,
        )
        return StorySummary(
            id=story.id,
            title=story.title,
            domain=_domain(story.url),
            points=story.points,
            num_comments=story.num_comments,
            created_at=story.created_at,
            hot_points=hot.points,
            hot_comments=hot.comments,
            thumb=store.thumbnail_path(story_id).exists(),
            gist=strip_markers(prep.gist) if prep else None,  # list cards show plain text
            ready=prep is not None or bool(available),
            picks=available,
            job=job.state.value if job and running else None,
            job_detail=job.detail if job and job.state is JobState.FAILED else None,
            state=state.state_of(story_id),
        )

    @app.post("/api/stories/{story_id}/archive")
    def archive_story(story_id: int) -> StateView:
        return _put_aside(story_id, "archived")

    @app.post("/api/stories/{story_id}/ignore")
    def ignore_story(story_id: int) -> StateView:
        return _put_aside(story_id, "ignored")

    @app.post("/api/stories/{story_id}/restore")
    def restore_story(story_id: int) -> StateView:
        load_story(story_id)
        state.restore(story_id)
        return StateView(story_id=story_id, state=None)

    def _put_aside(story_id: int, which: StoryState) -> StateView:
        load_story(story_id)
        state.mark(story_id, which)
        return StateView(story_id=story_id, state=which)

    @app.get("/api/feeds")
    def list_feeds() -> list[FeedInfo]:
        return [_feed_info(f) for f in settings.feeds.items]

    @app.post("/api/stories/{story_id}/prepare")
    def prepare_story(story_id: int, refresh: bool = False) -> JobView:
        if jobs is None:
            raise HTTPException(503, "Preparing stories is not available in this server")
        load_story(story_id)
        job = jobs.submit(story_id, mode=JobMode.REFRESH if refresh else JobMode.PREPARE)
        return JobView(story_id=job.story_id, state=job.state.value, detail=job.detail)

    @app.post("/api/stories/{story_id}/deepen")
    def deepen_story(story_id: int) -> JobView:
        """Ask for the deep read of the thread: Claude finds the debates and gives its take."""
        if jobs is None:
            raise HTTPException(503, "Deeper analysis is not available in this server")
        load_story(story_id)
        job = jobs.submit(story_id, mode=JobMode.DEEPEN)
        return JobView(story_id=job.story_id, state=job.state.value, detail=job.detail)

    @app.get("/api/jobs")
    def list_jobs() -> list[JobView]:
        return [
            JobView(story_id=j.story_id, state=j.state.value, detail=j.detail)
            for j in (jobs.all() if jobs else [])
        ]

    @app.get("/api/stories/{story_id}")
    def get_story(story_id: int, picks: Method = "llm") -> StoryDetail:
        story = load_story(story_id)
        prep = store.load_prep(story_id)
        pick_set = picks_for(story_id, picks)
        by_comment = {p.comment_id: p for p in pick_set.picks} if pick_set else {}
        stance = store.load_stance(story_id)
        comments = store.load_comments(story_id) or []
        by_id = {c.id: c for top in comments for c in top.walk()}
        views = [
            _view(s, prep, by_comment, stance, len(comments), by_id)
            for s in segments_for(story, picks)
        ]
        return StoryDetail(
            id=story.id,
            title=story.title,
            url=story.url,
            hn_url=story.hn_url,
            article_note=_article_note(store, story),
            thumb=store.thumbnail_path(story.id).exists(),
            audio_bytes=store.audio_bytes(story.id),
            analyzed_at=store.analysis_updated_at(story.id),
            analyzed_comments=sum(len(c.walk()) for c in comments),
            current_comments=story.num_comments,
            ready=prep is not None or any(store.load_picks(story_id, m) for m in METHODS),
            depth=stance.depth if stance else None,
            picks=[m for m in METHODS if store.load_picks(story_id, m)],
            job=_active_job(story_id)[0],
            job_detail=_active_job(story_id)[1],
            segments=views,
        )

    @app.delete("/api/stories/{story_id}/audio")
    def clear_story_audio(story_id: int) -> AudioCleared:
        """Free disk space. The clips are simply made again if the story is played later."""
        load_story(story_id)
        return AudioCleared(freed_bytes=store.clear_audio(story_id))

    @app.get("/api/stories/{story_id}/comments/{comment_id}")
    def get_comment_thread(story_id: int, comment_id: int) -> ThreadView:
        """The whole thread a comment belongs to, for reading it in context."""
        load_story(story_id)
        found = thread_slice(store.load_comments(story_id) or [], comment_id)
        if found is None:
            raise HTTPException(404, "That comment is not in the saved thread")
        return ThreadView(
            story_id=story_id,
            comment_id=comment_id,
            rank=found.rank,
            thread_total=found.total,
            around=found.around,
            nodes=[
                ThreadNodeView(
                    id=node.comment.id,
                    author=node.comment.author,
                    text=node.comment.text,
                    depth=node.depth,
                    relation=node.relation,
                    link=HN_ITEM.format(id=node.comment.id),
                    links=node.comment.links,
                )
                for node in found.nodes
            ],
        )

    @app.get("/api/stories/{story_id}/thumb", response_model=None)
    def get_thumb(story_id: int) -> FileResponse:
        path = store.thumbnail_path(story_id)
        if not path.exists():
            raise HTTPException(404, "No preview image")
        return FileResponse(
            path, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"}
        )

    @app.get("/api/audio/{story_id}/{order}", response_model=None)
    def get_audio(
        story_id: int,
        order: int,
        picks: Method = "llm",
        voice: Annotated[str | None, Query()] = None,
    ) -> FileResponse | JSONResponse:
        story = load_story(story_id)
        chosen = voice or settings.speech.voice
        if chosen not in audio.voices:
            raise HTTPException(400, "Unknown voice")
        # The text comes from the saved files, never from the browser.
        segment = next((s for s in segments_for(story, picks) if s.order == order), None)
        if segment is None:
            raise HTTPException(404, "Unknown segment")
        try:
            path = audio.audio_for(story_id, segment.text, chosen)
        except SpeechError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return FileResponse(path, media_type="audio/mpeg")

    app.mount("/static", RevalidatingStaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse(_versioned_index(), headers=REVALIDATE)

    return app


def _view(
    segment: Segment,
    prep: ArticlePrep | None,
    by_comment: dict[int, CommentPick],
    stance: CommentStance | None,
    thread_total: int,
    comments: dict[int, Comment],
) -> SegmentView:
    view = SegmentView(
        order=segment.order, kind=segment.kind, text=segment.text, display=segment.text
    )
    kind = segment.kind
    if kind is SegmentKind.GIST and prep:
        view.display = prep.gist
    elif kind is SegmentKind.KEY_POINTS and prep:
        view.display = ""
        view.bullets = prep.key_points
    elif kind is SegmentKind.REACTION and stance:
        view.display = stance.headline
        view.reaction = ReactionView(
            headline=stance.headline,
            top=stance.top_counts.model_dump(),
            top_total=stance.top_counts.total,
            all=stance.all_counts.model_dump(),
            all_total=stance.all_counts.total,
            thread_total=thread_total,
        )
    elif kind is SegmentKind.DEBATE and stance:
        debate = next((d for d in stance.debates if d.thread_id == segment.ref_comment_id), None)
        if debate:
            view.display = debate.topic
            view.comment_id = debate.thread_id
            view.debate = DebateView(
                topic=debate.topic,
                side_a=debate.side_a,
                side_b=debate.side_b,
                verdict=debate.verdict,
                leans=debate.leans,
                confidence=debate.confidence,
                authors=debate.authors,
                link=HN_ITEM.format(id=debate.thread_id),
            )
    else:
        pick = _pick_for(segment, by_comment, stance)
        if pick:
            view.display = pick.spoken_text
            view.emphasis = pick.emphasis
            view.intro = pick.intro
            view.author = pick.author
            view.reason = pick.reason
            view.link = HN_ITEM.format(id=pick.comment_id)
            view.comment_id = pick.comment_id
            comment = comments.get(pick.comment_id)
            if comment:
                view.links = [
                    LinkView(label=short_link(link.href), href=link.href) for link in comment.links
                ]
                if is_excerpt(pick.spoken_text, comment.text):
                    view.full = FullCommentView(
                        text=comment.text, words=word_count(comment.text), links=comment.links
                    )
            view.rank, view.reply_rank, view.thread_total = pick.rank, pick.reply_rank, thread_total
    return view


def _pick_for(
    segment: Segment, by_comment: dict[int, CommentPick], stance: CommentStance | None
) -> CommentPick | None:
    if segment.ref_comment_id is None:
        return None
    if segment.kind in (SegmentKind.AGREE, SegmentKind.DISAGREE) and stance:
        picks = stance.top_agree if segment.kind is SegmentKind.AGREE else stance.top_disagree
        return next((p for p in picks if p.comment_id == segment.ref_comment_id), None)
    return by_comment.get(segment.ref_comment_id)


def _versioned_index() -> str:
    """index.html with ?v=<last change> on the script and styles, so old copies are never reused."""
    version = max(int(path.stat().st_mtime) for path in STATIC_DIR.rglob("*") if path.is_file())
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for asset in ("/static/app.js", "/static/style.css"):
        html = html.replace(f'"{asset}"', f'"{asset}?v={version}"')
    return html


def _domain(url: str | None) -> str | None:
    return (urlparse(url).hostname or "").removeprefix("www.") or None if url else None


def _article_note(store: StoryStore, story: Story) -> str | None:
    article = store.load_article(story.id)
    if article is None:
        return None
    if article.status is ArticleStatus.DEFERRED:
        return f"No article summary: {article.reason} links are not supported yet."
    if article.status is ArticleStatus.FAILED:
        return "No article summary: the page could not be read."
    return None
