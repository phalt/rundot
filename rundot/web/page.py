"""The pages rundot web serves: a book, and the library index.

Each is built from a small template in templates/, with string.Template. There's no template engine: everything a
runbook gives the page is escaped here, or rendered by rundot.runbook and then sanitised, before it's put in.
"""

from collections.abc import Sequence
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote

from rundot.inputs import Input
from rundot.library import LIBRARY_VARIABLE, Book
from rundot.runbook import RunbookError, parse, render_html
from rundot.web.sanitise import sanitise

_TEMPLATES = Path(__file__).parent / "templates"


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
        document = sanitise(render_html(book.path.read_text(encoding="utf-8")))
    except (RunbookError, OSError) as e:
        # The page still opens, saying why, as rundot library lists a book it can't read with the reason.
        document, inputs = f'<p class="rd-problem">{escape(str(e))}</p>', ""
    else:
        inputs = _inputs(runbook.inputs)
    main = _BOOK.substitute(
        book=escape(quote(book.name)), title=title, description=description, inputs=inputs, document=document
    )
    return _PAGE.substitute(title=title, main=main)


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


def _field(input: Input) -> str:
    """A field for an input: a select for one with choices, a password field for a secret, or a text field."""
    name = escape(input.name)
    off = ""
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
