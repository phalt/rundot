"""rundot web end to end: the server the CLI builds, serving on 127.0.0.1 port 0, driven by http.client.

Runs go through the CLI's start_run and resume_info, so they write the same transcripts and saved state as
rundot run. No browser. Every thread a test starts is joined with a timeout, and the server is stopped after
each test.
"""

import json
import os
import re
import shutil
import textwrap
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from rundot import cli, state
from rundot.runbook import parse
from rundot.web.server import WebServer
from tests.web.client import TIMEOUT, Client, drive

RUNBOOKS = Path(__file__).parent.parent / "runbooks"

# Each run adds one line to count, so a test can tell whether its first step ran again.
FAILS = """\
    ---
    title: Fails until fixed
    ---

    ## Count

    ```sh run
    echo once >> count
    ```

    ## Check

    ```sh run
    test -f fixed
    ```
    """


class Served:
    """rundot web's server for a runbook, serving in a thread, and a client with its cookie."""

    def __init__(self, web: WebServer, interrupted: threading.Event) -> None:
        self.web = web
        self.interrupted = interrupted
        self.client = Client(web.url)
        assert web.opened is not None
        self.book = web.opened


@pytest.fixture
def serve() -> Iterator[Callable[[Path], Served]]:
    """Serve a runbook as rundot web does. The server is stopped, and its threads joined, after the test."""
    running: list[tuple[WebServer, threading.Thread]] = []

    def serve(path: Path) -> Served:
        web, interrupted = cli._web_server(parse(path), port=0)
        thread = threading.Thread(target=web.serve_forever, name="rundot web end to end")
        thread.start()
        running.append((web, thread))
        return Served(web, interrupted)

    yield serve

    for web, thread in running:
        web.hub.stop(timeout=TIMEOUT)
        closing = threading.Thread(target=web.close, name="rundot web end to end close")
        closing.start()
        closing.join(TIMEOUT)
        thread.join(TIMEOUT)
        assert not closing.is_alive(), "a request's thread didn't end when the server stopped"
        assert not thread.is_alive(), "the server didn't stop"


@pytest.fixture
def runner(monkeypatch) -> CliRunner:
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    return CliRunner()


def write(directory: Path, text: str, name: str = "runbook.md") -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(text))
    return path


def saved(path: Path, cwd: Path) -> dict[str, Any]:
    """The run state rundot saved for path, run in cwd."""
    return json.loads(state.state_path(os.environ, path, cwd).read_text())


def run(served: Served, **options: Any) -> list[dict[str, Any]]:
    """drive a run of the served book, then wait for its thread to end, once its state and transcript are saved."""
    events = drive(served.client, served.book, **options)
    assert served.web.hub.wait(TIMEOUT), "the run's thread didn't end"
    return events


def finished(events: list[dict[str, Any]]) -> dict[str, Any]:
    assert events[-1]["event"] == "run_finished"
    return events[-1]


def without_times(transcript: str) -> str:
    """A transcript with when each run started, and how long each block and run took, made the same."""
    transcript = re.sub(r"^--- rundot (.*) @ \S+$", r"--- rundot \1 @ TIME", transcript, flags=re.MULTILINE)
    return re.sub(r"\((\d+m)?\d+(\.\d)?s\)", "(Xs)", transcript)


def test_a_web_run_writes_the_same_transcript_as_rundot_run(serve, runner, tmp_path, started_in, monkeypatch):
    monkeypatch.setenv("VERSION", "1.2.0")
    monkeypatch.setenv("PUBLISH_TOKEN", "s3cret")
    path = Path(shutil.copy(RUNBOOKS / "transcript-example.md", tmp_path))
    transcript = started_in / "logs" / "release-1.2.0.md"
    ran = runner.invoke(cli.cli, ["run", str(path)], input="y\ny\n")
    assert ran.exit_code == 0, ran.output
    from_the_terminal = transcript.read_text()
    transcript.unlink()

    served = serve(path)
    events = run(served)

    assert finished(events)["status"] == "ok"
    from_the_web = transcript.read_text()
    assert without_times(from_the_web) == without_times(from_the_terminal)
    assert "s3cret" not in from_the_web
    assert "Publishing 1.2.0 with ****" in from_the_web


def test_a_run_that_failed_in_the_web_resumes_with_rundot_run(serve, runner, tmp_path, started_in):
    path = write(tmp_path, FAILS)
    served = serve(path)
    events = run(served)
    assert finished(events)["status"] == "failed"
    assert saved(path, started_in)["stopped"] == "failed"
    (started_in / "fixed").touch()

    result = runner.invoke(cli.cli, ["run", str(path)], input="r\ny\n")

    assert result.exit_code == 0, result.output
    assert "Last run stopped at 2/2 Check: failed (exit 1)" in result.output
    assert (started_in / "count").read_text() == "once\n"


