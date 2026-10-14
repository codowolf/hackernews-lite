"""The on-disk layout (data/stories/<id>/...). Every stage reads and writes through here."""

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from hackernews_lite.domain.models import (
    Article,
    ArticlePrep,
    Comment,
    CommentPicks,
    CommentScoreSet,
    CommentStance,
    FeedSnapshot,
    Story,
    Thumbnail,
)

M = TypeVar("M", bound=BaseModel)

_COMMENTS = TypeAdapter(list[Comment])


class StoryStore:
    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "stories"
        self._feeds = data_dir / "feeds"

    def story_dir(self, story_id: int) -> Path:
        return self._root / str(story_id)

    def story_ids(self) -> list[int]:
        if not self._root.exists():
            return []
        return sorted(int(p.name) for p in self._root.iterdir() if p.name.isdigit())

    # --- feeds ---------------------------------------------------------------------------

    def save_feed(self, feed: FeedSnapshot) -> None:
        self._write_model(feed, self._feeds / f"{feed.id}.json")

    def load_feed(self, feed_id: str) -> FeedSnapshot | None:
        return self._read_model(FeedSnapshot, self._feeds / f"{feed_id}.json")

    # --- story and comments -------------------------------------------------------------

    def save_story(self, story: Story) -> None:
        self._write_model(story, self.story_dir(story.id) / "story.json")

    def load_story(self, story_id: int) -> Story | None:
        return self._read_model(Story, self.story_dir(story_id) / "story.json")

    def save_comments(self, story_id: int, comments: list[Comment]) -> None:
        self._write_text(
            self.story_dir(story_id) / "comments.json",
            _COMMENTS.dump_json(comments, indent=2).decode(),
        )

    def load_comments(self, story_id: int) -> list[Comment] | None:
        path = self.story_dir(story_id) / "comments.json"
        if not path.exists():
            return None
        return _COMMENTS.validate_json(path.read_text(encoding="utf-8"))

    # --- article ------------------------------------------------------------------------

    def save_raw_article(self, story_id: int, html: str) -> None:
        self._write_text(self.story_dir(story_id) / "article.html", html)

    def load_raw_article(self, story_id: int) -> str | None:
        path = self.story_dir(story_id) / "article.html"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else None

    def save_article(self, story_id: int, article: Article) -> None:
        directory = self.story_dir(story_id)
        self._write_model(article, directory / "article.json")
        if article.text:
            self._write_text(directory / "article.md", article.text)

    def load_article(self, story_id: int) -> Article | None:
        return self._read_model(Article, self.story_dir(story_id) / "article.json")

    # --- LLM outputs, one file each so TTS and the UI can read them later -----------------

    def save_prep(self, story_id: int, prep: ArticlePrep) -> None:
        self._write_model(prep, self.story_dir(story_id) / "prep" / "article_prep.json")

    def load_prep(self, story_id: int) -> ArticlePrep | None:
        return self._read_model(
            ArticlePrep, self.story_dir(story_id) / "prep" / "article_prep.json"
        )

    def save_picks(self, story_id: int, picks: CommentPicks) -> None:
        self._write_model(picks, self._picks_path(story_id, picks.method))

    def load_picks(self, story_id: int, method: str = "llm") -> CommentPicks | None:
        return self._read_model(CommentPicks, self._picks_path(story_id, method))

    def save_stance(self, story_id: int, stance: CommentStance) -> None:
        self._write_model(stance, self.story_dir(story_id) / "comment_stance.json")

    def load_stance(self, story_id: int) -> CommentStance | None:
        return self._read_model(CommentStance, self.story_dir(story_id) / "comment_stance.json")

    def save_scores(self, story_id: int, scores: CommentScoreSet) -> None:
        self._write_model(scores, self.story_dir(story_id) / "comment_scores.json")

    def load_scores(self, story_id: int) -> CommentScoreSet | None:
        return self._read_model(CommentScoreSet, self.story_dir(story_id) / "comment_scores.json")

    def _picks_path(self, story_id: int, method: str) -> Path:
        name = "comments_picks.json" if method == "llm" else f"comments_picks_{method}.json"
        return self.story_dir(story_id) / name

    # --- thumbnail ----------------------------------------------------------------------

    def thumbnail_path(self, story_id: int) -> Path:
        return self.story_dir(story_id) / "thumb.webp"

    def save_thumbnail(self, story_id: int, meta: Thumbnail, image: bytes | None = None) -> None:
        if image is not None:
            path = self.thumbnail_path(story_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_bytes(image)
            os.replace(temp, path)
        self._write_model(meta, self.story_dir(story_id) / "thumbnail.json")

    def load_thumbnail(self, story_id: int) -> Thumbnail | None:
        return self._read_model(Thumbnail, self.story_dir(story_id) / "thumbnail.json")

    def analysis_updated_at(self, story_id: int) -> datetime | None:
        """When the LLM analysis (summary, picks, reaction) was last made, from its files."""
        directory = self.story_dir(story_id)
        files = [
            directory / "comment_stance.json",
            directory / "comments_picks.json",
            directory / "prep" / "article_prep.json",
        ]
        times = [path.stat().st_mtime for path in files if path.exists()]
        return datetime.fromtimestamp(max(times), tz=UTC) if times else None

    # --- audio ----------------------------------------------------------------------------

    def audio_path(self, story_id: int, content_hash: str, extension: str = "mp3") -> Path:
        return self.story_dir(story_id) / "audio" / f"{content_hash}.{extension}"

    def save_audio(self, story_id: int, content_hash: str, audio: bytes) -> Path:
        path = self.audio_path(story_id, content_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_bytes(audio)
        os.replace(temp, path)
        return path

    def audio_bytes(self, story_id: int) -> int:
        """Disk used by this story's saved voice clips."""
        directory = self.story_dir(story_id) / "audio"
        if not directory.is_dir():
            return 0
        return sum(file.stat().st_size for file in directory.iterdir() if file.is_file())

    def clear_audio(self, story_id: int) -> int:
        """Delete this story's voice clips (made again on demand). Returns the bytes freed."""
        directory = self.story_dir(story_id) / "audio"
        freed = self.audio_bytes(story_id)
        if directory.is_dir():
            shutil.rmtree(directory)
        return freed

    # --- helpers ------------------------------------------------------------------------

    def _write_model(self, model: BaseModel, path: Path) -> None:
        self._write_text(
            path, json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False)
        )

    def _read_model(self, cls: type[M], path: Path) -> M | None:
        if not path.exists():
            return None
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        """Write atomically, so a crash never leaves a half-written file for the next stage."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, path)
