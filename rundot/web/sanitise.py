"""Sanitise rendered runbook HTML with an allowlist modelled on GitHub's."""

import html
from html.parser import HTMLParser


def sanitise(markup: str) -> str:
    """Return markup with only allowed tags and attributes, and all text re-escaped."""
    parser = _Sanitiser()
    parser.feed(markup)
    parser.close()
    return "".join(parser.out)


class _Sanitiser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data, quote=False))