def test_a_run_that_failed_with_rundot_run_shows_the_resume_banner_and_resumes_in_the_web(
    serve, runner, tmp_path, started_in
):
    path = write(tmp_path, FAILS)
    assert runner.invoke(cli.cli, ["run", str(path)], input="y\n").exit_code == 1
    served = serve(path)

    stream = served.client.events(served.book)
    banner = stream.next()
    stream.close()

    assert banner["event"] == "resume"
    assert re.fullmatch(
        r"Last run stopped at 2/2 Check: failed \(exit 1\), \d{4}-\d\d-\d\d \d\d:\d\d\.", banner["message"]
    )
    (started_in / "fixed").touch()
    events = run(served, resume=True)
    assert events[0]["event"] == "overview" and events[0]["resuming_at"] == 1
    assert finished(events)["status"] == "ok"
    assert (started_in / "count").read_text() == "once\n"
    assert not state.state_path(os.environ, path, started_in).exists()


def test_the_resume_banner_says_what_has_changed_since_the_run_stopped(serve, runner, tmp_path):
    path = write(tmp_path, FAILS)
    runner.invoke(cli.cli, ["run", str(path)], input="y\n")
    path.write_text(path.read_text().replace("echo once", "echo twice"))
    served = serve(path)

    stream = served.client.events(served.book)
    banner = stream.next()
    stream.close()

    assert banner["message"].endswith(' runbook.md has changed since that run: "Count" is different.')


def test_starting_over_in_the_web_forgets_the_stopped_run_as_restart_does(serve, runner, tmp_path, started_in):
    path = write(tmp_path, FAILS)
    runner.invoke(cli.cli, ["run", str(path)], input="y\n")
    (started_in / "fixed").touch()
    served = serve(path)

    events = run(served, resume=False)

    assert events[0]["resuming_at"] is None
    assert finished(events)["status"] == "ok"
    assert (started_in / "count").read_text() == "once\nonce\n"


def test_there_is_no_resume_banner_without_a_stopped_run(serve, tmp_path):
    path = write(tmp_path, FAILS)
    served = serve(path)

    assert cli._resume_info(path) is None
    events = run(served)
    assert events[0]["event"] == "overview"


def test_there_is_no_resume_banner_for_a_runbook_now_shorter_than_the_stopped_run(runner, tmp_path):
    path = write(tmp_path, FAILS)
    runner.invoke(cli.cli, ["run", str(path)], input="y\n")
    write(tmp_path, "---\ntitle: Short\n---\n\n## Only\n\n```sh run\ntrue\n```\n")

    assert cli._resume_info(path) is None


def test_there_is_no_resume_banner_for_a_runbook_that_cant_be_read(runner, tmp_path):
    path = write(tmp_path, FAILS)
    runner.invoke(cli.cli, ["run", str(path)], input="y\n")
    path.write_bytes(b"---\ntitle: [\n")

    assert cli._resume_info(path) is None


def test_resuming_in_the_web_with_nothing_to_resume_says_so_on_the_page(serve, tmp_path, started_in):
    path = write(tmp_path, FAILS)
    served = serve(path)

    events = run(served, resume=True)

    assert [event["event"] for event in events] == ["error", "run_finished"]
    assert events[0]["message"] == f"nothing to resume for runbook.md in {started_in}"


