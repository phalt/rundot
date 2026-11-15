"""Tests for the run hub and WebPrompter, with queues and threads and no server.

Every run is the real runner.run on a small runbook in tmp_path, with blocks really run under bash. Every wait has
a timeout, so a hang fails the test instead of stalling the suite.
"""

import queue
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from rundot import runner
from rundot.backends import load_backends
from rundot.runbook import parse
from rundot.web.hub import RunHub

# Long enough for bash to start on a slow CI machine; only reached when something is wrong.
TIMEOUT = 10
AT = datetime(2026, 11, 15, 9, 30, tzinfo=timezone.utc)


def write_runbook(directory: Path, text: str) -> Path:
    path = directory / "runbook.md"
    path.write_text(textwrap.dedent(text))
    return path


def starter(path: Path, **options: Any):
    """A start_run that runs the runbook at path, as the CLI's will, with any of run's options."""

    def start_run(prompter, sink, resume):
        return runner.run(parse(path), load_backends(), sink, prompter, cwd=path.parent, **options)

    return start_run


def until(events: "queue.Queue[dict[str, Any] | None]", name: str) -> list[dict[str, Any]]:
    """Every event from events up to and including the first called name."""
    seen = []
    while True:
        event = events.get(timeout=TIMEOUT)
        assert event is not None, f"the hub stopped before {name}"
        seen.append(event)
        if event["event"] == name:
            return seen


def names(events: list[dict[str, Any]]) -> list[str]:
    return [event["event"] for event in events]


@pytest.fixture
def hub():
    """A hub with a stopped clock, stopped after the test so no worker thread outlives it."""
    hub = RunHub(clock=lambda: AT)
    yield hub
    hub.stop(timeout=TIMEOUT)
    assert hub.running is None


HELLO = """\
    # Hello

    ## Greet

    ```sh run
    echo hello
    ```
    """


def test_a_run_sends_its_events_as_json_lines_objects(hub, tmp_path):
    """The run happens on the hub's thread, and each event is what --json would print for it."""
    path = write_runbook(tmp_path, HELLO)
    events = hub.subscribe()

    hub.start(path, starter(path, ask_to_start=False), resume=False)
    seen = until(events, "run_finished")

    assert names(seen) == ["overview", "step_started", "block_started", "output", "block_finished", "run_finished"]
    assert seen[0]["at"] == AT.isoformat(timespec="milliseconds")
    assert seen[1] == {"event": "step_started", "at": seen[0]["at"], "number": 1, "name": "Greet"}
    assert seen[3]["text"] == "hello"
    assert seen[-1]["status"] == "ok"
    assert hub.wait(timeout=TIMEOUT)
