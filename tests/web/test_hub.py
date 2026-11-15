"""Tests for the run hub and WebPrompter, with queues and threads and no server.

Every run is the real runner.run on a small runbook in tmp_path, with blocks really run under bash. Every wait has
a timeout, so a hang fails the test instead of stalling the suite.
"""

import queue
import textwrap
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from rundot import runner
from rundot.backends import load_backends
from rundot.runbook import parse
from rundot.web.hub import Busy, RunHub, WebPrompter, WrongAnswer

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


def held(release: threading.Event):
    """A start_run that goes on until release is set, standing in for a run that's waiting or running a block."""

    def start_run(prompter, sink, resume):
        assert release.wait(TIMEOUT)

    return start_run


def test_a_second_start_while_a_run_is_going_is_busy_naming_the_running_book(hub, tmp_path):
    """Only one run at a time: starting another, of any book, says which book is running and starts nothing."""
    release = threading.Event()
    hub.start(tmp_path / "first.md", held(release), resume=False)
    started = []

    with pytest.raises(Busy, match="first.md is running") as busy:
        hub.start(tmp_path / "second.md", lambda *args: started.append(args), resume=False)

    assert busy.value.path == tmp_path / "first.md"
    assert hub.running == tmp_path / "first.md"
    release.set()
    assert hub.wait(timeout=TIMEOUT)
    assert started == []


def test_a_book_can_start_again_once_its_run_has_finished(hub, tmp_path):
    """Run again: a finished run doesn't block the next, and the next starts with an empty buffer."""
    path = write_runbook(tmp_path, HELLO)
    hub.start(path, starter(path, ask_to_start=False), resume=False)
    assert hub.wait(timeout=TIMEOUT)
    assert hub.running is None

    hub.start(path, starter(path, ask_to_start=False), resume=True)
    assert hub.wait(timeout=TIMEOUT)

    assert names(until(hub.subscribe(), "run_finished")).count("overview") == 1


def test_a_subscriber_that_joins_mid_run_gets_every_earlier_event_first_in_order(hub, tmp_path):
    """A reloaded tab loses nothing: the buffer is replayed in order, then live events follow it."""
    path = write_runbook(
        tmp_path,
        """\
        ## Count

        ```sh run
        echo one
        echo two
        ```
        """,
    )
    paused, release = threading.Event(), threading.Event()
    run = starter(path, ask_to_start=False)

    def start_run(prompter, sink, resume):
        def sink_pausing_after_one(event):
            sink(event)
            if isinstance(event, runner.OutputLine) and event.text == "one":
                paused.set()
                assert release.wait(TIMEOUT)

        return run(prompter, sink_pausing_after_one, resume)

    early = hub.subscribe()
    hub.start(path, start_run, resume=False)
    assert paused.wait(TIMEOUT)
    late = hub.subscribe()
    release.set()

    everything = until(early, "run_finished")
    assert until(late, "run_finished") == everything
    assert [event.get("text") for event in everything if event["event"] == "output"] == ["one", "two"]


VERSIONED = """\
    ---
    inputs:
      - name: VERSION
        description: The version
        pattern: ^\\d+$
    ---

    ## Release

    ```sh run
    echo releasing <VERSION>
    ```
    """


def test_ask_publishes_an_input_prompt_and_returns_the_answer(hub, tmp_path):
    """The page is told which input to show, and what it sends is the input's value."""
    path = write_runbook(tmp_path, VERSIONED)
    events = hub.subscribe()
    hub.start(path, starter(path, ask_to_start=False), resume=False)

    prompt = until(events, "prompt")[-1]
    hub.answer("input", "3")

    assert prompt == {
        "event": "prompt",
        "at": AT.isoformat(timespec="milliseconds"),
        "kind": "input",
        "name": "VERSION",
        "problem": None,
    }
    seen = until(events, "run_finished")
    assert [event["text"] for event in seen if event["event"] == "output"] == ["releasing 3"]
    assert seen[-1]["status"] == "ok"