def test_a_book_from_the_library_with_errors_says_why_on_the_page(serve, tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    write(library, "---\ntitle: Broken\n---\n\n## Go\n\n```ruby run\nputs 1\n```\n", "broken.md")
    monkeypatch.setenv("RUNDOT_LIBRARY", str(library))
    served = serve(write(tmp_path, FAILS))

    events = drive(served.client, "broken")
    assert served.web.hub.wait(TIMEOUT)

    assert events[0]["event"] == "error"
    assert events[0]["message"].startswith("broken.md can't be run: line 7: ")
    assert finished(events)["status"] == "failed"


def test_a_book_from_the_library_that_cant_be_read_says_why_on_the_page(serve, tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    (library / "unreadable.md").write_bytes(b"---\ntitle: [\n")
    monkeypatch.setenv("RUNDOT_LIBRARY", str(library))
    served = serve(write(tmp_path, FAILS))

    events = drive(served.client, "unreadable")
    assert served.web.hub.wait(TIMEOUT)

    assert events[0]["event"] == "error"
    assert "frontmatter has no closing ---" in events[0]["message"]


def test_a_transcript_that_cant_be_written_says_why_on_the_page(serve, tmp_path, started_in):
    (started_in / "logs").write_text("not a directory")
    path = write(tmp_path, "---\ntitle: T\noutput_path: logs/run.md\n---\n\n## Go\n\n```sh run\ntrue\n```\n")
    served = serve(path)

    events = run(served)

    assert [event["event"] for event in events][-2:] == ["error", "run_finished"]
    assert events[-2]["message"].startswith(f"can't write the transcript to {started_in / 'logs' / 'run.md'}: ")


# Ctrl-C, in process: the main thread sets interrupted, as rundot web does when Ctrl-C reaches it.


def test_ctrl_c_while_a_block_prints_finishes_the_run_as_interrupted_once_it_stops(serve, tmp_path, started_in):
    path = write(
        tmp_path,
        """\
        ---
        title: Slow
        ---

        ## Slow

        ```sh run
        while [ ! -f go ]; do sleep 0.02; done
        echo stopping cleanly
        echo still stopping
        ```

        ## Never reached

        ```sh run
        touch later-ran
        ```
        """,
    )
    served = serve(path)

    def press_ctrl_c(event: dict[str, Any]) -> None:
        if event["event"] == "block_started":
            served.interrupted.set()
            (started_in / "go").touch()

    events = run(served, seen=press_ctrl_c)

    output = [event["text"] for event in events if event["event"] == "output"]
    assert output == ["stopping cleanly", "still stopping"]
    assert [event["exit_code"] for event in events if event["event"] == "block_finished"] == [0]
    assert finished(events)["status"] == "interrupted"
    assert finished(events)["step"] == "Slow"
    assert saved(path, started_in)["stopped"] == "interrupted"
    assert not (started_in / "later-ran").exists()


def test_ctrl_c_while_a_quiet_block_runs_finishes_the_run_as_interrupted_when_it_ends(serve, tmp_path, started_in):
    path = write(
        tmp_path,
        """\
        ---
        title: Quiet
        ---

        ## Quiet

        ```sh run
        while [ ! -f go ]; do sleep 0.02; done
        exit 130
        ```
        """,
    )
    served = serve(path)

    def press_ctrl_c(event: dict[str, Any]) -> None:
        if event["event"] == "block_started":
            served.interrupted.set()
            (started_in / "go").touch()

    events = run(served, seen=press_ctrl_c)

    assert [event["exit_code"] for event in events if event["event"] == "block_finished"] == [130]
    assert finished(events)["status"] == "interrupted"
    assert saved(path, started_in)["stopped"] == "interrupted"


# Filling in placeholders. The page sets each block's text from these events, so a reload or a second tab, which
# get them replayed, show the same.

FILLED = """\
    ---
    title: Filled in
    inputs:
      - name: VERSION
        description: The version
      - name: TOKEN
        description: A token
        secret: true
    ---

    ## Release

    ```confirm
    Tag <VERSION>, and never print s3cret.
    ```

    ```input
    name: PR
    description: The pull request number
    ```

    ```input
    name: PIN
    description: A pin
    secret: true
    ```

    ```sh run
    echo "<VERSION> <PR> $PIN"
    ```
    """


def test_every_block_is_filled_in_with_plain_inputs_at_start_and_after_an_input_block(
    serve, tmp_path, started_in, monkeypatch
):
    monkeypatch.setenv("VERSION", "1.2.0")
    monkeypatch.setenv("TOKEN", "s3cret")
    monkeypatch.setenv("PR", "42")
    monkeypatch.setenv("PIN", "p1n-value")
    served = serve(write(tmp_path, FILLED))

    events = run(served)

    [overview] = [event for event in events if event["event"] == "overview"]
    assert list(overview["blocks"].values()) == [
        "Tag 1.2.0, and never print ****.\n",
        'echo "1.2.0 <PR> $PIN"\n',
    ]
    pr, pin = [event for event in events if event["event"] == "input_collected"]
    assert list(pr["blocks"].values()) == ['echo "1.2.0 42 $PIN"\n']
    assert pin["blocks"] == {}


def test_no_secret_value_reaches_the_browser_in_the_filled_in_blocks(serve, tmp_path, started_in, monkeypatch):
    monkeypatch.setenv("VERSION", "1.2.0")
    monkeypatch.setenv("TOKEN", "s3cret")
    monkeypatch.setenv("PR", "42")
    monkeypatch.setenv("PIN", "p1n-value")
    served = serve(write(tmp_path, FILLED))
    run(served)

    # A second tab gets the whole run replayed, as the raw stream the browser reads.
    response = served.client.open("GET", f"/book/{served.book}/events?tab=another")
    replayed = b""
    while b'"run_finished"' not in replayed:
        line = response.readline()
        assert line, "the stream ended"
        replayed += line
    response.close()

    assert b'"blocks"' in replayed
    assert b"s3cret" not in replayed
    assert b"p1n-value" not in replayed
