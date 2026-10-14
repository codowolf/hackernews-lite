"""What the reader has done with stories.

Archived means "done with it, keep for later"; ignored means "never show me this".
One small JSON file, written atomically. A story is in at most one of the two lists.
"""

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

StoryState = Literal["archived", "ignored"]
STATES: tuple[StoryState, ...] = ("archived", "ignored")


class UserState:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._marks: dict[StoryState, dict[int, str]] = {"archived": {}, "ignored": {}}
        self._load()

    def state_of(self, story_id: int) -> StoryState | None:
        with self._lock:
            return next((s for s in STATES if story_id in self._marks[s]), None)

    def ids(self, state: StoryState) -> list[int]:
        """Story ids in this list, most recently added first."""
        with self._lock:
            marks = self._marks[state]
            return sorted(marks, key=lambda story_id: marks[story_id], reverse=True)

    def counts(self) -> dict[StoryState, int]:
        with self._lock:
            return {state: len(self._marks[state]) for state in STATES}

    def mark(self, story_id: int, state: StoryState) -> None:
        """Move a story into `state`, taking it out of the other list."""
        with self._lock:
            for other in STATES:
                self._marks[other].pop(story_id, None)
            self._marks[state][story_id] = datetime.now(UTC).isoformat()
            self._save()

    def restore(self, story_id: int) -> None:
        with self._lock:
            for state in STATES:
                self._marks[state].pop(story_id, None)
            self._save()

    # --- storage ------------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # an unreadable file is treated as empty; the next change rewrites it
        for state in STATES:
            entries = raw.get(state, {}) if isinstance(raw, dict) else {}
            if isinstance(entries, dict):
                self._marks[state] = {int(k): str(v) for k, v in entries.items() if k.isdigit()}

    def _save(self) -> None:
        payload = {
            state: {str(story_id): at for story_id, at in marks.items()}
            for state, marks in self._marks.items()
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp, self._path)
