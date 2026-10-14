"""Command line entry point: `hackernews-lite run | show | thumbs | cost | serve`."""

import argparse
import sys
from pathlib import Path

from hackernews_lite.config import CurationMethod, Settings, load_settings
from hackernews_lite.domain.errors import HnListenerError
from hackernews_lite.feeds import FeedService
from hackernews_lite.llm.factory import build_llm
from hackernews_lite.pipeline import Pipeline
from hackernews_lite.prep.segments import build_segments
from hackernews_lite.sources.browser_fetcher import build_article_fetcher
from hackernews_lite.sources.hn_api import AlgoliaHn
from hackernews_lite.sources.images import HttpImageFetcher
from hackernews_lite.speech.kokoro import KokoroTts
from hackernews_lite.storage.cost_log import CostLog
from hackernews_lite.storage.story_store import StoryStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(PROJECT_ROOT / "config.toml")
    try:
        return int(args.handler(args, settings))
    except HnListenerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hackernews-lite")
    sub = parser.add_subparsers(required=True)

    run = sub.add_parser("run", help="fetch a feed's stories and prep them")
    run.add_argument("--feed", help="top, 24h, 48h, week or best (default from config)")
    run.add_argument("--top", type=int, help="number of stories (default from the feed)")
    run.add_argument("--story", type=int, action="append", help="run one story id (repeatable)")
    run.add_argument("--no-llm", action="store_true", help="fetch only; spend nothing")
    run.add_argument(
        "--picks",
        choices=["llm", "jev", "both"],
        help="which comment picker to run (default from config)",
    )
    run.add_argument("--force", action="store_true", help="redo stages even if cached")
    run.set_defaults(handler=_cmd_run)

    show = sub.add_parser("show", help="print what was prepared for a story")
    show.add_argument("story", type=int)
    show.add_argument("--picks", choices=["llm", "jev"], default="llm", help="which picks to show")
    show.set_defaults(handler=_cmd_show)

    thumbs = sub.add_parser("thumbs", help="fetch preview images for stories already saved")
    thumbs.add_argument("--force", action="store_true", help="redo images already saved")
    thumbs.set_defaults(handler=_cmd_thumbs)

    cost = sub.add_parser("cost", help="spend this month against the budget")
    cost.set_defaults(handler=_cmd_cost)

    serve = sub.add_parser("serve", help="start the web app")
    serve.add_argument("--port", type=int, default=8731)
    serve.add_argument("--lan", action="store_true", help="also allow phones on your wifi")
    serve.set_defaults(handler=_cmd_serve)

    return parser


def _cost_log(settings: Settings) -> CostLog:
    return CostLog(settings.paths.data_dir / "cost_log.jsonl", settings.budget.monthly_usd)


def _build_pipeline(
    settings: Settings, store: StoryStore, hn: AlgoliaHn, *, use_llm: bool, strict_jev: bool
) -> Pipeline:
    """Wire the stages together.

    The cheap Jev steps (comment picks and the quick reaction) need a Jev key. With `strict_jev` a
    missing key is an error; otherwise those steps are left out, with a notice, and nothing falls
    back to Claude quietly, because Claude is the expensive part.
    """
    log = _cost_log(settings)
    llm = build_llm(settings, log) if use_llm else None
    scorer = labeler = None
    wants_jev = "jev" in settings.curation.methods or settings.curation.stance == "quick"
    if use_llm and wants_jev:
        try:
            from hackernews_lite.scoring.factory import build_labeler, build_scorer

            scorer = build_scorer(settings, log) if "jev" in settings.curation.methods else None
            labeler = build_labeler(settings, log) if settings.curation.stance == "quick" else None
        except Exception as exc:  # no SDK, or TYPESAFE_API_KEY not set
            if strict_jev:
                raise HnListenerError(f"Jev is not available: {exc}") from exc
            print(f"  Jev steps are off (comment picks and the quick reaction): {exc}")
    return Pipeline(
        settings=settings,
        hn=hn,
        fetcher=build_article_fetcher(),
        llm=llm,
        store=store,
        scorer=scorer,
        labeler=labeler,
        images=HttpImageFetcher(),
    )


