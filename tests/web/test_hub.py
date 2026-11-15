"""Tests for the run hub and WebPrompter, with queues and threads and no server.

Every run is the real runner.run on a small runbook in tmp_path, with blocks really run under bash. Every wait has
a timeout, so a hang fails the test instead of stalling the suite.
"""

import json
import queue
import textwrap
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from rundot import runner
from rundot.backends import load_backends
from rundot.inputs import Input, InputError
from rundot.runbook import parse
from rundot.web.hub import Busy, RunHub, WebPrompter, WrongAnswer

# Long enough for bash to start on a slow CI machine; only reached when something is wrong.
TIMEOUT = 10
AT = datetime(2026, 11, 15, 9, 30, tzinfo=timezone.utc)
# The tab that starts a run, as the page names itself.
TAB = "tab-1"


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
    events = hub.subscribe(path, tab=TAB)

    hub.start(path, starter(path, ask_to_start=False), resume=False, tab=TAB)
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
    hub.start(tmp_path / "first.md", held(release), resume=False, tab=TAB)
    started = []

    with pytest.raises(Busy, match="first.md is running") as busy:
        hub.start(tmp_path / "second.md", lambda *args: started.append(args), resume=False, tab=TAB)

    assert busy.value.path == tmp_path / "first.md"
    assert hub.running == tmp_path / "first.md"
    release.set()
    assert hub.wait(timeout=TIMEOUT)
    assert started == []


def test_a_book_can_start_again_once_its_run_has_finished(hub, tmp_path):
    """Run again: a finished run doesn't block the next, and the next starts with an empty buffer."""
    path = write_runbook(tmp_path, HELLO)
    hub.start(path, starter(path, ask_to_start=False), resume=False, tab=TAB)
    assert hub.wait(timeout=TIMEOUT)
    assert hub.running is None

    hub.start(path, starter(path, ask_to_start=False), resume=True, tab=TAB)
    assert hub.wait(timeout=TIMEOUT)

    assert names(until(hub.subscribe(path, tab=TAB), "run_finished")).count("overview") == 1


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

    early = hub.subscribe(path, tab=TAB)
    hub.start(path, start_run, resume=False, tab=TAB)
    assert paused.wait(TIMEOUT)
    late = hub.subscribe(path, tab=TAB)
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
    events = hub.subscribe(path, tab=TAB)
    hub.start(path, starter(path, ask_to_start=False), resume=False, tab=TAB)

    prompt = until(events, "prompt")[-1]
    hub.answer("input", "3", tab=TAB)

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
    events = hub.subscribe(path, tab=TAB)
    hub.start(path, starter(path, ask_to_start=False), resume=False, tab=TAB)
    until(events, "prompt")

    hub.answer("input", "three", tab=TAB)
    again = until(events, "prompt")[-1]
    hub.answer("input", "3", tab=TAB)

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
    events = hub.subscribe(path, tab=TAB)
    hub.start(path, starter(path, block_by_block=True), resume=False, tab=TAB)
    kinds = []

    for kind in ("start", "confirm", "block"):
        prompt = until(events, "prompt")[-1]
        kinds.append(prompt["kind"])
        hub.answer(kind, "y", tab=TAB)

    assert kinds == ["start", "confirm", "block"]
    assert set(prompt) == {"event", "at", "kind"}
    seen = until(events, "run_finished")
    assert [event["text"] for event in seen if event["event"] == "output"] == ["brewing"]
    assert seen[-1]["status"] == "ok"


def test_stop_at_a_confirm_aborts_the_run(hub, tmp_path):
    """Any answer but y is a no, as at the terminal, so Stop can send an empty answer."""
    path = write_runbook(tmp_path, CHECKED)
    events = hub.subscribe(path, tab=TAB)
    hub.start(path, starter(path, ask_to_start=False), resume=False, tab=TAB)
    until(events, "prompt")

    hub.answer("confirm", "", tab=TAB)

    seen = until(events, "run_finished")
    assert "block_started" not in names(seen)
    assert seen[-1]["status"] == "aborted"


