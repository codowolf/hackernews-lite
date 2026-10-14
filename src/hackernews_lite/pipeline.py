"""Wires the stages together. Only this module and the CLI know which vendors are in use."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hackernews_lite.config import Settings
from hackernews_lite.domain.errors import HnListenerError
from hackernews_lite.domain.models import Story
from hackernews_lite.llm.client import LlmClient
from hackernews_lite.prep.article_prep import prep_article
from hackernews_lite.prep.article_stage import prepare_article
from hackernews_lite.prep.comment_curation import curate_comments
from hackernews_lite.prep.comment_scoring import CommentScorer, pick_by_score
from hackernews_lite.prep.comment_stance import analyze_stance
from hackernews_lite.prep.comment_stance_quick import StanceLabeler, analyze_stance_quick
from hackernews_lite.prep.highlight import add_highlights
from hackernews_lite.prep.prompts import load_prompt
from hackernews_lite.prep.stage import StageResult, StageStatus
from hackernews_lite.prep.thumbnail import prepare_thumbnail
from hackernews_lite.sources.article_fetcher import ArticleFetcher
from hackernews_lite.sources.hn_api import HnSource
from hackernews_lite.sources.images import ImageFetcher
from hackernews_lite.storage.story_store import StoryStore


@dataclass
class StoryOutcome:
    story: Story
    stages: dict[str, StageResult] = field(default_factory=dict)


class Pipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        hn: HnSource,
        fetcher: ArticleFetcher,
        llm: LlmClient | None,
        store: StoryStore,
        scorer: CommentScorer | None = None,
        labeler: StanceLabeler | None = None,
        images: ImageFetcher | None = None,
    ) -> None:
        self._settings = settings
        self._hn = hn
        self._fetcher = fetcher
        self._llm = llm
        self._store = store
        self._scorer = scorer
        self._labeler = labeler
        self._images = images

    def run_story(self, story: Story, *, use_llm: bool = True, force: bool = False) -> StoryOutcome:
        """Run every stage for one story. A failing stage is recorded and never stops the others."""
        outcome = StoryOutcome(story)
        prompts_dir = self._settings.paths.prompts_dir
        llm_settings = self._settings.llm

        def run(name: str, stage: Callable[[], StageResult]) -> None:
            try:
                outcome.stages[name] = stage()
            except HnListenerError as exc:
                outcome.stages[name] = StageResult(StageStatus.FAILED, str(exc))

        def ingest() -> StageResult:
            fetched, comments = self._hn.thread(story.id)
            fetched = fetched.model_copy(update={"fetched_at": datetime.now(UTC)})
            self._store.save_story(fetched)
            self._store.save_comments(story.id, comments)
            return StageResult(StageStatus.DONE, f"{fetched.num_comments} comments")

        run("ingest", ingest)
        run("article", lambda: prepare_article(story, self._store, self._fetcher, force=force))
        if self._images is not None:
            images = self._images
            run(
                "thumbnail",
                lambda: prepare_thumbnail(story, self._store, self._fetcher, images, force=force),
            )
        llm = self._llm if use_llm else None
        if llm is not None:
            run(
                "prep",
                lambda: prep_article(
                    story,
                    self._store,
                    llm,
                    load_prompt(prompts_dir, "article_prep"),
                    llm_settings.article_prep,
                    force=force,
                ),
            )
        methods = self._settings.curation.methods
        if llm is not None and "llm" in methods:
            run(
                "comments",
                lambda: curate_comments(
                    story,
                    self._store,
                    llm,
                    load_prompt(prompts_dir, "comment_curation"),
                    llm_settings.comment_curation,
                    force=force,
                ),
            )
        if use_llm and "jev" in methods and self._scorer is not None:
            scorer = self._scorer
            run(
                "comments_jev",
                lambda: pick_by_score(
                    story, self._store, scorer, self._settings.curation, force=force
                ),
            )
        self._run_stance(run, story, llm, force=force)
        if llm is not None:  # a tiny call that bolds key phrases in the cheaply picked quotes
            run(
                "highlight",
                lambda: add_highlights(
                    story,
                    self._store,
                    llm,
                    load_prompt(prompts_dir, "highlight"),
                    llm_settings.highlight,
                    force=force,
                ),
            )
        return outcome

    def _run_stance(
        self,
        run: Callable[[str, Callable[[], StageResult]], None],
        story: Story,
        llm: LlmClient | None,
        *,
        force: bool,
    ) -> None:
        """How the thread reacted: cheap labels by default, or Claude reading it all if set."""
        mode = self._settings.curation.stance
        if mode == "quick" and self._labeler is not None:
            labeler = self._labeler
            run("stance", lambda: analyze_stance_quick(story, self._store, labeler, force=force))
        elif mode == "deep" and llm is not None:
            run("stance", lambda: self._deep_stance(story, llm, force=force))

    def deepen_story(self, story: Story) -> StoryOutcome:
        """The deep analysis, on request: Claude reads the thread, finds debates, gives a take."""
        outcome = StoryOutcome(story)
        if self._llm is None:
            outcome.stages["stance"] = StageResult(StageStatus.FAILED, "no model is available")
            return outcome
        llm = self._llm
        try:
            outcome.stages["stance"] = self._deep_stance(story, llm, force=True)
        except HnListenerError as exc:
            outcome.stages["stance"] = StageResult(StageStatus.FAILED, str(exc))
        return outcome

    def _deep_stance(self, story: Story, llm: LlmClient, *, force: bool) -> StageResult:
        return analyze_stance(
            story,
            self._store,
            llm,
            load_prompt(self._settings.paths.prompts_dir, "comment_stance"),
            self._settings.llm.stance,
            force=force,
        )
