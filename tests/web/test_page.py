"""Tests for the pages rundot web serves: the book page and the library index, built by rundot.web.page."""

import re
import subprocess
import textwrap
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from click.testing import CliRunner

from rundot import cli
from rundot.library import Book
from rundot.web import page

RUNBOOKS = Path(__file__).parent.parent / "runbooks"
ROOT = Path(__file__).parent.parent.parent
WEB = ROOT / "rundot" / "web"


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


def test_every_page_ends_with_links_to_the_docs_and_the_website(tmp_path):
    """They open in a new tab, so following one doesn't leave a run, and send no Referer, so the page's address
    stays on this machine."""
    pages = [
        all_features(),
        page.library([], tmp_path),
        page.library([Book("a", tmp_path / "a.md", None, None)], tmp_path),
    ]

    for markup in pages:
        body = markup.split("<body>", 1)[1]
        assert body.endswith(
            '<footer class="rd-page-footer">'
            '<a href="https://docs.rundot.dev/" target="_blank" rel="noreferrer">docs.rundot.dev</a>'
            '<span aria-hidden="true">/</span>'
            '<a href="https://rundot.dev/" target="_blank" rel="noreferrer">rundot.dev</a>'
            "</footer>\n</body>\n</html>\n"
        )


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


# Blocks and steps


def test_a_run_block_has_its_language_a_disabled_play_button_its_code_an_output_area_and_a_footer():
    assert (
        '<div class="rd-block" data-kind="run" data-line="30" data-lang="sh"><div class="rd-block-head">'
        '<span class="rd-lang">sh</span>'
        '<button type="button" class="rd-play" data-action="block" disabled aria-label="Run this block">▶</button>'
        '</div><pre><code>echo "Releasing &lt;VERSION&gt;"\n</code></pre>'
        '<pre class="rd-output" hidden></pre><p class="rd-footer" hidden></p></div>'
    ) in all_features()


def test_every_run_block_has_a_play_button_and_nothing_else_does():
    markup = all_features()

    assert markup.count('data-action="block"') == 4
    for line in (30, 50, 58, 64):
        block = markup.split(f'data-line="{line}"', 1)[1].split('class="rd-block"', 1)[0]
        assert 'data-action="block"' in block


def test_fences_that_arent_rundot_blocks_are_plain_code_with_no_button():
    markup = all_features()

    assert '<pre><code class="language-sh">echo "for reading only"\n</code></pre>' in markup
    assert '<pre><code class="language-python">print("not run")\n</code></pre>' in markup


def test_a_confirm_is_a_callout_with_disabled_done_and_stop():
    assert (
        '<div class="rd-block" data-kind="confirm" data-line="46">'
        '<pre><code>Set version = "&lt;VERSION&gt;" in pyproject.toml and save it.\n</code></pre>'
        '<div class="rd-actions"><button type="button" data-action="done" disabled>Done</button>'
        '<button type="button" data-action="stop" disabled>Stop</button></div></div>'
    ) in all_features()


def test_a_docstring_block_just_shows_its_text():
    assert (
        '<div class="rd-block" data-kind="docstring" data-line="26"><pre><code>Releasing &lt;VERSION&gt;.\n'
        "</code></pre></div>"
    ) in all_features()


def test_an_input_block_is_a_disabled_field_where_it_sits():
    markup = book_page(RUNBOOKS / "input-block-example.md")

    assert (
        '<div class="rd-block" data-kind="input" data-line="32"><form class="rd-input-block">'
        '<label class="rd-field" data-input="PR"><span class="rd-field-name">PR</span>'
        '<input type="text" name="PR" value="" autocomplete="off" disabled>'
        '<span class="rd-field-help">The number GitHub gave the pull request</span>'
        '<span class="rd-field-error" hidden></span></label>'
        '<button type="submit" data-action="input" disabled>Submit</button></form></div>'
    ) in markup
    assert '<input type="password" name="OTP" value="" autocomplete="off" disabled>' in markup
    assert "name: PR" not in markup


def test_an_input_block_with_choices_is_a_disabled_select(tmp_path):
    path = write(tmp_path / "pick.md", "## One\n\n```input\nname: PICK\ndescription: Pick\nchoices: [a, b]\n```\n")

    assert '<select name="PICK" disabled><option value="a">a</option>' in book_page(path)


def test_an_input_block_that_isnt_a_valid_input_stays_as_code(tmp_path):
    path = write(tmp_path / "bad.md", "## One\n\n```input\nname: [\n```\n")

    assert '<div class="rd-block" data-kind="input" data-line="3"><pre><code>name: [\n</code></pre></div>' in (
        book_page(path)
    )


def test_each_step_heading_shows_its_number_of_the_total_and_a_place_for_its_mark():
    markup = all_features()

    assert (
        '<h2 data-step="1"><span class="rd-step-name">Check the version</span>'
        '<span class="rd-step-count">1/3</span><span class="rd-step-mark"></span></h2>'
    ) in markup
    assert '<span class="rd-step-count">3/3</span>' in markup
    assert "<h3>Why a subheading</h3>" in markup


