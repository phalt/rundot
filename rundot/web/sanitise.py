"""Sanitise rendered runbook HTML with an allowlist modelled on GitHub's."""

import html
from html.parser import HTMLParser

# Everything markdown-it renders, plus the raw HTML GitHub allows in a README.
_TAGS = frozenset(
    "a abbr b bdo blockquote br caption cite code dd del details dfn div dl dt em figcaption figure"
    " h1 h2 h3 h4 h5 h6 hr i img ins kbd li mark ol p pre q rp rt ruby s samp small span strike"
    " strong sub summary sup table tbody td tfoot th thead time tr tt ul var wbr".split()
)
# Tags with no end tag, written the way markdown-it writes them.
_VOID = frozenset({"br", "hr", "img", "wbr"})


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
        # Allowed tags that are open, so every end tag written matches one, and none is left open.
        self.open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _TAGS:
            return
        if tag in _VOID:
            self.out.append(f"<{tag} />")
        else:
            self.open.append(tag)
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.open:
            return
        while (closing := self.open.pop()) != tag:
            self.out.append(f"</{closing}>")
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data, quote=False))

    def close(self) -> None:
        super().close()
        self.out.extend(f"</{tag}>" for tag in reversed(self.open))
        self.open.clear()
