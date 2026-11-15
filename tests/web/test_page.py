"""Tests for the pages rundot web serves: the book page and the library index, built by rundot.web.page."""

import re
import textwrap
from pathlib import Path

from click.testing import CliRunner

from rundot import cli
from rundot.library import Book
from rundot.web import page

RUNBOOKS = Path(__file__).parent.parent / "runbooks"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


def book_page(path: Path, title: str | None = None, description: str | None = None) -> str:
    return page.book(Book(path.stem, path, title, description))


def all_features() -> str:
    return book_page(RUNBOOKS / "all_features.md", "Release example", "A runbook that uses every part of the format.")


# The header


def test_every_page_has_the_wordmark_and_the_library_link_at_the_top(tmp_path):
    pages = [
        all_features(),
        page.library([], tmp_path),
        page.library([Book("a", tmp_path / "a.md", None, None)], tmp_path),
    ]

    for markup in pages:
        body = markup.split("<body>", 1)[1]
        assert body.startswith('<header class="rd-header">')
        header = body.split("</header>", 1)[0]
        assert '<a class="rd-wordmark" href="/">run<span>.</span>dot</a>' in header
        assert '<a class="rd-nav" href="/">← Library</a>' in header


def test_every_page_loads_the_packaged_stylesheet_and_script(tmp_path):
    for markup in [all_features(), page.library([], tmp_path)]:
        assert '<link rel="stylesheet" href="/static/rundot.css">' in markup
        assert '<script type="module" src="/static/rundot.js"></script>' in markup


def test_a_book_page_has_its_title_then_description_then_the_document():
    markup = all_features()

    title = markup.index('<h1 class="rd-title">Release example</h1>')
    description = markup.index('<p class="rd-description">A runbook that uses every part of the format.</p>')
    document = markup.index('<article class="rd-doc">')
    assert title < description < document
    assert "<title>Release example · run.dot</title>" in markup


def test_a_book_with_no_title_is_headed_by_its_name_and_has_no_description(tmp_path):
    markup = book_page(write(tmp_path / "plain.md", "## One\n"))

    assert '<h1 class="rd-title">plain</h1>' in markup
    assert "rd-description" not in markup


def test_a_book_page_names_its_book_for_the_script(tmp_path):
    markup = book_page(write(tmp_path / "two words.md", "## One\n"))

    assert '<main class="rd-book" data-book="two%20words">' in markup


def test_a_book_that_cant_be_read_says_why_under_its_title(tmp_path):
    path = tmp_path / "broken.md"
    path.write_bytes(b"---\ntitle: [\n")

    markup = book_page(path)

    assert '<h1 class="rd-title">broken</h1>' in markup
    assert '<p class="rd-problem">frontmatter has no closing ---</p>' in markup
    assert "rd-inputs" not in markup


def test_a_book_whose_body_isnt_utf_8_says_why(tmp_path):
    path = tmp_path / "bad.md"
    path.write_bytes(b"## One\n\n\xff\n")

    assert "line 3 isn&#x27;t valid UTF-8" in book_page(path)


def test_a_book_whose_file_has_gone_says_why(tmp_path):
    assert "No such file" in book_page(tmp_path / "gone.md")


# The library index


def test_the_library_lists_each_book_with_its_title_and_description(tmp_path):
    books = [
        Book("hello", tmp_path / "hello.md", "Say hello", "Who to say hello to"),
        Book("plain", tmp_path / "plain.md", None, None),
    ]

    markup = page.library(books, tmp_path)

    assert '<h1 class="rd-title">Library</h1>' in markup
    assert '<a class="rd-book-link" href="/book/hello">Say hello</a>' in markup
    assert '<span class="rd-book-name">hello</span>' in markup
    assert '<p class="rd-book-description">Who to say hello to</p>' in markup
    assert '<a class="rd-book-link" href="/book/plain">plain</a>' in markup
    assert markup.index("/book/hello") < markup.index("/book/plain")


def test_the_library_says_where_it_is(tmp_path):
    assert f'<p class="rd-description">{tmp_path}</p>' in page.library([], tmp_path)


