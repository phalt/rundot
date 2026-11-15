"""The rundot web HTTP server: routes, the token cookie, Host and Origin checks, SSE and files.

It binds to 127.0.0.1 only. Every request must have the right Host, and the cookie the one-time token sets, and
every POST must come from the server's own Origin. The CLI gives it the callables that wire a run, so this module
never touches transcripts or run state.
"""

import hmac
import json
import mimetypes
import queue
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from rundot.library import Book
from rundot.web import page
from rundot.web.hub import Busy, RunHub, StartRun, WrongAnswer

STATIC = Path(__file__).parent / "static"
CSP = "default-src 'self'"
# How often an SSE handler looks up from its queue, and the server from its socket, to see whether it's stopping.
POLL = 0.05
# How often an idle SSE stream sends a comment, so a closed tab's handler finds out and ends.
HEARTBEAT = 15.0
# The most a POST body may be. Answers and run requests are a few bytes.
MAX_BODY = 64 * 1024

# The CLI's: the StartRun for a book, given its path, and the resume banner's text for it, or None.
MakeStartRun = Callable[[Path], StartRun]
ResumeInfo = Callable[[Path], str | None]


class WebServer:
    """rundot web's server, on 127.0.0.1 and the port given, or one the OS picks for 0."""

    def __init__(
        self,
        hub: RunHub,
        books: Callable[[], Sequence[Book]],
        start_run: MakeStartRun,
        resume_info: ResumeInfo,
        *,
        library: Path,
        opened: str | None = None,
        port: int = 0,
        static: Path = STATIC,
    ) -> None:
        """books lists the books that can be opened, from the library at library. With opened, / goes straight to
        that book."""
        self.hub = hub
        self.books = books
        self.library = library
        self.start_run = start_run
        self.resume_info = resume_info
        self.opened = opened
        self.static = static
        self.token = secrets.token_urlsafe(32)
        self.stopping = threading.Event()
        self._http = _HTTPServer(("127.0.0.1", port), _Handler, self)
        # Where the socket is really bound, so a test can check it's 127.0.0.1.
        self.address: tuple[str, int] = self._http.socket.getsockname()
        self.port = self.address[1]
        # Cookies don't keep to a port, so each server's has its own name.
        self.cookie_name = f"rundot-{self.port}"
        self.hosts = frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})
        self.origins = frozenset(f"http://{host}" for host in self.hosts)

    @property
    def url(self) -> str:
        """The URL to open, with the token, which sets the cookie."""
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def serve_forever(self) -> None:
        self._http.serve_forever(poll_interval=POLL)

    def close(self) -> None:
        """Stop serving, end every SSE stream, and wait for every request's thread to end.

        Called from another thread than serve_forever's.
        """
        self.stopping.set()
        self._http.shutdown()
        self._http.server_close()


class _HTTPServer(ThreadingHTTPServer):
    # Request threads are joined by server_close, so none outlives the server. SSE handlers notice it stopping.
    daemon_threads = False
    block_on_close = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], web: WebServer) -> None:
        super().__init__(address, handler)
        self.web = web


