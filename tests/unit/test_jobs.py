import threading
import time

from hackernews_lite.domain.errors import HnListenerError
from hackernews_lite.web.jobs import JobMode, JobRunner, JobState


def wait_until(runner: JobRunner, story_id: int, *states: JobState) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = runner.get(story_id)
        if job and job.state in states:
            return
        time.sleep(0.01)
    raise AssertionError(f"job {story_id} never reached {states}")


def test_a_job_runs_and_finishes() -> None:
    ran: list[int] = []
    runner = JobRunner(lambda story_id, mode: ran.append(story_id))
    runner.submit(1)
    wait_until(runner, 1, JobState.DONE)
    assert ran == [1]


def test_known_failures_are_reported_with_their_reason() -> None:
    def work(story_id: int, mode: JobMode) -> None:
        raise HnListenerError("the model refused")

    runner = JobRunner(work)
    runner.submit(1)
    wait_until(runner, 1, JobState.FAILED)
    job = runner.get(1)
    assert job is not None and job.detail == "the model refused"


def test_bugs_do_not_leave_a_job_running_forever() -> None:
    def work(story_id: int, mode: JobMode) -> None:
        raise ValueError("oops")

    runner = JobRunner(work)
    runner.submit(1)
    wait_until(runner, 1, JobState.FAILED)
    job = runner.get(1)
    assert job is not None and "oops" in job.detail


def test_submitting_a_running_job_again_does_not_start_a_second_one() -> None:
    gate = threading.Event()
    calls: list[int] = []

    def work(story_id: int, mode: JobMode) -> None:
        calls.append(story_id)
        gate.wait(5)

    runner = JobRunner(work)
    runner.submit(1)
    wait_until(runner, 1, JobState.RUNNING)
    runner.submit(1)
    gate.set()
    wait_until(runner, 1, JobState.DONE)
    assert calls == [1]


def test_a_failed_job_can_be_retried() -> None:
    attempts: list[int] = []

    def work(story_id: int, mode: JobMode) -> None:
        attempts.append(story_id)
        if len(attempts) == 1:
            raise HnListenerError("first try fails")

    runner = JobRunner(work)
    runner.submit(1)
    wait_until(runner, 1, JobState.FAILED)
    runner.submit(1)
    wait_until(runner, 1, JobState.DONE)
    assert attempts == [1, 1]


def test_the_mode_of_a_request_reaches_the_work() -> None:
    seen: list[tuple[int, JobMode]] = []
    runner = JobRunner(lambda story_id, mode: seen.append((story_id, mode)))
    runner.submit(1)
    wait_until(runner, 1, JobState.DONE)
    runner.submit(1, mode=JobMode.REFRESH)
    wait_until(runner, 1, JobState.DONE)
    runner.submit(1, mode=JobMode.DEEPEN)
    wait_until(runner, 1, JobState.DONE)
    time.sleep(0.05)
    assert seen == [(1, JobMode.PREPARE), (1, JobMode.REFRESH), (1, JobMode.DEEPEN)]


def test_what_the_reader_opens_goes_ahead_of_background_work() -> None:
    gate = threading.Event()
    order: list[int] = []

    def work(story_id: int, mode: JobMode) -> None:
        order.append(story_id)
        if story_id == 1:
            gate.wait(5)  # hold the single worker so the others queue up behind it

    runner = JobRunner(work)
    runner.submit(1)
    wait_until(runner, 1, JobState.RUNNING)
    runner.submit(2, background=True)
    runner.submit(3, background=True)
    runner.submit(4)  # the reader asked for this one last
    gate.set()
    for story_id in (2, 3, 4):
        wait_until(runner, story_id, JobState.DONE)
    assert order == [1, 4, 2, 3]


def test_opening_a_story_that_was_queued_as_background_work_moves_it_forward() -> None:
    gate = threading.Event()
    order: list[int] = []

    def work(story_id: int, mode: JobMode) -> None:
        order.append(story_id)
        if story_id == 1:
            gate.wait(5)

    runner = JobRunner(work)
    runner.submit(1)
    wait_until(runner, 1, JobState.RUNNING)
    runner.submit(2, background=True)
    runner.submit(3, background=True)
    upgraded = runner.submit(3)  # now the reader opens 3
    assert upgraded.background is False
    gate.set()
    wait_until(runner, 3, JobState.DONE)
    wait_until(runner, 2, JobState.DONE)
    assert order == [1, 3, 2]  # 3 ran once, ahead of 2, and was not run again


def test_a_story_asked_for_twice_runs_once() -> None:
    runs: list[int] = []
    gate = threading.Event()

    def work(story_id: int, mode: JobMode) -> None:
        runs.append(story_id)
        gate.wait(5)

    runner = JobRunner(work)
    runner.submit(1)
    wait_until(runner, 1, JobState.RUNNING)
    runner.submit(1)
    runner.submit(1, background=True)
    gate.set()
    wait_until(runner, 1, JobState.DONE)
    time.sleep(0.05)
    assert runs == [1]
