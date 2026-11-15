"""The pages rundot web serves: a book, and the library index. Placeholders for now; task 6 gives them their look.

Everything a runbook gives the page is escaped, or rendered by rundot.runbook and then sanitised.
"""

from collections.abc import Sequence
from html import escape
from urllib.parse import quote

from rundot.library import Book
from rundot.runbook import RunbookError, render_html
from rundot.web.sanitise import sanitise


def book(book: Book) -> str:
    """The page for a book: its title, description and whole rendered document, under a link to the library."""
    try:
        body = sanitise(render_html(book.path.read_text(encoding="utf-8")))
    except (RunbookError, OSError, UnicodeDecodeError) as e:
        # The page still opens, saying why, as rundot library lists a book it can't read with the reason.
        body = f"<p>{escape(str(e))}</p>"
    title = escape(book.title or book.name)
    description = f"<p>{escape(book.description)}</p>" if book.description else ""
    return _page(title, f"<h1>{title}</h1>{description}{body}")


def library(books: Sequence[Book]) -> str:
    """The library index: each book's title and description, linking to its page."""
    if not books:
        return _page("Library", "<p>no books in the library</p>")
    items = "".join(
        f'<li><a href="/book/{quote(each.name)}">{escape(each.title or each.name)}</a>'
        + (f" {escape(each.description)}" if each.description else "")
        + "</li>"
        for each in books
    )
    return _page("Library", f"<ul>{items}</ul>")


def _page(title: str, main: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{title}</title></head>"
        f'<body><header><a href="/">← Library</a></header><main>{main}</main></body></html>'
    )
