"""The pages rundot web serves: a book, and the library index.

Each is built from a small template in templates/, with string.Template. There's no template engine: everything a
runbook gives the page is escaped here, or rendered by rundot.runbook and then sanitised, before it's put in.
"""

import re
from collections.abc import Sequence
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote

from rundot.inputs import Input
from rundot.library import LIBRARY_VARIABLE, Book
from rundot.runbook import Block, Runbook, RunbookError, parse, render_html
from rundot.web.sanitise import sanitise

_TEMPLATES = Path(__file__).parent / "templates"
# The hooks render_html leaves, as the sanitiser writes them.
_BLOCK = re.compile(r'<div class="rd-block" data-kind="(?P<kind>\w+)" data-line="(?P<line>\d+)"[^>]*>.*?</div>', re.S)
_LINKS = re.compile(r"<(?:img|a) [^>]*>")
_URL = re.compile(r'(?P<name>src|href)="(?P<url>[^"]*)"')
_STEP = re.compile(r'<h2 data-step="(?P<number>\d+)">(?P<name>.*?)</h2>', re.S)


def _template(name: str) -> Template:
    return Template((_TEMPLATES / name).read_text(encoding="utf-8"))


_PAGE = _template("page.html")
_BOOK = _template("book.html")
_LIBRARY = _template("library.html")


def book(book: Book) -> str:
    """The page for a book: its title and description, its inputs form, and its whole rendered document."""
    title = escape(book.title or book.name)
    description = f'<p class="rd-description">{escape(book.description)}</p>' if book.description else ""
    try:
        # parse reads the file first, so a body that isn't UTF-8 is a RunbookError that says which line.
        runbook = parse(book.path)
        rendered = sanitise(render_html(book.path.read_text(encoding="utf-8")))
        document = _fill(runbook, _files(book.name, rendered))
    except (RunbookError, OSError) as e:
        # The page still opens, saying why, as rundot library lists a book it can't read with the reason.
        document, inputs = f'<p class="rd-problem">{escape(str(e))}</p>', ""
    else:
        inputs = _inputs(runbook.inputs)
    main = _BOOK.substitute(
        book=escape(quote(book.name)), title=title, description=description, inputs=inputs, document=document
    )
    return _PAGE.substitute(title=title, main=main)


def _files(name: str, document: str) -> str:
    """Point relative image and link URLs at the book's own directory, which /book/<name>/file/ serves.

    The page's URL is /book/<name>, so a browser would resolve them against /book/ otherwise. Only the sanitiser's
    own img and a tags are looked in, since it escapes every < in text.
    """
    base = f"/book/{escape(quote(name))}/file/"

    def url(match: re.Match[str]) -> str:
        return f'{match["name"]}="{base}{match["url"]}"' if _relative(match["url"]) else match[0]

    return _LINKS.sub(lambda tag: _URL.sub(url, tag[0]), document)


def _relative(url: str) -> bool:
    """Whether url is a path relative to the page: not a scheme, an absolute path, or only a query or fragment."""
    if not url or url[0] in "/#?":
        return False
    scheme, colon, _ = url.partition(":")
    return not colon or any(c in scheme for c in "/?#")


def _fill(runbook: Runbook, document: str) -> str:
    """Fill in the hooks render_html leaves, once the document is sanitised: each block's controls, and each step
    heading's n/total and mark.

    The sanitiser writes the hooks' attributes in a fixed order and escapes all text, so a block's div holds only
    its pre, and none of its text can look like markup. Markup in the runbook that only looks like a hook, with a
    line or step number that isn't a real block's or step's, is left as it is.
    """
    blocks = {block.line: block for step in runbook.steps for block in step.blocks}
    blocks.update((block.line, block) for block in runbook.preamble)
    total = len(runbook.steps)

    def block(match: re.Match[str]) -> str:
        found = blocks.get(int(match["line"]))
        return match[0] if found is None or found.kind != match["kind"] else _block(found)

    def step(match: re.Match[str]) -> str:
        number = int(match["number"])
        if not 1 <= number <= total:
            return match[0]
        return (
            f'<h2 data-step="{number}"><span class="rd-step-name">{match["name"]}</span>'
            f'<span class="rd-step-count">{number}/{total}</span><span class="rd-step-mark"></span></h2>'
        )

    return _STEP.sub(step, _BLOCK.sub(block, document))