def test_a_refused_value_comes_back_as_a_second_prompt_with_the_problem(hub, tmp_path):
    """inputs.collect checks what the page sends as it checks what's typed, and says why it refused it."""
    path = write_runbook(tmp_path, VERSIONED)
    events = hub.subscribe()
    hub.start(path, starter(path, ask_to_start=False), resume=False)
    until(events, "prompt")

    hub.answer("input", "three")
    again = until(events, "prompt")[-1]
    hub.answer("input", "3")

    assert again["name"] == "VERSION"
    assert again["problem"] == "VERSION must match ^\\d+$"
    assert until(events, "run_finished")[-1]["status"] == "ok"


CHECKED = """\
    ## Check

    ```confirm
    Is the kettle on?
    ```

    ```sh run
    echo brewing
    ```
    """


def test_start_the_run_button_and_a_confirm_are_told_apart_by_kind(hub, tmp_path):
    """Start, ▶ and a confirm block's Done each wait for their own kind of answer, in the run's order."""
    path = write_runbook(tmp_path, CHECKED)
    events = hub.subscribe()
    hub.start(path, starter(path, block_by_block=True), resume=False)
    kinds = []

    for kind in ("start", "confirm", "block"):
        prompt = until(events, "prompt")[-1]
        kinds.append(prompt["kind"])
        hub.answer(kind, "y")

    assert kinds == ["start", "confirm", "block"]
    assert set(prompt) == {"event", "at", "kind"}
    seen = until(events, "run_finished")
    assert [event["text"] for event in seen if event["event"] == "output"] == ["brewing"]
    assert seen[-1]["status"] == "ok"


def test_stop_at_a_confirm_aborts_the_run(hub, tmp_path):
    """Any answer but y is a no, as at the terminal, so Stop can send an empty answer."""
    path = write_runbook(tmp_path, CHECKED)
    events = hub.subscribe()
    hub.start(path, starter(path, ask_to_start=False), resume=False)
    until(events, "prompt")

    hub.answer("confirm", "")

    seen = until(events, "run_finished")
    assert "block_started" not in names(seen)
    assert seen[-1]["status"] == "aborted"


def test_an_answer_of_the_wrong_kind_is_refused_and_the_prompt_keeps_waiting(hub, tmp_path):
    """A ▶ click while Start? is waiting isn't taken as Start, and a Start isn't taken as an input's value."""
    path = write_runbook(tmp_path, VERSIONED)
    events = hub.subscribe()
    hub.start(path, starter(path), resume=False)
    until(events, "prompt")

    with pytest.raises(WrongAnswer, match="waiting for an answer to input, not start"):
        hub.answer("start", "y")
    hub.answer("input", "3")
    assert until(events, "prompt")[-1]["kind"] == "start"
    with pytest.raises(WrongAnswer, match="waiting for an answer to start, not block"):
        hub.answer("block", "y")
    hub.answer("start", "y")

    assert until(events, "run_finished")[-1]["status"] == "ok"


def test_an_answer_before_any_run_is_refused(hub):
    with pytest.raises(WrongAnswer, match="no run has started"):
        hub.answer("start", "y")


def test_an_answer_when_nothing_is_waiting_is_refused(hub, tmp_path):
    """Once the run has finished, or between prompts, there's nothing for an answer to answer."""
    path = write_runbook(tmp_path, HELLO)
    hub.start(path, starter(path, ask_to_start=False), resume=False)
    assert hub.wait(timeout=TIMEOUT)

    with pytest.raises(WrongAnswer, match="nothing is waiting for an answer, so block can't be one"):
        hub.answer("block", "y")


def test_a_question_the_web_has_no_control_for_is_an_error():
    """step_by_step's [y]es / [s]kip / [q]uit has no button, so the web never runs with it."""
    with pytest.raises(ValueError, match="no control answers"):
        WebPrompter(lambda name, fields: None).confirm(runner.STEP_QUESTION)
