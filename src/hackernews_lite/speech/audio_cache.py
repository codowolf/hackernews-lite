"""Speaks a segment once, then serves the saved clip. Re-listening costs nothing."""

import threading
from datetime import UTC, datetime
from pathlib import Path

from hackernews_lite.domain.hashing import content_hash
from hackernews_lite.speech.provider import TtsProvider
from hackernews_lite.storage.cost_log import CostEvent, CostLog
from hackernews_lite.storage.story_store import StoryStore


class AudioCache:
    def __init__(
        self, store: StoryStore, provider: TtsProvider, log: CostLog | None = None
    ) -> None:
        self._store = store
        self._provider = provider
        self._log = log
        self._lock = threading.Lock()  # local models are not safe to run in parallel

    @property
    def voices(self) -> list[str]:
        return self._provider.voices()

    def audio_for(self, story_id: int, text: str, voice: str) -> Path:
        key = content_hash(self._provider.name, voice, text)
        path = self._store.audio_path(story_id, key)
        if path.exists():
            return path
        with self._lock:
            if path.exists():  # another request finished it while this one waited
                return path
            audio = self._provider.synthesize(text, voice)
            saved = self._store.save_audio(story_id, key, audio)
        self._record_cost(story_id, voice, len(text))
        return saved

    def _record_cost(self, story_id: int, voice: str, characters: int) -> None:
        usd = self._provider.cost_usd(characters)
        if self._log is None or usd <= 0:
            return
        self._log.record(
            CostEvent(
                at=datetime.now(UTC).isoformat(timespec="seconds"),
                kind="tts",
                model=f"{self._provider.name}:{voice}",
                units=f"{characters} characters",
                usd=usd,
                story_id=story_id,
            )
        )