def _block(block: Block) -> str:
    """A block's div, with the controls for its kind. The script enables them when the run reaches it."""
    language = f' data-lang="{escape(block.language)}"' if block.language else ""
    code = f"<pre><code>{escape(block.content, quote=False)}</code></pre>"
    match block.kind:
        case "run":
            inside = (
                f'<div class="rd-block-head"><span class="rd-lang">{escape(block.language or "")}</span>'
                '<button type="button" class="rd-play" data-action="block" disabled aria-label="Run this block">▶'
                f'</button></div>{code}<pre class="rd-output" hidden></pre><p class="rd-footer" hidden></p>'
            )
        case "confirm":
            inside = (
                f'{code}<div class="rd-actions"><button type="button" data-action="done" disabled>Done</button>'
                '<button type="button" data-action="stop" disabled>Stop</button></div>'
            )
        case "input" if block.input is not None:
            inside = (
                f'<form class="rd-input-block">{_field(block.input, disabled=True)}'
                '<button type="submit" data-action="input" disabled>Submit</button></form>'
            )
        case _:  # a docstring, or an input block that isn't a valid input
            inside = code
    return f'<div class="rd-block" data-kind="{block.kind}" data-line="{block.line}"{language}>{inside}</div>'


def library(books: Sequence[Book], where: Path, running: Path | None = None) -> str:
    """The library index at where: each book's title and description, linking to its page.

    running is the book whose run is going, if any, which is marked.
    """
    if books:
        listed = "".join(_listed(each, each.path == running) for each in books)
        content = f'<ul class="rd-books">{listed}</ul>'
    else:
        # The same as rundot library says.
        content = (
            f"<p>no books in the library at {escape(str(where))}</p>"
            f"<p>Add runbooks there, or set {LIBRARY_VARIABLE} to where yours are.</p>"
        )
    return _PAGE.substitute(title="Library", main=_LIBRARY.substitute(where=escape(str(where)), books=content))


def _listed(book: Book, running: bool) -> str:
    """A book's entry in the library index."""
    parts = [
        f'<a class="rd-book-link" href="/book/{escape(quote(book.name))}">{escape(book.title or book.name)}</a>',
        f'<span class="rd-book-name">{escape(book.name)}</span>',
    ]
    if running:
        parts.append('<span class="rd-pill rd-running">running</span>')
    if book.problem is not None:
        parts.append(f'<p class="rd-book-problem">{escape(f"can't read it: {book.problem}")}</p>')
    if book.description:
        parts.append(f'<p class="rd-book-description">{escape(book.description)}</p>')
    return f"<li>{''.join(parts)}</li>"


def _inputs(inputs: Sequence[Input]) -> str:
    """The form for the frontmatter's inputs, with Start, and the line it folds into once the run starts."""
    fields = "".join(_field(input) for input in inputs)
    return (
        f'<form class="rd-inputs" data-inputs>{fields}<button type="submit" data-action="start">Start</button></form>'
        '<p class="rd-inputs-summary" data-inputs-summary hidden></p>'
    )


def _field(input: Input, *, disabled: bool = False) -> str:
    """A field for an input: a select for one with choices, a password field for a secret, or a text field.

    disabled is for an input block's field, which the script enables when the run reaches it.
    """
    name = escape(input.name)
    off = " disabled" if disabled else ""
    if input.choices:
        options = "".join(
            f'<option value="{escape(choice)}"{" selected" * (choice == input.default)}>{escape(choice)}</option>'
            for choice in input.choices
        )
        control = f'<select name="{name}"{off}>{options}</select>'
    else:
        kind = "password" if input.secret else "text"
        value = escape(input.default or "")
        control = f'<input type="{kind}" name="{name}" value="{value}" autocomplete="off"{off}>'
    return (
        f'<label class="rd-field" data-input="{name}"><span class="rd-field-name">{name}</span>{control}'
        f'<span class="rd-field-help">{escape(input.description)}</span>'
        '<span class="rd-field-error" hidden></span></label>'
    )