def test_markup_that_only_looks_like_a_block_or_a_step_is_left_alone(tmp_path):
    path = write(
        tmp_path / "forged.md",
        """\
        ## One

        <div class="rd-block" data-kind="run" data-line="99"><pre><code>rm -rf /</code></pre></div>

        <div class="rd-block" data-kind="run" data-line="7"><pre><code>wrong kind</code></pre></div>

        ```confirm
        Real
        ```

        <h2 data-step="5">Not a step</h2>
        """,
    )

    markup = book_page(path)

    assert '<div class="rd-block" data-kind="run" data-line="99"><pre><code>rm -rf /</code></pre></div>' in markup
    assert markup.count('data-action="done"') == 1
    assert '<h2 data-step="5">Not a step</h2>' in markup
    assert 'data-action="block"' not in markup


# Banners


def banner(markup: str, name: str) -> str:
    return markup.split(f'data-banner="{name}" hidden>', 1)[1].split('<div class="rd-banner', 1)[0]


def test_the_resume_busy_error_failed_and_finished_banners_are_there_hidden_until_the_script_shows_them():
    markup = all_features()

    assert markup.index('<div class="rd-banners">') < markup.index('<form class="rd-inputs"')
    resume = banner(markup, "resume")
    assert '<button type="button" data-action="resume">Resume</button>' in resume
    assert '<button type="button" data-action="restart">Start over</button>' in resume
    assert "data-message" in resume
    assert "is running" in banner(markup, "busy")
    assert "data-message" in banner(markup, "error")
    failed = banner(markup, "failed")
    assert '<button type="button" data-action="resume">Retry step</button>' in failed
    assert '<button type="button" data-action="restart">Start over</button>' in failed
    assert '<button type="button" data-action="restart">Run again</button>' in banner(markup, "finished")


# Hostile runbooks


class Tags(HTMLParser):
    """Every start tag in a page, with its attributes, as a browser would read them."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.seen.append((tag, attrs))


HOSTILE = '"><script>alert(1)</script><img src=x onerror=alert(2)>'


def test_nothing_a_runbook_gives_the_page_can_add_markup(tmp_path):
    path = write(
        tmp_path / "hostile.md",
        f"""\
        ---
        title: '{HOSTILE}'
        description: '{HOSTILE}'
        inputs:
          - name: 'A{HOSTILE}'
            description: '{HOSTILE}'
            default: '{HOSTILE}'
          - name: B
            description: '{HOSTILE}'
            choices: ['{HOSTILE}', ok]
          - name: C
            description: '{HOSTILE}'
            secret: true
        ---

        <script>alert(3)</script> <a href="javascript:alert(4)">x</a> <img src=x onerror=alert(5)>

        ## {HOSTILE}

        ```sh run
        echo '{HOSTILE}' </div></pre>
        ```

        ```confirm
        {HOSTILE}
        ```

        ```input
        name: D
        description: '{HOSTILE}'
        ```

        ```{HOSTILE}
        {HOSTILE}
        ```
        """,
    )

    markup = page.book(Book(f"x{HOSTILE}", path, HOSTILE, HOSTILE))

    tags = Tags()
    tags.feed(markup)
    assert [attrs for tag, attrs in tags.seen if tag == "script"] == [
        [("type", "module"), ("src", "/static/rundot.js")]
    ]
    assert not [name for _, attrs in tags.seen for name, _ in attrs if name.startswith("on")]
    assert not [value for _, attrs in tags.seen for _, value in attrs if "javascript:" in (value or "")]
    assert 'data-book="x%22%3E%3Cscript%3E' in markup
    # Every place the text went, it arrived as text.
    assert markup.count("&lt;script&gt;alert(1)&lt;/script&gt;") >= 10


def test_nothing_a_library_gives_its_index_can_add_markup(tmp_path):
    books = [
        Book(f"a{HOSTILE}", tmp_path / "a.md", HOSTILE, HOSTILE),
        Book(f"b{HOSTILE}", tmp_path / "b.md", None, None, problem=HOSTILE),
    ]

    markup = page.library(books, tmp_path / HOSTILE, running=tmp_path / "a.md")
    tags = Tags()
    tags.feed(markup)

    assert [tag for tag, _ in tags.seen if tag in ("script", "img")] == ["script"]
    assert markup.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 7


# What ships


def test_the_built_wheel_has_every_static_file_and_template_with_the_licences(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path), "--quiet"], cwd=ROOT, check=True)
    [wheel] = tmp_path.glob("rundot-*.whl")

    shipped = set(zipfile.ZipFile(wheel).namelist())

    here = {
        str(path.relative_to(ROOT))
        for folder in ("static", "templates")
        for path in (WEB / folder).rglob("*")
        if path.is_file()
    }
    assert here <= shipped
    for needed in [
        "rundot/web/static/rundot.css",
        "rundot/web/static/rundot.js",
        "rundot/web/static/fonts/OFL.txt",
        "rundot/web/static/fonts/geist-latin-400-normal.woff2",
        "rundot/web/static/fonts/geist-mono-latin-400-normal.woff2",
        "rundot/web/static/fonts/instrument-serif-latin-400-italic.woff2",
        "rundot/web/static/pele/pele.js",
        "rundot/web/static/pele/LICENSE",
        "rundot/web/static/pele/VERSION",
        "rundot/web/templates/page.html",
    ]:
        assert needed in shipped


def test_the_fonts_are_latin_subsets_only_and_licensed():
    fonts = sorted(path.name for path in (WEB / "static" / "fonts").glob("*.woff2"))

    assert fonts and all("-latin-" in font for font in fonts)
    licence = (WEB / "static" / "fonts" / "OFL.txt").read_text()
    assert "SIL OPEN FONT LICENSE Version 1.1" in licence
    assert "The Geist Project Authors" in licence
    assert "The Instrument Serif Project Authors" in licence


def test_pele_has_its_licence_and_pinned_version():
    pele = WEB / "static" / "pele"

    assert re.fullmatch(r"\d+\.\d+\.\d+\n", (pele / "VERSION").read_text())
    assert "MIT License" in (pele / "LICENSE").read_text()


def test_every_font_the_stylesheet_uses_ships_with_it():
    css = (WEB / "static" / "rundot.css").read_text()

    used = re.findall(r"url\(\s*['\"]?([^'\")]+)", css)
    assert used
    for url in used:
        assert (WEB / "static" / url).is_file(), url


def test_no_url_in_the_stylesheet_script_or_templates_points_off_the_server():
    """So the page works offline. The footer's links are the only exception: they load nothing until clicked."""
    files = [WEB / "static" / "rundot.css", WEB / "static" / "rundot.js", *(WEB / "templates").glob("*.html")]
    links = ['<a href="https://docs.rundot.dev/"', '<a href="https://rundot.dev/"']

    for path in files:
        text = path.read_text()
        for link in links:
            text = text.replace(link, "<a")
        assert not re.findall(r"(?:[a-z][a-z0-9+.-]*:)?//[\w.-]+", text, flags=re.I), path.name
        for url in re.findall(r"(?:url\(|src=|href=|import\()\s*['\"]?([^'\")\s>]+)", text):
            assert url.startswith(("/", "./", "fonts/", "#", "${")) and not url.startswith("//"), (path.name, url)


