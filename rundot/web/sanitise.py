"""Sanitise rendered runbook HTML with an allowlist modelled on GitHub's."""

import html
import re
from html.parser import HTMLParser

# Everything markdown-it renders, plus the raw HTML GitHub allows in a README.
_TAGS = frozenset(
    "a abbr b bdo blockquote br caption cite code dd del details dfn div dl dt em figcaption figure"
    " h1 h2 h3 h4 h5 h6 hr i img ins kbd li mark ol p pre q rp rt ruby s samp small span strike"
    " strong sub summary sup table tbody td tfoot th thead time tr tt ul var wbr".split()
)
# The attributes each tag keeps. Anything else goes, including every on* handler and style.
_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "div": {"align"},
    "p": {"align"},
    "ol": {"start"},
    "th": {"align"},
    "td": {"align"},
    "details": {"open"},
}
# Attributes kept even with no value, like <details open>.
_BOOLEAN = frozenset({"open"})
# markdown-it marks a fence's language as <code class="language-x">.
_LANGUAGE = re.compile(r"language-[^\s]+")
# markdown-it aligns table cells with exactly this style, which GitHub writes as align.
_TEXT_ALIGN = re.compile(r"text-align:(left|center|right)")

# Attributes holding a URL, and the schemes they may use. A URL with no scheme is relative, and kept.
_URLS = frozenset({"href", "src"})
_SCHEMES = frozenset({"http", "https", "mailto"})
# Browsers ignore tabs and newlines anywhere in a URL, and control characters and spaces before it.
_IGNORED_IN_URL = re.compile(r"[\t\n\r]")
_LEADING = re.compile(r"^[\x00-\x20]+")

# Tags dropped with everything inside them, not just the tag.
_DROP = frozenset(
    "applet embed form frame frameset iframe math noembed noframes noscript object script select style svg"
    " template textarea title xmp".split()
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
        # How many dropped tags are open; while any are, everything is dropped.
        self.dropping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP:
            self.dropping += 1
        if self.dropping or tag not in _TAGS:
            return
        start = "".join(_attribute(name, value) for name, value in _keep(tag, attrs).items())
        if tag in _VOID:
            self.out.append(f"<{tag}{start} />")
        else:
            self.open.append(tag)
            self.out.append(f"<{tag}{start}>")

    def handle_endtag(self, tag: str) -> None:
        if self.dropping:
            self.dropping -= tag in _DROP
            return
        if tag not in self.open:
            return
        while (closing := self.open.pop()) != tag:
            self.out.append(f"</{closing}>")
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.dropping:
            self.out.append(html.escape(data, quote=False))

    def close(self) -> None:
        super().close()
        self.out.extend(f"</{tag}>" for tag in reversed(self.open))
        self.open.clear()


def _keep(tag: str, attrs: list[tuple[str, str | None]]) -> dict[str, str | None]:
    """The attributes tag keeps, in order. A repeated attribute keeps its first value, as browsers do."""
    given: dict[str, str | None] = {}
    for name, value in attrs:
        given.setdefault(name, value)
    allowed = _ATTRIBUTES.get(tag, set())
    kept = {
        name: value
        for name, value in given.items()
        if name in allowed and (value is not None or name in _BOOLEAN) and (name not in _URLS or _safe_url(value or ""))
    }
    if tag == "code" and _LANGUAGE.fullmatch(given.get("class") or ""):
        kept["class"] = given["class"]
    if tag in ("th", "td") and "align" not in kept and (align := _TEXT_ALIGN.fullmatch(given.get("style") or "")):
        kept["align"] = align[1]
    return kept


def _safe_url(url: str) -> bool:
    """Whether url is relative or uses an allowed scheme, read the way a browser reads it.

    Entities are already decoded by the parser, so &#106;avascript: arrives here as javascript:.
    """
    url = _LEADING.sub("", _IGNORED_IN_URL.sub("", url))
    scheme, colon, _ = url.partition(":")
    if not colon or any(c in scheme for c in "/?#"):
        return True
    return scheme.lower() in _SCHEMES


def _attribute(name: str, value: str | None) -> str:
    return f" {name}" if value is None else f' {name}="{html.escape(value)}"'