def test_the_library_says_why_a_book_cant_be_read(tmp_path):
    books = [Book("broken", tmp_path / "broken.md", None, None, problem="frontmatter has no closing ---")]

    assert '<p class="rd-book-problem">can&#x27;t read it: frontmatter has no closing ---</p>' in page.library(
        books, tmp_path
    )


def test_the_library_marks_the_book_thats_running(tmp_path):
    books = [Book("a", tmp_path / "a.md", None, None), Book("b", tmp_path / "b.md", None, None)]

    markup = page.library(books, tmp_path, running=tmp_path / "b.md")

    assert markup.count('<span class="rd-pill rd-running">running</span>') == 1
    assert markup.index("rd-running") > markup.index("/book/b")


def test_an_empty_library_says_what_rundot_library_says(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNDOT_LIBRARY", str(tmp_path))
    said = CliRunner().invoke(cli.cli, ["library"]).output.splitlines()

    markup = page.library([], tmp_path)

    assert len(said) == 2
    for line in said:
        assert f"<p>{line}</p>" in markup


def test_a_book_name_in_a_library_link_is_url_encoded(tmp_path):
    markup = page.library([Book("two words&<", tmp_path / "x.md", None, None)], tmp_path)

    assert 'href="/book/two%20words%26%3C"' in markup
    assert ">two words&amp;&lt;</a>" in markup


def test_pages_have_no_markup_left_from_their_templates(tmp_path):
    for markup in [all_features(), page.library([], tmp_path)]:
        assert "$" not in re.sub(r"<pre.*?</pre>", "", markup, flags=re.DOTALL)


# The inputs form


def form(markup: str) -> str:
    return markup.split('<form class="rd-inputs" data-inputs>', 1)[1].split("</form>", 1)[0]


def test_the_inputs_form_is_at_the_very_top_under_the_title_and_description():
    markup = all_features()

    description = markup.index('<p class="rd-description">')
    inputs = markup.index('<form class="rd-inputs" data-inputs>')
    document = markup.index('<article class="rd-doc">')
    assert description < inputs < document


def test_the_form_has_one_field_per_frontmatter_input_then_start():
    fields = form(all_features())

    assert fields == (
        '<label class="rd-field" data-input="VERSION"><span class="rd-field-name">VERSION</span>'
        '<input type="text" name="VERSION" value="" autocomplete="off">'
        '<span class="rd-field-help">The version being released, e.g. 1.2.0</span>'
        '<span class="rd-field-error" hidden></span></label>'
        '<label class="rd-field" data-input="PUBLISH_TOKEN"><span class="rd-field-name">PUBLISH_TOKEN</span>'
        '<input type="password" name="PUBLISH_TOKEN" value="" autocomplete="off">'
        '<span class="rd-field-help">Token for the package index</span>'
        '<span class="rd-field-error" hidden></span></label>'
        '<button type="submit" data-action="start">Start</button>'
    )


def test_a_default_fills_its_field_in(tmp_path):
    path = write(
        tmp_path / "default.md",
        """\
        ---
        inputs:
          - name: NAME
            description: Who
            default: Paul
        ---
        """,
    )

    assert '<input type="text" name="NAME" value="Paul" autocomplete="off">' in form(book_page(path))


def test_an_input_with_choices_is_a_select_with_its_default_chosen(tmp_path):
    path = write(
        tmp_path / "choices.md",
        """\
        ---
        inputs:
          - name: COLOUR
            description: Which
            choices: [red, green]
            default: green
        ---
        """,
    )

    assert (
        '<select name="COLOUR"><option value="red">red</option><option value="green" selected>green</option></select>'
    ) in form(book_page(path))


def test_a_book_with_no_inputs_still_has_start(tmp_path):
    assert form(book_page(write(tmp_path / "none.md", "## One\n"))) == (
        '<button type="submit" data-action="start">Start</button>'
    )


def test_the_form_has_a_hidden_summary_line_to_fold_into():
    markup = all_features()

    summary = markup.index('<p class="rd-inputs-summary" data-inputs-summary hidden></p>')
    assert markup.index("</form>") < summary < markup.index('<article class="rd-doc">')
