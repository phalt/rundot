"""The run hub: the one run a rundot web server has going, and the prompter that lets the page answer it."""

import queue
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from rundot import runner
from rundot.inputs import Input, Prompter
from rundot.jsonlines import to_json
from rundot.runbook import Step

Sink = Callable[[runner.Event], None]
# What the CLI gives the hub to start a run: it wires the transcript, run state and saved inputs, then calls
# runner.run with the prompter and sink it's given. resume says whether to pick up a run that stopped.
StartRun = Callable[[Prompter, Sink, bool], object]
# Each subscriber's queue gets events as JSON objects, then None once the hub stops.
Events = queue.Queue[dict[str, Any] | None]


class Busy(Exception):
    """A run is already going, of the book at path. There's one run at a time per server."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"{path.name} is running")
        self.path = path


# The runner's questions, by the kind of control that answers each: Start, a run block's ▶, and a confirm's Done.
_CONFIRMS = {"Start? [y/N]": "start", runner.BLOCK_QUESTION: "block", "Continue? [y/N]": "confirm"}


class WrongAnswer(Exception):
    """An answer that isn't for the prompt being waited on, or comes when nothing is waiting."""


class WebPrompter:
    """Answers the runner's prompts with answers the page sends.

    Each prompt is published as a prompt event, then the runner's thread waits until answer gives its value.
    """

    def __init__(self, publish: Callable[[str, dict[str, Any]], None]) -> None:
        """publish sends a hub event, given its name and fields."""
        self._publish = publish
        self._lock = threading.Lock()
        self._waiting: str | None = None  # the kind of prompt waiting for an answer
        # None is stop's, so the runner's thread never waits on an answer that can't come.
        self._answers: queue.Queue[str | None] = queue.Queue()
        self._stopped = False

    def ask(self, input: Input, problem: str | None) -> str:
        # Never the value: the page has the input's description, and a secret's answer must not come back.
        return self._prompt("input", {"name": input.name, "problem": problem})

    def confirm(self, question: str) -> str:
        if (kind := _CONFIRMS.get(question)) is None:
            raise ValueError(f"no control answers {question!r}")
        return self._prompt(kind, {})

    def answer(self, kind: str, value: str) -> None:
        """Answer the prompt being waited on, which must be of this kind. Raises WrongAnswer otherwise."""
        with self._lock:
            if self._waiting is None:
                raise WrongAnswer(f"nothing is waiting for an answer, so {kind} can't be one")
            if self._waiting != kind:
                raise WrongAnswer(f"waiting for an answer to {self._waiting}, not {kind}")
            self._waiting = None
            self._answers.put(value)

    def stop(self) -> None:
        """Interrupt the prompt being waited on, and every later one, as Ctrl-C does at the terminal.

        The runner finishes the run as interrupted for it, so the thread waiting here ends.
        """
        with self._lock:
            self._stopped = True
            self._waiting = None
            self._answers.put(None)

    def _prompt(self, kind: str, fields: dict[str, Any]) -> str:
        # Waiting before it's published, so a page that answers straight away isn't refused.
        with self._lock:
            if self._stopped:
                raise KeyboardInterrupt
            self._waiting = kind
        self._publish("prompt", {"kind": kind, **fields})
        if (value := self._answers.get()) is None:
            raise KeyboardInterrupt
        return value


class RunHub:
    """Owns at most one run, on a daemon worker thread, and every event it has sent so far.

    Events are jsonlines.to_json objects, so the page reads the same schema as --json.
    """

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now().astimezone()) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._buffer: list[dict[str, Any]] = []
        self._subscribers: list[Events] = []
        self._steps: tuple[Step, ...] = ()
        self._thread: threading.Thread | None = None
        self._path: Path | None = None
        self._prompter: WebPrompter | None = None

    @property
    def running(self) -> Path | None:
        """The book whose run is going, or None."""
        with self._lock:
            return self._running()

    def start(self, path: Path, start_run: StartRun, resume: bool) -> None:
        """Start a run of the book at path on a new worker thread, with start_run.

        Raises Busy if a run is going, of this book or any other.
        """
        prompter = WebPrompter(self._hub_event)
        with self._lock:
            if (path_running := self._running()) is not None:
                raise Busy(path_running)
            self._buffer = []
            self._path = path
            self._prompter = prompter
            self._thread = threading.Thread(
                target=self._work, args=(start_run, prompter, resume), name=f"rundot run {path.name}", daemon=True
            )
            self._thread.start()

    def subscribe(self) -> Events:
        """A queue that gets every event of the run so far, then each new one as it happens."""
        events: Events = queue.Queue()
        with self._lock:
            for event in self._buffer:
                events.put(event)
            self._subscribers.append(events)
        return events

    def answer(self, kind: str, value: str) -> None:
        """Answer the prompt the run is waiting on. Raises WrongAnswer if it isn't waiting on one of this kind."""
        with self._lock:
            prompter = self._prompter
        if prompter is None:
            raise WrongAnswer(f"no run has started, so {kind} can't be an answer")
        prompter.answer(kind, value)

    def wait(self, timeout: float) -> bool:
        """Wait up to timeout seconds for the run to end. Whether no run is going."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.running is None

    def stop(self, timeout: float) -> None:
        """Stop the run, wait up to timeout seconds for its thread to end, and end every subscriber's queue.

        A prompt being waited on, or the run's next one, is interrupted, so the run finishes as interrupted.
        """
        with self._lock:
            prompter = self._prompter
        if prompter is not None:
            prompter.stop()
        self.wait(timeout)
        with self._lock:
            for events in self._subscribers:
                events.put(None)
            self._subscribers = []

    def _work(self, start_run: StartRun, prompter: WebPrompter, resume: bool) -> None:
        try:
            start_run(prompter, self._sink, resume)
        except KeyboardInterrupt:
            # The runner finishes a run as interrupted once its overview is sent. Before that, while it asks for
            # the frontmatter inputs, Ctrl-C escapes it, so the run is finished here.
            self._sink(runner.RunFinished("interrupted", 0.0))

    def _running(self) -> Path | None:
        """running, for a caller that holds the lock."""
        return self._path if self._thread is not None and self._thread.is_alive() else None

    def _sink(self, event: runner.Event) -> None:
        if isinstance(event, runner.Overview):
            self._steps = event.steps
        self._publish(to_json(event, self._steps, self._clock()))

    def _hub_event(self, name: str, fields: dict[str, Any]) -> None:
        """Publish an event only the hub sends, such as prompt, shaped like to_json's."""
        self._publish({"event": name, "at": self._clock().isoformat(timespec="milliseconds"), **fields})

    def _publish(self, event: dict[str, Any]) -> None:
        # Under the lock, so a subscriber that joins now gets each event exactly once: replayed or live.
        with self._lock:
            self._buffer.append(event)
            for events in self._subscribers:
                events.put(event)