def _cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    store = StoryStore(settings.paths.data_dir)
    hn = AlgoliaHn()
    if args.picks:
        methods: list[CurationMethod] = ["llm", "jev"] if args.picks == "both" else [args.picks]
        settings = settings.model_copy(
            update={"curation": settings.curation.model_copy(update={"methods": methods})}
        )
    pipeline = _build_pipeline(settings, store, hn, use_llm=not args.no_llm, strict_jev=True)
    if args.story:
        stories = [hn.thread(story_id)[0] for story_id in args.story]
    else:
        feed_id = args.feed or settings.feeds.default
        stories = FeedService(
            hn=hn, store=store, feeds=settings.feeds, daily=settings.daily
        ).refresh(feed_id, limit=args.top)
    print(f"{len(stories)} stories")
    for story in stories:
        outcome = pipeline.run_story(story, use_llm=not args.no_llm, force=args.force)
        print(f"\n{story.id}  {story.points} pts  {story.title}")
        for name, result in outcome.stages.items():
            print(f"    {name:9} {result}")
    print(f"\nSpent this month: ${_cost_log(settings).month_total():.4f}")
    return 0


def _cmd_show(args: argparse.Namespace, settings: Settings) -> int:
    store = StoryStore(settings.paths.data_dir)
    story = store.load_story(args.story)
    if story is None:
        raise HnListenerError(f"Story {args.story} has not been fetched yet")
    prep, picks = store.load_prep(story.id), store.load_picks(story.id, args.picks)
    print(f"{story.title}\n{story.hn_url}\n")
    for segment in build_segments(story, prep, picks):
        print(f"[{segment.order}] {segment.kind.value}: {segment.text}\n")
    if picks:
        print("Why these comments:")
        for pick in picks.picks:
            print(f"  - {pick.author}: {pick.reason}")
    return 0


def _cmd_thumbs(args: argparse.Namespace, settings: Settings) -> int:
    from hackernews_lite.prep.thumbnail import prepare_thumbnail

    store = StoryStore(settings.paths.data_dir)
    pages, images = build_article_fetcher(), HttpImageFetcher()
    for story_id in store.story_ids():
        story = store.load_story(story_id)
        if story is None:
            continue
        result = prepare_thumbnail(story, store, pages, images, force=args.force)
        print(f"{story_id}  {result}  {story.title[:50]}")
    return 0


def _cmd_cost(args: argparse.Namespace, settings: Settings) -> int:
    log = _cost_log(settings)
    spent = log.month_total()
    print(f"This month: ${spent:.4f} of ${settings.budget.monthly_usd:.2f}")
    return 0


def _ensure_port_free(port: int) -> None:
    """Another app on the loopback address would silently shadow ours, so check both addresses."""
    import socket

    for address in ("127.0.0.1", "0.0.0.0"):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex((address, port)) == 0:
                raise HnListenerError(f"Port {port} is already in use; pick another with --port")


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import socket

    import uvicorn

    from hackernews_lite.preparation import make_prepare_work, make_thumbnail_worker
    from hackernews_lite.speech.audio_cache import AudioCache
    from hackernews_lite.web.app import create_app
    from hackernews_lite.web.jobs import JobRunner

    _ensure_port_free(args.port)
    store = StoryStore(settings.paths.data_dir)
    hn = AlgoliaHn()
    pipeline = _build_pipeline(settings, store, hn, use_llm=True, strict_jev=False)
    app = create_app(
        settings=settings,
        store=store,
        audio=AudioCache(store, KokoroTts(PROJECT_ROOT / "models"), _cost_log(settings)),
        feeds=FeedService(hn=hn, store=store, feeds=settings.feeds, daily=settings.daily),
        jobs=JobRunner(make_prepare_work(pipeline, store)),
        on_new_stories=make_thumbnail_worker(store, build_article_fetcher(), HttpImageFetcher()),
    )
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    print(f"\n  On this Mac:  http://localhost:{args.port}")
    if args.lan:
        lan_ip = socket.gethostbyname(socket.gethostname())
        print(f"  On your phone (same wifi):  http://{lan_ip}:{args.port}")
    print("  First play of each clip takes a few seconds while the voice is made.\n")
    uvicorn.run(app, host=host, port=args.port, log_level="warning")
    return 0
