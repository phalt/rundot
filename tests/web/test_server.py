"""Tests for the rundot web server: a real server on 127.0.0.1 port 0 in a thread, driven by http.client.

No browser. Every thread a test starts is joined with a timeout, and the server is shut down after each test, so a
hang fails the test instead of stalling the suite.
"""

import http.client
import json
import os
import queue
import textwrap
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from rundot import runner
from rundot.backends import load_backends
from rundot.library import Book
from rundot.runbook import parse
from rundot.web import server as server_module
from rundot.web.hub import RunHub
from rundot.web.server import WebServer

# Long enough for bash to start on a slow CI machine; only reached when something is wrong.
TIMEOUT = 10

HELLO = """\
    ---
    title: Say <b>hello</b>
    description: Who to say hello to
    ---

    A paragraph with <script>alert(1)</script> raw HTML.

    ## Greet

    ```sh run
    echo hello
    ```
    """

OTHER = """\
    ## Wait

    ```confirm
    Is the kettle on?
    ```
    """


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


def starter(path: Path):
    """The CLI's start_run, for the book at path: runs it for real, asking before each block as the web does."""

    def start_run(prompter, sink, resume):
        return runner.run(parse(path), load_backends(), sink, prompter, cwd=path.parent, block_by_block=True)

    return start_run


class Running:
    """A server running in a thread, and what it was given."""

    def __init__(self, web: WebServer, hub: RunHub, started: list[tuple[Path, bool]]) -> None:
        self.web = web
        self.hub = hub
        self.started = started  # every (path, resume) start_run was made for, in order
        self.host = f"127.0.0.1:{web.port}"
        self.cookie = f"{web.cookie_name}={web.token}"

    def request(
        self,
        method: str,
        path: str,
        body: bytes | str | None = None,
        *,
        cookie: bool = True,
        host: str | None = None,
        origin: str | None = "",
        headers: dict[str, str] | None = None,
    ) -> tuple[http.client.HTTPResponse, bytes]:
        """Make a request and read its whole response. POSTs come from the server's own origin unless told not to."""
        response = self.open(method, path, body, cookie=cookie, host=host, origin=origin, headers=headers)
        return response, response.read()

    def open(
        self,
        method: str,
        path: str,
        body: bytes | str | None = None,
        *,
        cookie: bool = True,
        host: str | None = None,
        origin: str | None = "",
        headers: dict[str, str] | None = None,
    ) -> http.client.HTTPResponse:
        """Make a request and return its response unread, for a stream."""
        connection = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=TIMEOUT)
        sent = {"Host": host or self.host, **(headers or {})}
        if cookie:
            sent["Cookie"] = self.cookie
        if method == "POST" and origin is not None:
            sent["Origin"] = origin or f"http://{self.host}"
        connection.request(method, path, body=body, headers=sent)
        return connection.getresponse()

    def post(self, path: str, value: Any) -> tuple[http.client.HTTPResponse, bytes]:
        return self.request("POST", path, json.dumps(value))


@pytest.fixture
def library(tmp_path) -> Path:
    library = tmp_path / "library"
    write(library / "hello.md", HELLO)
    write(library / "other.md", OTHER)
    return library


@pytest.fixture
def static(tmp_path) -> Path:
    write(tmp_path / "static" / "rundot.css", "body { color: green; }\n")
    return tmp_path / "static"


