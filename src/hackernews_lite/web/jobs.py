"""Runs slow work (preparing a story) one at a time in the background, and reports progress.

Work the reader asked for (opening a story) always goes ahead of background work (getting the top
of a list ready), so what you are looking at is never stuck behind something you did not ask for.
"""

import itertools
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from hackernews_lite.domain.errors import HnListenerError

READER = 0  # the reader asked for this story
BACKGROUND = 1  # prepared ahead of time, in case the reader opens it


class JobMode(StrEnum):
    PREPARE = "prepare"  # analyze the story if it has not been
    REFRESH = "refresh"  # redo every step, even if it looks up to date
    DEEPEN = "deepen"  # the deep read of the thread: debates and a take


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class Job:
    story_id: int
    state: JobState
    detail: str = ""
    background: bool = False  # still waiting as background work; false once the reader wants it


class JobRunner:
    """One worker, so a burst of taps never starts many model calls at once."""

    def __init__(self, work: Callable[[int, JobMode], None]) -> None:
        """`work(story_id, mode)` does the job; it raises HnListenerError when it cannot."""
        self._work = work
        self._queue: queue.PriorityQueue[tuple[int, int, int, JobMode]] = queue.PriorityQueue()
        self._ticket = itertools.count()  # first come, first served within a priority
        self._latest: dict[int, int] = {}  # story id -> its current ticket; older ones are stale
        self._jobs: dict[int, Job] = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="prepare").start()

    def submit(
        self, story_id: int, *, mode: JobMode = JobMode.PREPARE, background: bool = False
    ) -> Job:
        with self._lock:
            current = self._jobs.get(story_id)
            if current and current.state in (JobState.QUEUED, JobState.RUNNING):
                # Already on its way. If the reader now wants a story that was only queued as
                # background work, move it to the front.
                if current.state is JobState.QUEUED and current.background and not background:
                    job = Job(story_id, JobState.QUEUED, background=False)
                    self._jobs[story_id] = job
                    self._enqueue(story_id, mode, READER)
                    return job
                return current
            job = Job(story_id, JobState.QUEUED, background=background)
            self._jobs[story_id] = job
            self._enqueue(story_id, mode, BACKGROUND if background else READER)
        return job

    def get(self, story_id: int) -> Job | None:
        with self._lock:
            return self._jobs.get(story_id)

    def all(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def _enqueue(self, story_id: int, mode: JobMode, priority: int) -> None:
        ticket = next(self._ticket)
        self._latest[story_id] = ticket
        self._queue.put((priority, ticket, story_id, mode))

    def _loop(self) -> None:
        while True:
            _, ticket, story_id, mode = self._queue.get()
            with self._lock:
                if self._latest.get(story_id) != ticket:
                    continue  # replaced by a newer request for the same story
                self._jobs[story_id] = Job(story_id, JobState.RUNNING)
            self._run(story_id, mode)

    def _run(self, story_id: int, mode: JobMode) -> None:
        try:
            self._work(story_id, mode)
        except HnListenerError as exc:
            self._set(Job(story_id, JobState.FAILED, str(exc)))
        except Exception as exc:  # a bug must not leave the card spinning forever
            self._set(Job(story_id, JobState.FAILED, f"Unexpected error: {exc}"))
        else:
            self._set(Job(story_id, JobState.DONE))

    def _set(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.story_id] = job
