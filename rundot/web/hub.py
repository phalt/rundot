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


class WebPrompter:
    """Answers the runner's prompts with answers the page sends."""

    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        self._publish = publish

    def ask(self, input: Input, problem: str | None) -> str:
        raise NotImplementedError

    def confirm(self, question: str) -> str:
        raise NotImplementedError


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

    @property
    def running(self) -> Path | None:
        """The book whose run is going, or None."""
        with self._lock:
            return self._running()

    def start(self, path: Path, start_run: StartRun, resume: bool) -> None:
        """Start a run of the book at path on a new worker thread, with start_run.

        Raises Busy if a run is going, of this book or any other.
        """
        prompter = WebPrompter(self._publish)
        with self._lock:
            if (path_running := self._running()) is not None:
                raise Busy(path_running)
            self._buffer = []
            self._path = path
            self._thread = threading.Thread(
                target=start_run, args=(prompter, self._sink, resume), name=f"rundot run {path.name}", daemon=True
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

    def wait(self, timeout: float) -> bool:
        """Wait up to timeout seconds for the run to end. Whether no run is going."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.running is None

    def stop(self, timeout: float) -> None:
        """Stop the run, wait up to timeout seconds for its thread to end, and end every subscriber's queue."""
        self.wait(timeout)
        with self._lock:
            for events in self._subscribers:
                events.put(None)
            self._subscribers = []

    def _running(self) -> Path | None:
        """running, for a caller that holds the lock."""
        return self._path if self._thread is not None and self._thread.is_alive() else None

    def _sink(self, event: runner.Event) -> None:
        if isinstance(event, runner.Overview):
            self._steps = event.steps
        self._publish(to_json(event, self._steps, self._clock()))

    def _publish(self, event: dict[str, Any]) -> None:
        # Under the lock, so a subscriber that joins now gets each event exactly once: replayed or live.
        with self._lock:
            self._buffer.append(event)
            for events in self._subscribers:
                events.put(event)
