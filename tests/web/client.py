"""A small http.client driver for a real rundot web server, as the page drives it: no browser."""

import http.client
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

# Long enough for bash to start on a slow CI machine; only reached when something is wrong.
TIMEOUT = 10


class Client:
    """Requests to a rundot web server, with the cookie its URL's token sets, from the server's own origin, as one
    tab, named as the page names itself."""

    tab = "tab-1"

    def __init__(self, url: str) -> None:
        """url is the one rundot web prints, with its token."""
        parts = urlsplit(url)
        assert parts.hostname == "127.0.0.1" and parts.port is not None, url
        self.port = parts.port
        self.host = f"127.0.0.1:{self.port}"
        response, _ = self.request("GET", f"{parts.path}?{parts.query}", cookie=None)
        assert response.status == 303, response.status
        cookie = response.getheader("Set-Cookie")
        assert cookie is not None
        self.cookie: str | None = cookie.split(";")[0]
        self.location = response.getheader("Location")

    def request(
        self, method: str, path: str, body: str | None = None, *, cookie: str | None | bool = True
    ) -> tuple[http.client.HTTPResponse, bytes]:
        response = self.open(method, path, body, cookie=cookie)
        return response, response.read()

    def open(
        self, method: str, path: str, body: str | None = None, *, cookie: str | None | bool = True
    ) -> http.client.HTTPResponse:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=TIMEOUT)
        headers = {"Host": self.host}
        if cookie is True:
            cookie = self.cookie
        if cookie:
            headers["Cookie"] = cookie
        if method == "POST":
            headers["Origin"] = f"http://{self.host}"
        connection.request(method, path, body=body, headers=headers)
        return connection.getresponse()

    def post(self, path: str, value: dict[str, Any]) -> int:
        response, body = self.request("POST", path, json.dumps({**value, "tab": self.tab}))
        assert response.status in (204, 409), (response.status, body)
        return response.status

    def events(self, book: str) -> "Events":
        response = self.open("GET", f"/book/{book}/events?tab={self.tab}")
        assert response.status == 200, response.status
        return Events(response)


class Events:
    """A book's SSE stream, one event at a time."""

    def __init__(self, response: http.client.HTTPResponse) -> None:
        self.response = response

    def next(self) -> dict[str, Any]:
        """The next event. Waits up to TIMEOUT for it, as the connection's timeout."""
        while True:
            line = self.response.readline()
            assert line, "the stream ended"
            if line.startswith(b"data: "):
                return json.loads(line.removeprefix(b"data: "))

    def close(self) -> None:
        self.response.close()


# Answers every prompt as a person who says yes to everything would. Inputs aren't asked: tests set them.
def yes(prompt: dict[str, Any]) -> str:
    assert prompt["kind"] != "input", prompt
    return "y"


def drive(
    client: Client,
    book: str,
    *,
    resume: bool = False,
    answer: Callable[[dict[str, Any]], str] = yes,
    seen: Callable[[dict[str, Any]], None] = lambda event: None,
) -> list[dict[str, Any]]:
    """Start a run of book, answer each prompt with answer, and return every event up to run_finished.

    seen is told of each event as it arrives, before any answer to it.
    """
    events = client.events(book)
    try:
        assert client.post(f"/book/{book}/run", {"resume": resume}) == 204
        got: list[dict[str, Any]] = []
        while True:
            event = events.next()
            if event["event"] in ("resume", "busy"):
                continue  # sent on connecting, before the run's own events
            got.append(event)
            seen(event)
            if event["event"] == "prompt":
                assert client.post(f"/book/{book}/answer", {"kind": event["kind"], "value": answer(event)}) == 204
            if event["event"] == "run_finished":
                return got
    finally:
        events.close()