def test_an_answer_of_the_wrong_kind_is_refused_and_the_prompt_keeps_waiting(hub, tmp_path):
    """A ▶ click while Start? is waiting isn't taken as Start, and a Start isn't taken as an input's value."""
    path = write_runbook(tmp_path, VERSIONED)
    events = hub.subscribe(path, tab=TAB)
    hub.start(path, starter(path), resume=False, tab=TAB)
    until(events, "prompt")

    with pytest.raises(WrongAnswer, match="waiting for an answer to input, not start"):
        hub.answer("start", "y", tab=TAB)
    hub.answer("input", "3", tab=TAB)
    assert until(events, "prompt")[-1]["kind"] == "start"
    with pytest.raises(WrongAnswer, match="waiting for an answer to start, not block"):
        hub.answer("block", "y", tab=TAB)
    hub.answer("start", "y", tab=TAB)

    assert until(events, "run_finished")[-1]["status"] == "ok"


def test_an_answer_before_any_run_is_refused(hub):
    with pytest.raises(WrongAnswer, match="no run has started"):
        hub.answer("start", "y", tab=TAB)


def test_an_answer_when_nothing_is_waiting_is_refused(hub, tmp_path):
    """Once the run has finished, or between prompts, there's nothing for an answer to answer."""
    path = write_runbook(tmp_path, HELLO)
    hub.start(path, starter(path, ask_to_start=False), resume=False, tab=TAB)
    assert hub.wait(timeout=TIMEOUT)

    with pytest.raises(WrongAnswer, match="nothing is waiting for an answer, so block can't be one"):
        hub.answer("block", "y", tab=TAB)


def test_a_question_the_web_has_no_control_for_is_an_error():
    """step_by_step's [y]es / [s]kip / [q]uit has no button, so the web never runs with it."""
    with pytest.raises(ValueError, match="no control answers"):
        WebPrompter(lambda name, fields: None).confirm(runner.STEP_QUESTION)


INPUT_BLOCK = """\
    ## Ask

    ```input
    name: NAME
    description: Who to greet
    ```

    ```sh run
    echo hello <NAME>
    ```
    """


@pytest.mark.parametrize(
    ("text", "options", "kind", "step"),
    [
        (HELLO, {}, "start", None),
        (HELLO, {"ask_to_start": False, "block_by_block": True}, "block", "Greet"),
        (CHECKED, {"ask_to_start": False}, "confirm", "Check"),
        (VERSIONED, {}, "input", None),
        (INPUT_BLOCK, {"ask_to_start": False}, "input", "Ask"),
    ],
    ids=["start", "run block", "confirm", "frontmatter input", "input block"],
)
def test_stop_while_a_prompt_is_waiting_finishes_the_run_interrupted(hub, tmp_path, text, options, kind, step):
    """Stopping the server can't leave the run's thread waiting forever: the run ends as Ctrl-C would end it."""
    path = write_runbook(tmp_path, text)
    events = hub.subscribe(path, tab=TAB)
    hub.start(path, starter(path, **options), resume=False, tab=TAB)
    assert until(events, "prompt")[-1]["kind"] == kind

    hub.stop(timeout=TIMEOUT)

    seen = until(events, "run_finished")
    assert seen[-1]["status"] == "interrupted"
    assert seen[-1]["step"] == step
    assert names(seen).count("run_finished") == 1
    assert "block_finished" not in names(seen)
    assert events.get(timeout=TIMEOUT) is None
    assert hub.running is None
    with pytest.raises(WrongAnswer):
        hub.answer(kind, "y", tab=TAB)


def test_a_prompt_after_stop_interrupts_without_being_published():
    """A run that was busy when stop came, such as running a block, finishes at its next prompt without showing it."""
    published = []
    prompter = WebPrompter(lambda name, fields: published.append(name))
    prompter.stop()

    with pytest.raises(KeyboardInterrupt):
        prompter.confirm("Start? [y/N]")
    with pytest.raises(KeyboardInterrupt):
        prompter.ask(Input("NAME", "Who to greet"), None)
    assert published == []