class _Refused(Exception):
    """End the request with this status and message."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


class _Handler(BaseHTTPRequestHandler):
    server: _HTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: the terminal is for the CLI's own messages."""

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def _handle(self, method: str) -> None:
        try:
            self._route(method)
        except _Refused as refused:
            self._send(refused.status, str(refused).encode(), "text/plain; charset=utf-8")

    def _route(self, method: str) -> None:
        web = self.server.web
        if self.headers.get("Host") not in web.hosts:
            raise _Refused(HTTPStatus.FORBIDDEN, "this server only answers to 127.0.0.1 and localhost")
        url = urlsplit(self.path)
        query = parse_qsl(url.query, keep_blank_values=True)
        if method == "GET" and any(name == "t" for name, _ in query):
            return self._take_token(url.path, query)
        if not self._has_cookie():
            raise _Refused(HTTPStatus.FORBIDDEN, "open the URL rundot web printed, with its token")
        if method == "POST" and self.headers.get("Origin") not in web.origins:
            raise _Refused(HTTPStatus.FORBIDDEN, "only this server's own pages can do that")

        parts = [unquote(part) for part in url.path.split("/")[1:]]
        match method, parts:
            case "GET", [""]:
                if web.opened is not None:
                    return self._redirect(f"/book/{quote(web.opened)}")
                return self._html(page.library(web.books(), web.library, web.hub.running))
            case "GET", ["static", *rest] if rest:
                return self._file(web.static, rest)
            case "GET", ["book", name]:
                return self._html(page.book(self._book(name)))
            case "GET", ["book", name, "events"]:
                return self._events(self._book(name))
            case "GET", ["book", name, "file", *rest] if rest:
                return self._file(self._book(name).path.parent, rest)
            case "POST", ["book", name, "answer"]:
                return self._answer(self._book(name))
            case "POST", ["book", name, "run"]:
                return self._run(self._book(name))
        raise _Refused(HTTPStatus.NOT_FOUND, "no such page")

    def _take_token(self, path: str, query: list[tuple[str, str]]) -> None:
        """Swap the URL's token for the cookie, and go to the same URL without it. A wrong token sets nothing."""
        web = self.server.web
        given = next(value for name, value in query if name == "t")
        if not hmac.compare_digest(given.encode(), web.token.encode()):
            raise _Refused(HTTPStatus.FORBIDDEN, "that token isn't this server's")
        rest = urlencode([(name, value) for name, value in query if name != "t"])
        cookie = f"{web.cookie_name}={web.token}; HttpOnly; SameSite=Strict; Path=/"
        self._redirect(path + (f"?{rest}" if rest else ""), {"Set-Cookie": cookie})

    def _has_cookie(self) -> bool:
        # Parsed by hand: SimpleCookie drops every cookie in the header when it meets a name like expires or
        # domain, and other servers on localhost share the browser's cookies for it, since cookies ignore ports.
        web = self.server.web
        for cookie in self.headers.get("Cookie", "").split(";"):
            name, _, value = cookie.strip().partition("=")
            if name == web.cookie_name and hmac.compare_digest(value.encode(), web.token.encode()):
                return True
        return False

    def _book(self, name: str) -> Book:
        for book in self.server.web.books():
            if book.name == name:
                return book
        raise _Refused(HTTPStatus.NOT_FOUND, f"no book called {name}")

    def _file(self, root: Path, parts: list[str]) -> None:
        """Send the file at parts under root, which must stay inside it, symlinks and all."""
        relative = PurePosixPath(*parts)
        if relative.is_absolute() or ".." in relative.parts or "" in parts:
            raise _Refused(HTTPStatus.FORBIDDEN, "only files inside the directory can be served")
        try:
            resolved = (root / relative).resolve(strict=True)
            if not resolved.is_relative_to(root.resolve(strict=True)):
                raise _Refused(HTTPStatus.FORBIDDEN, "only files inside the directory can be served")
            content = resolved.read_bytes()
        except OSError:  # missing, a directory, or unreadable
            raise _Refused(HTTPStatus.NOT_FOUND, "no such file") from None
        kind, _ = mimetypes.guess_type(resolved.name)
        self._send(HTTPStatus.OK, content, kind or "application/octet-stream")

    def _events(self, book: Book) -> None:
        """Stream the book's events as SSE: its run so far, then each as it happens, until the server stops."""
        web = self.server.web
        events = web.hub.subscribe(book.path, web.resume_info(book.path))
        try:
            self.send_response(HTTPStatus.OK)
            self._common_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = time.monotonic()
            while not web.stopping.is_set():
                try:
                    event = events.get(timeout=POLL)
                except queue.Empty:
                    if time.monotonic() - last < HEARTBEAT:
                        continue
                    self.wfile.write(b":\n\n")
                else:
                    if event is None:
                        break
                    self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                last = time.monotonic()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the tab has gone
        finally:
            web.hub.unsubscribe(events)

    def _answer(self, book: Book) -> None:
        body = self._json()
        kind, value = body.get("kind"), body.get("value")
        if not isinstance(kind, str) or not isinstance(value, str):
            raise _Refused(HTTPStatus.BAD_REQUEST, 'an answer is {"kind": "...", "value": "..."}')
        hub = self.server.web.hub
        if hub.running != book.path:
            raise _Refused(HTTPStatus.CONFLICT, f"{book.name} isn't running")
        try:
            hub.answer(kind, value)
        except WrongAnswer as e:
            raise _Refused(HTTPStatus.CONFLICT, str(e)) from None
        self._send(HTTPStatus.NO_CONTENT)

    def _run(self, book: Book) -> None:
        resume = self._json().get("resume")
        if not isinstance(resume, bool):
            raise _Refused(HTTPStatus.BAD_REQUEST, 'a run is {"resume": true} or {"resume": false}')
        web = self.server.web
        try:
            web.hub.start(book.path, web.start_run(book.path), resume)
        except Busy as busy:
            body = json.dumps({"busy": busy.path.stem}).encode()
            return self._send(HTTPStatus.CONFLICT, body, "application/json")
        self._send(HTTPStatus.NO_CONTENT)

    def _json(self) -> dict[str, Any]:
        """The request's body, which must be a JSON object."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise _Refused(HTTPStatus.BAD_REQUEST, "Content-Length isn't a number") from None
        if not 0 <= length <= MAX_BODY:
            raise _Refused(HTTPStatus.BAD_REQUEST, "the body is too big")
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            raise _Refused(HTTPStatus.BAD_REQUEST, "the body isn't JSON") from None
        if not isinstance(body, dict):
            raise _Refused(HTTPStatus.BAD_REQUEST, "the body isn't a JSON object")
        return body

    def _html(self, markup: str) -> None:
        self._send(HTTPStatus.OK, markup.encode(), "text/html; charset=utf-8")

    def _redirect(self, location: str, headers: dict[str, str] | None = None) -> None:
        self._send(HTTPStatus.SEE_OTHER, headers={"Location": location, **(headers or {})})

    def _send(
        self,
        status: HTTPStatus,
        body: bytes = b"",
        kind: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self._common_headers()
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if kind is not None:
            self.send_header("Content-Type", kind)
        if status != HTTPStatus.NO_CONTENT:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _common_headers(self) -> None:
        # On every response, not only HTML: a runbook's directory may hold an .html file, served as one.
        self.send_header("Content-Security-Policy", CSP)