@pytest.fixture
def serve(library, static) -> Iterator[Callable[..., Running]]:
    """Start a server on port 0 in a thread; it's stopped, and every thread it started joined, after the test."""
    running: list[tuple[WebServer, RunHub, threading.Thread]] = []

    def serve(
        *,
        books: Callable[[], list[Book]] | None = None,
        opened: str | None = None,
        resume_info: Callable[[Path], str | None] = lambda path: None,
        start_run: Callable[[Path], Any] | None = None,
    ) -> Running:
        hub = RunHub()
        started: list[tuple[Path, bool]] = []

        def make_start_run(path: Path):
            run = (start_run or starter)(path)

            def recorded(prompter, sink, resume):
                started.append((path, resume))
                return run(prompter, sink, resume)

            return recorded

        def library_books() -> list[Book]:
            return [
                Book("hello", library / "hello.md", "Say <b>hello</b>", "Who to say hello to"),
                Book("other", library / "other.md", None, None),
            ]

        web = WebServer(
            hub,
            books or library_books,
            make_start_run,
            resume_info,
            library=library,
            opened=opened,
            static=static,
        )
        thread = threading.Thread(target=web.serve_forever, name="rundot web test server")
        thread.start()
        running.append((web, hub, thread))
        return Running(web, hub, started)

    yield serve

    for web, hub, thread in running:
        hub.stop(timeout=TIMEOUT)
        # close joins every request's thread, so it's run on its own thread to put a timeout on that.
        closing = threading.Thread(target=web.close, name="rundot web test close")
        closing.start()
        closing.join(TIMEOUT)
        thread.join(TIMEOUT)
        assert not closing.is_alive(), "a request's thread didn't end when the server stopped"
        assert not thread.is_alive(), "the server didn't stop"


# The token


def test_the_server_binds_to_127_0_0_1_on_the_port_the_os_picks(serve):
    running = serve()

    assert running.web.address == ("127.0.0.1", running.web.port)
    assert running.web.port > 0
    assert running.web.url == f"http://127.0.0.1:{running.web.port}/?t={running.web.token}"
    assert len(running.web.token) >= 43  # secrets.token_urlsafe(32)


def test_each_server_makes_its_own_token(serve):
    assert serve().web.token != serve().web.token


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/"),
        ("GET", "/book/hello"),
        ("GET", "/book/hello/events"),
        ("POST", "/book/hello/answer"),
        ("POST", "/book/hello/run"),
        ("GET", "/static/rundot.css"),
        ("GET", "/book/hello/file/hello.md"),
        ("GET", "/no/such/path"),
    ],
)
def test_without_the_cookie_every_route_is_403(serve, method, path):
    running = serve()

    response, body = running.request(method, path, "{}" if method == "POST" else None, cookie=False)

    assert response.status == 403
    assert b"hello" not in body


def test_the_right_token_sets_the_cookie_and_redirects_without_it(serve):
    running = serve()

    response, _ = running.request("GET", f"/?t={running.web.token}", cookie=False)

    assert response.status == 303
    assert response.getheader("Location") == "/"
    cookie = response.getheader("Set-Cookie")
    assert cookie is not None
    assert cookie.startswith(f"{running.web.cookie_name}={running.web.token};")
    attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert {"httponly", "samesite=strict", "path=/"} <= attributes


def test_the_token_works_on_any_page_and_keeps_the_rest_of_the_query(serve):
    running = serve()

    response, _ = running.request("GET", f"/book/hello?a=1&t={running.web.token}", cookie=False)

    assert response.status == 303
    assert response.getheader("Location") == "/book/hello?a=1"


@pytest.mark.parametrize("token", ["wrong", "", "x" * 43])
def test_a_wrong_token_sets_nothing(serve, token):
    running = serve()

    response, _ = running.request("GET", f"/?t={token}", cookie=False)

    assert response.status == 403
    assert response.getheader("Set-Cookie") is None


@pytest.mark.parametrize("cookie", ["{name}=wrong", "{name}=", "{name}", "", "not a cookie", "other={token}"])
def test_a_wrong_cookie_is_403(serve, cookie):
    running = serve()
    cookie = cookie.format(name=running.web.cookie_name, token=running.web.token)

    response, _ = running.request("GET", "/", cookie=False, headers={"Cookie": cookie})

    assert response.status == 403


@pytest.mark.parametrize("others", ["domain=x; {cookie}", "a=1; {cookie}; b=2", 'expires="x; {cookie}'])
def test_the_cookie_is_found_among_other_localhost_servers_cookies(serve, others):
    """Other servers on localhost share the browser's cookies for it, whatever they've called theirs."""
    running = serve()

    response, _ = running.request("GET", "/", cookie=False, headers={"Cookie": others.format(cookie=running.cookie)})

    assert response.status == 200