def test_secret_values_never_appear_in_any_published_event(hub, tmp_path):
    """Not in prompts, not when refused, not in output, and not in the code a block runs."""
    path = write_runbook(
        tmp_path,
        """\
        ---
        inputs:
          - name: TOKEN
            description: The token
            secret: true
            pattern: ^tok-.+
        ---

        ## Use them

        ```input
        name: PASSWORD
        description: The password
        secret: true
        ```

        ```sh run
        echo "token $TOKEN"
        echo "password $PASSWORD" >&2
        ```
        """,
    )
    events = hub.subscribe(path, tab=TAB)
    hub.start(path, starter(path, block_by_block=True), resume=False, tab=TAB)

    for kind, value in [
        ("input", "refused-secret"),
        ("input", "tok-first-secret"),
        ("start", "y"),
        ("input", "second-secret"),
        ("block", "y"),
    ]:
        prompt = until(events, "prompt")[-1]
        assert prompt["kind"] == kind, prompt
        hub.answer(kind, value, tab=TAB)
    seen = until(events, "run_finished")
    assert hub.wait(timeout=TIMEOUT)

    published = json.dumps(until(hub.subscribe(path, tab=TAB), "run_finished"))
    assert seen[-1]["status"] == "ok"
    assert [event["text"] for event in seen if event["event"] == "output"] == ["token ****", "password ****"]
    for value in ("refused-secret", "tok-first-secret", "second-secret"):
        assert value not in published


def empty(events: "queue.Queue[dict[str, Any] | None]") -> bool:
    """Whether nothing more is on events, waiting a moment for anything still on its way."""
    try:
        events.get(timeout=0.1)
    except queue.Empty:
        return True
    return False


def test_a_tab_on_another_book_is_told_which_book_is_busy_and_gets_none_of_its_events(hub, tmp_path):
    """One run at a time across books: another book's tab says which one is running, until it ends."""
    release = threading.Event()
    running, other = tmp_path / "first.md", tmp_path / "second.md"
    before = hub.subscribe(other)
    hub.start(running, held(release), resume=False, tab=TAB)
    during = hub.subscribe(other)

    assert before.get(timeout=TIMEOUT) == {
        "event": "busy",
        "at": AT.isoformat(timespec="milliseconds"),
        "book": "first",
    }
    assert during.get(timeout=TIMEOUT)["book"] == "first"
    release.set()
    assert hub.wait(timeout=TIMEOUT)

    for events in (before, during):
        assert events.get(timeout=TIMEOUT) == {
            "event": "busy",
            "at": AT.isoformat(timespec="milliseconds"),
            "book": None,
        }
        assert empty(events)


def test_only_the_tab_that_started_a_run_can_answer_it(hub, tmp_path):
    """Another tab's answer is refused, and the prompt keeps waiting for the tab that started the run."""
    path = write_runbook(tmp_path, CHECKED)
    events = hub.subscribe(path, tab=TAB)
    hub.start(path, starter(path), resume=False, tab=TAB)
    until(events, "prompt")

    with pytest.raises(WrongAnswer, match="only the tab that started the run can answer it"):
        hub.answer("start", "y", tab="tab-2")
    hub.answer("start", "y", tab=TAB)
    until(events, "prompt")
    hub.answer("confirm", "", tab=TAB)

    assert until(events, "run_finished")[-1]["status"] == "aborted"


def test_another_tab_on_the_running_book_is_told_it_is_busy_and_still_replays_the_run(hub, tmp_path):
    """A second tab, or one with no name, sees the run but can't click it, until the run ends. A reload of the tab
    that started it isn't told it's busy, so it carries on."""
    path = write_runbook(tmp_path, CHECKED)
    owner = hub.subscribe(path, tab=TAB)
    before = hub.subscribe(path, tab="tab-2")
    hub.start(path, starter(path), resume=False, tab=TAB)
    until(owner, "prompt")
    during, unnamed, reloaded = (hub.subscribe(path, tab=tab) for tab in ("tab-3", None, TAB))

    replayed = until(reloaded, "prompt")
    busy = {"event": "busy", "at": AT.isoformat(timespec="milliseconds"), "book": "runbook"}
    assert "busy" not in names(replayed)
    for events in (before, during, unnamed):
        assert until(events, "prompt") == [busy, *replayed]
    hub.answer("start", "y", tab=TAB)
    until(owner, "prompt")
    hub.answer("confirm", "", tab=TAB)
    assert hub.wait(timeout=TIMEOUT)

    finished = until(reloaded, "run_finished")[-1]
    for events in (before, during, unnamed):
        assert until(events, "busy")[-2:] == [finished, {**busy, "book": None}]
        assert empty(events)
    assert until(owner, "run_finished")[-1] == finished
    assert empty(owner)
    assert empty(reloaded)


