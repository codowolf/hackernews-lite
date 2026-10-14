"""Typed settings loaded from config.toml: which models and paths are in use."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Effort = Literal["low", "medium", "high"]
CurationMethod = Literal["llm", "jev"]


class Paths(BaseModel):
    data_dir: Path = Path("data")
    prompts_dir: Path = Path("prompts")
    env_file: Path = Path(".env")  # secrets such as TYPESAFE_API_KEY; never checked in


FeedKind = Literal["front_page", "window", "best", "daily"]


class Feed(BaseModel):
    """One way of choosing stories: the front page, a time window, HN's best, or each day's best."""

    id: str
    label: str  # short, for the switcher
    title: str  # heading above the list
    kind: FeedKind
    window_hours: int = 0  # for "window": how far back to look
    min_points: int = 0  # for "window": ignore anything below this
    limit: int = 20

    @property
    def grouped(self) -> bool:
        """Whether the list reads as one group per day (the app shows day headings)."""
        return self.kind == "daily"


def _default_feeds() -> list[Feed]:
    return [
        Feed(id="daily", label="Daily", title="Best of each day", kind="daily"),
        Feed(id="top", label="Top", title="Top stories now", kind="front_page", limit=30),
        Feed(
            id="24h",
            label="24h",
            title="Last 24 hours",
            kind="window",
            window_hours=24,
            min_points=50,
        ),
        Feed(
            id="48h",
            label="48h",
            title="Last 48 hours",
            kind="window",
            window_hours=48,
            min_points=100,
        ),
        Feed(
            id="week",
            label="Week",
            title="Last week",
            kind="window",
            window_hours=168,
            min_points=200,
        ),
        Feed(id="best", label="Best", title="Best stories", kind="best"),
    ]


class DayMode(BaseModel):
    """How much of each day to show on the Daily list. No count and no percent means all."""

    id: str
    label: str
    count: int = 0  # the best N stories of the day
    percent: float = 0  # or the best this percent of the day's stories (that reached min_points)

    @model_validator(mode="after")
    def _not_both(self) -> "DayMode":
        if self.count and self.percent:
            raise ValueError(f"day mode '{self.id}': give a count or a percent, not both")
        if not 0 <= self.percent <= 100 or self.count < 0:
            raise ValueError(f"day mode '{self.id}': count and percent must be positive")
        return self


def _default_day_modes() -> list[DayMode]:
    return [
        DayMode(id="top5", label="Top 5", count=5),
        DayMode(id="top10", label="Top 10", count=10),
        DayMode(id="top20", label="Top 20", count=20),
    ]


class Daily(BaseModel):
    """The Daily list: each day on its own, with a choice of how much of the day to show."""

    default_mode: str = "top5"
    page_days: int = 4  # days shown at first, and added by each "Show earlier days"
    max_days: int = 30
    min_points: int = 30  # a story needs this many points to count at all
    modes: list[DayMode] = Field(default_factory=_default_day_modes)

    def mode(self, mode_id: str) -> DayMode | None:
        return next((mode for mode in self.modes if mode.id == mode_id), None)


class Feeds(BaseModel):
    default: str = "daily"
    max_age_minutes: int = 60  # a list older than this is refreshed when opened
    items: list[Feed] = Field(default_factory=_default_feeds)

    def get(self, feed_id: str) -> Feed | None:
        return next((feed for feed in self.items if feed.id == feed_id), None)


class Budget(BaseModel):
    monthly_usd: float = 10.0


class LlmStage(BaseModel):
    model: str
    effort: Effort
    max_tokens: int


class LlmSettings(BaseModel):
    provider: Literal["claude_code", "anthropic"] = "claude_code"
    article_prep: LlmStage
    comment_curation: LlmStage
    stance: LlmStage
    highlight: LlmStage


class ModelPrice(BaseModel):
    input: float  # USD per million input tokens
    output: float  # USD per million output tokens


class Weights(BaseModel):
    """How much each signal counts toward a comment's score. Negative weights are penalties."""

    first_hand: float = 3.0
    corrects: float = 2.0
    substance: float = 2.0
    adds_info: float = 1.5
    standalone: float = 1.0
    hostile: float = -3.0


def _default_methods() -> list[CurationMethod]:
    return ["llm"]


class Curation(BaseModel):
    methods: list[CurationMethod] = Field(default_factory=_default_methods)
    weights: Weights = Field(default_factory=Weights)
    min_score: float = 0.35  # of a 0 to 1 scale; weaker comments are never picked
    max_picks: int = 8
    max_per_thread: int = 2  # picks from one top-level comment and its replies
    # How the thread's reaction is made. "quick" labels the top comments with Jev: cheap, no
    # Claude tokens. "deep" has Claude read the whole thread (debates and a take) for every story.
    # "off" skips it. Deep analysis can always be asked for on a story's page.
    stance: Literal["off", "quick", "deep"] = "quick"


class SpeechSettings(BaseModel):
    provider: Literal["kokoro"] = "kokoro"
    voice: str = "af_heart"


class Trending(BaseModel):
    """What makes a story "hot" in the list (the flame icon)."""

    min_points_per_hour: float = 30.0  # average upvotes per hour since it was posted
    min_comments: int = 500  # or a very busy discussion
    min_points: int = 100  # never flag a story with fewer upvotes than this


class Prepare(BaseModel):
    """When stories get analyzed, so reading never waits on a button."""

    on_open: bool = True  # opening a story analyzes it at once if it has not been
    list_top: int = (
        10  # also get the first N stories of each list ready in the background (0 = off)
    )


class JevSettings(BaseModel):
    model: str = "jev-latest"
    workers: int = 8


class Settings(BaseModel):
    paths: Paths = Field(default_factory=Paths)
    feeds: Feeds = Field(default_factory=Feeds)
    daily: Daily = Field(default_factory=Daily)
    prepare: Prepare = Field(default_factory=Prepare)
    budget: Budget = Field(default_factory=Budget)
    llm: LlmSettings
    curation: Curation = Field(default_factory=Curation)
    jev: JevSettings = Field(default_factory=JevSettings)
    speech: SpeechSettings = Field(default_factory=SpeechSettings)
    trending: Trending = Field(default_factory=Trending)
    pricing: dict[str, ModelPrice]

    def resolve(self, root: Path) -> "Settings":
        """Return a copy whose relative paths are anchored at `root` (the project directory)."""
        paths = Paths(
            data_dir=root / self.paths.data_dir,
            prompts_dir=root / self.paths.prompts_dir,
            env_file=root / self.paths.env_file,
        )
        return self.model_copy(update={"paths": paths})


def load_settings(path: Path) -> Settings:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return Settings.model_validate(raw).resolve(path.parent)