def test_the_cookie_is_named_for_the_port_so_two_servers_dont_share_one(serve):
    """Cookies don't keep to a port, so a second rundot web must not overwrite the first's."""
    first, second = serve(), serve()

    assert first.web.cookie_name == f"rundot-{first.web.port}"
    assert first.web.cookie_name != second.web.cookie_name
    response, _ = second.request("GET", "/", cookie=False, headers={"Cookie": first.cookie})
    assert response.status == 403


# Host and Origin


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_the_servers_own_hosts_are_allowed(serve, host):
    running = serve()

    response, _ = running.request("GET", "/", host=f"{host}:{running.web.port}")

    assert response.status == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1", "127.0.0.1:1", "localhost"])
def test_a_foreign_host_is_refused(serve, host):
    """A page on a DNS-rebound name can't reach the server, even with the cookie."""
    running = serve()

    response, _ = running.request("GET", "/", host=host.format(port=running.web.port))

    assert response.status == 403


@pytest.mark.parametrize("origin", ["http://evil.example", "null", "https://127.0.0.1:{port}", "http://127.0.0.1:1"])
def test_a_post_from_a_foreign_origin_is_refused(serve, origin):
    """Another site open in the browser can't click ▶."""
    running = serve()

    response, _ = running.request(
        "POST", "/book/hello/run", json.dumps({"resume": False}), origin=origin.format(port=running.web.port)
    )

    assert response.status == 403
    assert running.started == []


def test_a_post_with_no_origin_is_refused(serve):
    running = serve()

    response, _ = running.request("POST", "/book/hello/run", json.dumps({"resume": False}), origin=None)

    assert response.status == 403
    assert running.started == []


def test_a_post_from_localhost_is_allowed(serve):
    running = serve()

    response, _ = running.request(
        "POST", "/book/hello/run", json.dumps({"resume": False}), origin=f"http://localhost:{running.web.port}"
    )

    assert response.status == 204


# Pages


@pytest.mark.parametrize("path", ["/", "/book/hello", "/no/such/path"])
def test_every_response_has_the_content_security_policy(serve, path):
    running = serve()

    response, _ = running.request("GET", path)

    assert response.getheader("Content-Security-Policy") == "default-src 'self'"


def test_a_refusal_has_the_content_security_policy_too(serve):
    response, _ = serve().request("GET", "/", cookie=False)

    assert response.getheader("Content-Security-Policy") == "default-src 'self'"


def test_the_index_lists_every_book_with_a_link_to_it(serve):
    response, body = serve().request("GET", "/")

    page = body.decode()
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert '<a class="rd-book-link" href="/book/hello">Say &lt;b&gt;hello&lt;/b&gt;</a>' in page
    assert "Who to say hello to" in page
    assert '<a class="rd-book-link" href="/book/other">other</a>' in page
    assert "running" not in page


def test_an_empty_library_says_so_and_where_it_is(serve, library):
    _, body = serve(books=lambda: []).request("GET", "/")

    assert f"no books in the library at {library}" in body.decode()


def test_the_index_marks_the_book_thats_running(serve):
    running = serve()
    running.post("/book/other/run", {"resume": False})

    _, body = running.request("GET", "/")

    assert '<a class="rd-book-link" href="/book/other">other</a>' in body.decode()
    assert body.decode().count('<span class="rd-pill rd-running">running</span>') == 1


def test_the_index_is_the_library_even_with_a_book_opened(serve):
    response, body = serve(opened="hello").request("GET", "/")

    assert response.status == 200
    assert '<a class="rd-book-link" href="/book/other">other</a>' in body.decode()


def test_the_url_to_open_goes_straight_to_the_book_opened(serve):
    running = serve(opened="hello")

    assert running.web.url == f"http://127.0.0.1:{running.web.port}/book/hello?t={running.web.token}"
    response, _ = running.request("GET", f"/book/hello?t={running.web.token}", cookie=False)
    assert response.status == 303
    assert response.getheader("Location") == "/book/hello"