def test_a_finished_run_is_replayed_only_to_its_own_book(hub, tmp_path):
    """Another book's tab gets none of a run's events, live or after it has finished."""
    path = write_runbook(tmp_path, HELLO)
    live = hub.subscribe(tmp_path / "other.md")
    hub.start(path, starter(path, ask_to_start=False), resume=False, tab=TAB)
    assert hub.wait(timeout=TIMEOUT)

    assert [event["book"] for event in (live.get(timeout=TIMEOUT), live.get(timeout=TIMEOUT))] == ["runbook", None]
    assert empty(live)
    assert empty(hub.subscribe(tmp_path / "other.md"))
    assert until(hub.subscribe(path, tab=TAB), "run_finished")[-1]["status"] == "ok"


def test_a_book_with_a_run_to_resume_is_told_so_when_nothing_is_running(hub, tmp_path):
    """The resume banner's text comes first, and only to a tab with no run of its book to replay."""
    path = write_runbook(tmp_path, HELLO)

    events = hub.subscribe(path, resume="Stopped at step 1")

    assert events.get(timeout=TIMEOUT) == {
        "event": "resume",
        "at": AT.isoformat(timespec="milliseconds"),
        "message": "Stopped at step 1",
    }
    assert empty(events)
    assert empty(hub.subscribe(path, resume=None))


def test_a_book_with_a_run_to_replay_or_another_book_running_gets_no_resume_event(hub, tmp_path):
    """A replayed run already says how it ended, and a busy server can't resume anything."""
    path = write_runbook(tmp_path, HELLO)
    hub.start(path, starter(path, ask_to_start=False), resume=False, tab=TAB)
    assert hub.wait(timeout=TIMEOUT)
    assert names(until(hub.subscribe(path, resume="Stopped"), "run_finished"))[0] == "overview"

    release = threading.Event()
    hub.start(tmp_path / "first.md", held(release), resume=False, tab=TAB)
    assert hub.subscribe(path, resume="Stopped").get(timeout=TIMEOUT)["event"] == "busy"
    release.set()


def test_an_unsubscribed_queue_gets_nothing_more(hub, tmp_path):
    """A closed tab's queue is let go, so it doesn't fill up with events nobody reads."""
    path = write_runbook(tmp_path, HELLO)
    events = hub.subscribe(path, tab=TAB)

    hub.unsubscribe(events)
    hub.unsubscribe(events)
    hub.start(path, starter(path, ask_to_start=False), resume=False, tab=TAB)
    assert hub.wait(timeout=TIMEOUT)
    hub.stop(timeout=TIMEOUT)

    assert empty(events)


def failing(error: Exception):
    def start_run(prompter, sink, resume):
        raise error

    return start_run


def test_a_run_that_cant_start_sends_its_error_then_finishes_failed(hub, tmp_path):
    """A RunnerError or InputError, which the terminal prints and exits 1 for, is shown in the page instead."""
    path = tmp_path / "broken.md"
    for error in (runner.RunnerError("line 3: no backend runs 'ruby' blocks"), InputError("NAME from the last run …")):
        hub.start(path, failing(error), resume=False, tab=TAB)
        events = hub.subscribe(path, tab=TAB)

        seen = until(events, "run_finished")
        assert hub.wait(timeout=TIMEOUT)
        assert seen[0] == {"event": "error", "at": AT.isoformat(timespec="milliseconds"), "message": str(error)}
        assert seen[1]["status"] == "failed"
        assert len(seen) == 2


def test_a_bug_in_a_run_is_shown_in_the_page_and_still_raised(hub, tmp_path, monkeypatch):
    """Anything else is a bug: the page is told, and its traceback still reaches the terminal."""
    raised = []
    monkeypatch.setattr(threading, "excepthook", lambda args: raised.append(args.exc_value))
    path = tmp_path / "buggy.md"
    events = hub.subscribe(path, tab=TAB)
    bug = ZeroDivisionError("division by zero")

    hub.start(path, failing(bug), resume=False, tab=TAB)

    assert names(until(events, "run_finished")) == ["error", "run_finished"]
    assert hub.wait(timeout=TIMEOUT)
    assert raised == [bug]
