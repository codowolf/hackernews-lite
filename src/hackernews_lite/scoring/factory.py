"""Builds the configured CommentScorer. The Jev SDK is imported only when it is actually used."""

from hackernews_lite.config import Settings
from hackernews_lite.domain.errors import ScoringError
from hackernews_lite.prep.comment_scoring import CommentScorer
from hackernews_lite.prep.comment_stance_quick import StanceLabeler
from hackernews_lite.scoring.metered import MeteredLabeler, MeteredScorer
from hackernews_lite.secrets import read_secret
from hackernews_lite.storage.cost_log import CostLog

KEY_NAME = "TYPESAFE_API_KEY"


def _api_key(settings: Settings) -> str:
    key = read_secret(KEY_NAME, settings.paths.env_file)
    if key is None:
        raise ScoringError(f"{KEY_NAME} is not set (put it in {settings.paths.env_file.name})")
    return key


def build_scorer(settings: Settings, log: CostLog) -> CommentScorer:
    from hackernews_lite.scoring.jev import JevScorer

    key = _api_key(settings)
    jev = JevScorer(model=settings.jev.model, workers=settings.jev.workers, api_key=key)
    return MeteredScorer(jev, settings.pricing, log)


def build_labeler(settings: Settings, log: CostLog) -> StanceLabeler:
    from hackernews_lite.scoring.jev_stance import JevStanceLabeler

    key = _api_key(settings)
    labeler = JevStanceLabeler(model=settings.jev.model, workers=settings.jev.workers, api_key=key)
    return MeteredLabeler(labeler, settings.pricing, log)