def test_pele_names_no_url_but_xml_namespaces():
    pele = (WEB / "static" / "pele" / "pele.js").read_text()

    assert set(re.findall(r"[a-z]+://[^\s'\"`]+", pele)) == {
        "http://www.w3.org/2000/svg",
        "http://www.w3.org/1999/xhtml",
    }


# Diagrams


def test_a_mermaid_fence_reaches_the_page_as_code_for_the_script_to_draw():
    markup = book_page(RUNBOOKS / "mermaid.md")

    assert markup.count('<pre><code class="language-mermaid">') == 4
    assert "flowchart LR\n  A[Write the runbook] --&gt; B{Does it run?}" in markup


def test_no_page_loads_pele_itself():
    for markup in [book_page(RUNBOOKS / "mermaid.md"), all_features()]:
        assert "pele" not in markup


def test_the_script_loads_pele_only_when_the_page_has_a_mermaid_fence():
    script = (WEB / "static" / "rundot.js").read_text()

    [loaded] = re.findall(r".*import\(.*", script)
    assert 'import("./pele/pele.js")' in loaded
    assert "if (fences.length) diagrams(fences);" in script
    assert 'document.querySelectorAll("pre > code.language-mermaid")' in script


# Files next to the runbook


def test_relative_images_and_links_point_at_the_books_own_files(tmp_path):
    path = write(
        tmp_path / "my book.md",
        """\
        ![logo](images/logo.png) <img src="./a b.svg" width="64">

        [notes](notes.md) [top](#top) [root](/static/rundot.css) [site](https://rundot.dev/x.png) [mail](mailto:a@b.c)
        [query](?a=1&b=2) [empty]() [colon](a/b:c)
        """,
    )

    markup = book_page(path)

    assert '<img src="/book/my%20book/file/images/logo.png" alt="logo" />' in markup
    assert '<img src="/book/my%20book/file/./a b.svg" width="64" />' in markup
    assert '<a href="/book/my%20book/file/notes.md">notes</a>' in markup
    for kept in [
        'href="?a=1&amp;b=2"',
        'href="#top"',
        'href="/static/rundot.css"',
        'href="https://rundot.dev/x.png"',
        'href="mailto:a@b.c"',
    ]:
        assert kept in markup


def test_the_script_fills_in_every_block_from_the_overview_and_each_collected_input():
    """The page sets each block's text from the blocks the overview and input_collected carry, so every block
    shows its plain inputs filled in after Start, not only once the run reaches it. A browser run of
    all_features checks the rest by hand."""
    script = (WEB / "static" / "rundot.js").read_text()

    handlers = script[script.index("const handlers = {") : script.index("function at(")]
    overview = handlers[handlers.index("overview(e) {") : handlers.index("step_started(e)")]
    collected = handlers[handlers.index("input_collected(e) {") : handlers.index("block_started(e)")]
    assert "fill(e.blocks);" in overview
    assert "fill(e.blocks);" in collected