def test_a_book_page_has_its_title_description_library_link_and_sanitised_body(serve):
    response, body = serve().request("GET", "/book/hello")

    page = body.decode()
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert '<h1 class="rd-title">Say &lt;b&gt;hello&lt;/b&gt;</h1>' in page
    assert "Who to say hello to" in page
    assert '<a class="rd-nav" href="/">← Library</a>' in page
    assert 'class="rd-block" data-kind="run"' in page
    assert "alert(1)" not in page
    assert "title:" not in page


def test_a_book_with_no_title_is_headed_by_its_name(serve):
    _, body = serve().request("GET", "/book/other")

    assert '<h1 class="rd-title">other</h1>' in body.decode()


def test_a_book_that_cant_be_read_says_why(serve, library):
    (library / "hello.md").write_bytes(b"---\ntitle: [\n")

    response, body = serve().request("GET", "/book/hello")

    assert response.status == 200
    assert "frontmatter has no closing ---" in body.decode()


def test_a_book_whose_file_has_gone_says_why(serve, library):
    (library / "hello.md").unlink()

    response, body = serve().request("GET", "/book/hello")

    assert response.status == 200
    assert "No such file" in body.decode()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/book/nope"),
        ("GET", "/book/nope/events"),
        ("POST", "/book/nope/run"),
        ("POST", "/book/nope/answer"),
        ("GET", "/book/nope/file/hello.md"),
        ("GET", "/book/hello/nothing"),
        ("GET", "/book/hello/answer"),
        ("POST", "/book/hello"),
        ("POST", "/"),
        ("GET", "/book"),
        ("GET", "/book/"),
        ("GET", "/nothing"),
        ("GET", "/static"),
    ],
)
def test_an_unknown_book_or_path_is_404(serve, method, path):
    response, _ = serve().request(method, path, "{}" if method == "POST" else None)

    assert response.status == 404


def test_the_url_of_the_book_opened_is_url_encoded(serve):
    running = serve(opened="two words")

    assert running.web.url == f"http://127.0.0.1:{running.web.port}/book/two%20words?t={running.web.token}"


def test_a_book_name_is_url_decoded(serve, library):
    write(library / "two words.md", OTHER)
    books = [Book("two words", library / "two words.md", None, None)]

    response, body = serve(books=lambda: books).request("GET", "/book/two%20words")

    assert response.status == 200
    assert '<h1 class="rd-title">two words</h1>' in body.decode()


# Files


def test_static_files_are_served_from_the_package_directory(serve):
    response, body = serve().request("GET", "/static/rundot.css")

    assert response.status == 200
    assert response.getheader("Content-Type") == "text/css"
    assert body == b"body { color: green; }\n"


def test_the_packaged_static_directory_is_the_default():
    assert server_module.STATIC == Path(server_module.__file__).parent / "static"


def test_a_static_directory_that_doesnt_exist_yet_serves_nothing(serve, static, tmp_path):
    (static / "rundot.css").unlink()
    static.rmdir()

    response, _ = serve().request("GET", "/static/rundot.css")

    assert response.status == 404


def test_a_runbooks_relative_files_are_served_from_its_directory(serve, library):
    (library / "images").mkdir()
    (library / "images" / "logo.png").write_bytes(b"\x89PNG")
    (library / "notes.unknownkind").write_bytes(b"?")

    running = serve()
    response, body = running.request("GET", "/book/hello/file/images/logo.png")
    unknown, _ = running.request("GET", "/book/hello/file/notes.unknownkind")

    assert response.status == 200
    assert response.getheader("Content-Type") == "image/png"
    assert body == b"\x89PNG"
    assert unknown.getheader("Content-Type") == "application/octet-stream"


def test_a_file_name_is_url_decoded(serve, library):
    (library / "a picture.png").write_bytes(b"\x89PNG")

    response, _ = serve().request("GET", "/book/hello/file/a%20picture.png")

    assert response.status == 200


@pytest.mark.parametrize(
    "path",
    [
        "/book/hello/file/../secret.txt",
        "/book/hello/file/images/../../secret.txt",
        "/book/hello/file/%2e%2e/secret.txt",
        "/book/hello/file/..%2fsecret.txt",
        "/book/hello/file//etc/passwd",
        "/book/hello/file/%2fetc%2fpasswd",
        "/static/../library/hello.md",
    ],
)
def test_paths_that_leave_the_directory_are_refused(serve, tmp_path, path):
    (tmp_path / "secret.txt").write_text("secret")

    response, body = serve().request("GET", path)

    assert response.status == 403
    assert b"secret" not in body.replace(b"secret.txt", b"")


def test_a_symlink_that_leaves_the_directory_is_refused(serve, library, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    (library / "escape.txt").symlink_to(tmp_path / "secret.txt")
    (library / "outside").symlink_to(tmp_path, target_is_directory=True)

    running = serve()
    for path in ("/book/hello/file/escape.txt", "/book/hello/file/outside/secret.txt"):
        response, body = running.request("GET", path)
        assert response.status == 403
        assert body != b"secret"


def test_a_symlink_that_stays_inside_the_directory_is_served(serve, library):
    (library / "same.md").symlink_to(library / "hello.md")

    response, body = serve().request("GET", "/book/hello/file/same.md")

    assert response.status == 200
    assert body == (library / "hello.md").read_bytes()


@pytest.mark.parametrize("name", ["missing.png", "images", "broken"])
def test_a_missing_file_or_a_directory_is_404(serve, library, name):
    (library / "images").mkdir()
    (library / "broken").symlink_to(library / "nowhere")

    response, _ = serve().request("GET", f"/book/hello/file/{name}")

    assert response.status == 404


def test_a_file_that_cant_be_read_is_404(serve, library):
    unreadable = library / "locked.txt"
    unreadable.write_text("locked")
    unreadable.chmod(0)
    if os.access(unreadable, os.R_OK):
        pytest.skip("running as root, which can read any file")

    response, _ = serve().request("GET", "/book/hello/file/locked.txt")

    assert response.status == 404


# Events


def sse(response: http.client.HTTPResponse) -> Iterator[dict[str, Any]]:
    """Each event an SSE response sends, until it ends."""
    assert response.status == 200
    while line := response.readline():
        if line.startswith(b"data: "):
            yield json.loads(line.removeprefix(b"data: "))


def until(events: Iterator[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    seen = []
    for event in events:
        seen.append(event)
        if event["event"] == name:
            return seen
    raise AssertionError(f"the stream ended before {name}: {seen}")


def test_events_replay_the_run_so_far_then_stream_it_live(serve, library):
    running = serve()
    assert running.post("/book/hello/run", {"resume": False})[0].status == 204
    path = library / "hello.md"
    from_the_hub = running.hub.subscribe(path)

    stream = running.open("GET", "/book/hello/events")
    assert stream.getheader("Content-Type") == "text/event-stream"
    assert stream.getheader("Cache-Control") == "no-store"
    events = sse(stream)
    seen = until(events, "prompt")
    assert seen[-1]["kind"] == "start"
    assert running.post("/book/hello/answer", {"kind": "start", "value": "y"})[0].status == 204
    seen += until(events, "prompt")
    assert seen[-1]["kind"] == "block"
    assert running.post("/book/hello/answer", {"kind": "block", "value": "y"})[0].status == 204
    seen += until(events, "run_finished")

    hub_events = []
    while (event := from_the_hub.get(timeout=TIMEOUT))["event"] != "run_finished":
        hub_events.append(event)
    assert seen == [*hub_events, event]
    assert [event["text"] for event in seen if event["event"] == "output"] == ["hello"]
    assert seen[-1]["status"] == "ok"


def test_each_event_is_one_data_line_of_the_hubs_json_exactly(serve, library):
    """No event: or id: fields, and no reformatting: the page reads the same objects as --json."""
    running = serve()
    running.post("/book/hello/run", {"resume": False})
    expected = running.hub.subscribe(library / "hello.md").get(timeout=TIMEOUT)

    stream = running.open("GET", "/book/hello/events")

    assert stream.readline() == f"data: {json.dumps(expected, ensure_ascii=False)}\n".encode()
    assert stream.readline() == b"\n"


def test_events_end_when_the_hub_stops(serve):
    running = serve()
    running.post("/book/hello/run", {"resume": False})
    events = sse(running.open("GET", "/book/hello/events"))
    until(events, "prompt")

    running.hub.stop(timeout=TIMEOUT)

    assert until(events, "run_finished")[-1]["status"] == "interrupted"
    assert list(events) == []


def test_events_end_cleanly_when_the_server_stops(serve):
    """Even with the run still waiting, so nothing comes on the queue: the handler notices the server stopping."""
    running = serve()
    running.post("/book/hello/run", {"resume": False})
    events = sse(running.open("GET", "/book/hello/events"))
    until(events, "prompt")

    closing = threading.Thread(target=running.web.close)
    closing.start()
    closing.join(TIMEOUT)

    assert not closing.is_alive()
    assert list(events) == []


def test_another_books_tab_is_told_which_book_is_busy(serve):
    running = serve()
    running.post("/book/hello/run", {"resume": False})

    events = sse(running.open("GET", "/book/other/events"))

    assert next(events)["event"] == "busy"
    running.hub.stop(timeout=TIMEOUT)
    assert [event["book"] for event in events] == [None]


def test_a_book_with_a_run_to_resume_gets_the_resume_banner(serve, library):
    asked = []

    def resume_info(path: Path) -> str | None:
        asked.append(path)
        return "Stopped at step 1, Greet" if path.name == "hello.md" else None

    running = serve(resume_info=resume_info)
    hello = running.open("GET", "/book/hello/events")
    first = next(sse(hello))
    other = sse(running.open("GET", "/book/other/events"))
    running.hub.stop(timeout=TIMEOUT)

    assert first["event"] == "resume"
    assert first["message"] == "Stopped at step 1, Greet"
    assert list(other) == []
    assert asked == [library / "hello.md", library / "other.md"]


def test_a_run_that_cant_start_shows_its_error(serve):
    def start_run(path):
        def broken(prompter, sink, resume):
            raise runner.RunnerError("line 9: no backend runs 'ruby' blocks")

        return broken

    running = serve(start_run=start_run)
    running.post("/book/hello/run", {"resume": False})

    seen = until(sse(running.open("GET", "/book/hello/events")), "run_finished")

    assert [event["event"] for event in seen] == ["error", "run_finished"]
    assert seen[0]["message"] == "line 9: no backend runs 'ruby' blocks"
    assert seen[1]["status"] == "failed"


def test_an_idle_stream_sends_a_heartbeat(serve, monkeypatch):
    monkeypatch.setattr(server_module, "HEARTBEAT", 0.0)
    stream = serve().open("GET", "/book/hello/events")

    assert stream.readline() == b":\n"
    assert stream.readline() == b"\n"


def test_a_closed_tab_lets_go_of_its_queue(serve, monkeypatch):
    """The heartbeat finds the tab gone, and the handler ends instead of waiting for the server to stop."""
    monkeypatch.setattr(server_module, "HEARTBEAT", 0.0)
    running = serve()
    let_go = threading.Event()
    unsubscribe = running.hub.unsubscribe

    def spy(events: "queue.Queue[dict[str, Any] | None]") -> None:
        unsubscribe(events)
        let_go.set()

    monkeypatch.setattr(running.hub, "unsubscribe", spy)
    stream = running.open("GET", "/book/hello/events")
    assert stream.readline() == b":\n"

    stream.close()

    assert let_go.wait(TIMEOUT)


# Answers


def test_an_answer_goes_to_the_prompt_the_run_is_waiting_on(serve):
    running = serve()
    running.post("/book/other/run", {"resume": False})
    events = sse(running.open("GET", "/book/other/events"))
    until(events, "prompt")

    response, body = running.post("/book/other/answer", {"kind": "start", "value": "y"})

    assert response.status == 204
    assert body == b""
    assert until(events, "prompt")[-1]["kind"] == "confirm"


def test_an_answer_of_the_wrong_kind_is_409(serve):
    running = serve()
    running.post("/book/hello/run", {"resume": False})
    until(sse(running.open("GET", "/book/hello/events")), "prompt")

    response, body = running.post("/book/hello/answer", {"kind": "block", "value": "y"})

    assert response.status == 409
    assert body == b"waiting for an answer to start, not block"


def test_an_answer_when_nothing_is_running_is_409(serve):
    response, body = serve().post("/book/hello/answer", {"kind": "start", "value": "y"})

    assert response.status == 409
    assert body == b"hello isn't running"


def test_an_answer_for_a_book_that_isnt_the_one_running_is_409(serve):
    """Another book's tab can't answer the running book's prompt."""
    running = serve()
    running.post("/book/hello/run", {"resume": False})
    until(sse(running.open("GET", "/book/hello/events")), "prompt")

    response, body = running.post("/book/other/answer", {"kind": "start", "value": "y"})

    assert response.status == 409
    assert body == b"other isn't running"


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "",
        "[]",
        '"start"',
        '{"kind": "start"}',
        '{"value": "y"}',
        '{"kind": 1, "value": "y"}',
        '{"kind": "start", "value": true}',
        b"\xff",
    ],
)
def test_an_answer_that_isnt_a_kind_and_a_value_is_400(serve, body):
    running = serve()
    running.post("/book/hello/run", {"resume": False})

    response, _ = running.request("POST", "/book/hello/answer", body)

    assert response.status == 400


@pytest.mark.parametrize("length", ["many", "-1", str(64 * 1024 + 1)])
def test_a_bad_content_length_is_400(serve, length):
    response, _ = serve().request("POST", "/book/hello/answer", "{}", headers={"Content-Length": length})

    assert response.status == 400


# Runs


@pytest.mark.parametrize("resume", [False, True])
def test_run_starts_the_book_through_start_run(serve, library, resume):
    """Start and Start over send resume false, and Resume and Retry step send true."""
    running = serve()

    response, body = running.post("/book/hello/run", {"resume": resume})

    assert response.status == 204
    assert body == b""
    assert running.hub.running == library / "hello.md"
    until(sse(running.open("GET", "/book/hello/events")), "prompt")
    assert running.started == [(library / "hello.md", resume)]


def test_run_while_another_run_is_going_is_409_naming_the_busy_book(serve, library):
    running = serve()
    running.post("/book/hello/run", {"resume": False})

    for book in ("other", "hello"):
        response, body = running.post(f"/book/{book}/run", {"resume": False})
        assert response.status == 409
        assert response.getheader("Content-Type") == "application/json"
        assert json.loads(body) == {"busy": "hello"}
    assert running.hub.running == library / "hello.md"


def test_a_book_can_run_again_once_its_run_has_finished(serve, library):
    running = serve()
    running.post("/book/hello/run", {"resume": False})
    events = sse(running.open("GET", "/book/hello/events"))
    until(events, "prompt")
    running.post("/book/hello/answer", {"kind": "start", "value": "n"})
    until(events, "run_finished")
    assert running.hub.wait(timeout=TIMEOUT)

    response, _ = running.post("/book/hello/run", {"resume": True})

    assert response.status == 204
    assert until(events, "prompt")[-1]["kind"] == "start"


@pytest.mark.parametrize("body", ['{"resume": "yes"}', "{}", '{"resume": 1}', "nope"])
def test_a_run_request_that_isnt_resume_true_or_false_is_400(serve, body):
    running = serve()

    response, _ = running.request("POST", "/book/hello/run", body)

    assert response.status == 400
    assert running.hub.running is None


def test_closing_a_server_that_never_served_does_not_wait_for_it(library, static):
    """rundot web closes its server however it stops, including before it has started serving."""
    web = WebServer(RunHub(), lambda: [], starter, lambda path: None, library=library, static=static)

    # A daemon, so if close does wait, the test fails rather than the suite hanging at exit.
    closing = threading.Thread(target=web.close, name="rundot web test close", daemon=True)
    closing.start()
    closing.join(2)

    assert not closing.is_alive(), "close waited for a server that never started"
