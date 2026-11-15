"""Tests for the HTML sanitiser that rundot web runs over rendered runbooks."""

import re
from pathlib import Path

import pytest

from rundot import runbook
from rundot.web.sanitise import sanitise

RUNBOOKS = Path(__file__).parent.parent / "runbooks"
# broken-body.md isn't valid UTF-8, so it can't be rendered.
RENDERABLE = sorted(path for path in RUNBOOKS.rglob("*.md") if path.name != "broken-body.md")
HOOK = re.compile(r'<div class="rd-block" data-kind="\w+" data-line="\d+"(?: data-lang="[^"]*")?>')
STEP = re.compile(r'<h2 data-step="\d+">')


def test_plain_text_is_kept():
    """Text with nothing to remove comes back as it went in."""
    assert sanitise("Hello, world") == "Hello, world"


def test_text_is_re_escaped():
    """Escaped markup in text stays text, so &lt;script&gt; in a paragraph can't become a tag."""
    assert sanitise("&lt;script&gt;alert(1)&lt;/script&gt; &amp; done") == (
        "&lt;script&gt;alert(1)&lt;/script&gt; &amp; done"
    )


def test_tags_markdown_it_emits_survive_unchanged():
    """Everything markdown-it renders a runbook into comes through as it was."""
    markup = (
        "<h1>Title</h1>\n<p>Some <em>prose</em>, <strong>bold</strong> and <code>code</code>.</p>\n"
        "<blockquote>\n<p>Quote</p>\n</blockquote>\n<hr />\n"
        "<ul>\n<li>one</li>\n</ul>\n<ol>\n<li>first</li>\n</ol>\n"
        "<pre><code>echo hi\n</code></pre>\n<p>a<br />\nb</p>\n"
        "<table>\n<thead>\n<tr>\n<th>Key</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td>a</td>\n</tr>\n</tbody>\n</table>\n"
        "<h2>h2</h2><h3>h3</h3><h4>h4</h4><h5>h5</h5><h6>h6</h6>"
    )

    assert sanitise(markup) == markup


def test_github_extras_survive_unchanged():
    """The raw HTML GitHub allows in a README is allowed here too."""
    markup = (
        "<details><summary>More</summary><p>Hidden</p></details>"
        "<p>Press <kbd>Ctrl</kbd>-<kbd>C</kbd>. H<sub>2</sub>O, x<sup>2</sup>.</p>"
        "<dl><dt>Term</dt><dd>Meaning</dd></dl><p><s>old</s> <del>gone</del> <ins>new</ins> <b>b</b> <i>i</i></p>"
    )

    assert sanitise(markup) == markup


def test_unknown_tags_are_dropped_but_their_text_kept():
    """A tag that isn't allowed goes, but what it says stays, as on GitHub."""
    assert sanitise("<p><marquee>Hi <blink>there</blink></marquee></p>") == "<p>Hi there</p>"


def test_unclosed_tags_are_closed_at_the_end():
    """Nothing allowed is left open to swallow the rest of the page."""
    assert sanitise("<details><summary>More<p>Hidden") == "<details><summary>More<p>Hidden</p></summary></details>"


def test_an_end_tag_closes_the_tags_left_open_inside_it():
    """Output is always balanced, whatever order the input closes things in."""
    assert sanitise("<p><em>a<strong>b</p>c") == "<p><em>a<strong>b</strong></em></p>c"


def test_stray_end_tags_are_dropped():
    """An end tag with nothing open to close, or of a tag that isn't allowed, is left out."""
    assert sanitise("</div>a</marquee><p>b</em></p>") == "a<p>b</p>"


def test_void_tags_are_written_as_markdown_it_writes_them():
    """br, hr, img and wbr have no end tag, however they're written."""
    assert sanitise("a<br>b<br/>c</br><hr><wbr>") == "a<br />b<br />c<hr /><wbr />"


@pytest.mark.parametrize("tag", ["script", "style", "iframe", "object", "form", "svg", "math", "template", "textarea"])
def test_dangerous_tags_are_removed_with_their_content(tag):
    """Their content goes too, so no script, style or nested markup is left behind."""
    assert sanitise(f"<p>a</p><{tag}><p>inside</p>alert(1)</{tag}><p>b</p>") == "<p>a</p><p>b</p>"


def test_dropped_tags_nest():
    """Content stays dropped until the outermost dropped tag closes."""
    assert sanitise("<form><form>x</form>still inside</form>after") == "after"


def test_an_unclosed_dropped_tag_drops_everything_after_it():
    """A script that never closes can't leak its content as text."""
    assert sanitise("<p>a</p><script>alert(1)<p>b</p>") == "<p>a</p>"


def test_svg_with_a_script_inside_produces_no_script():
    """The classic <svg><script> vector is dropped as a whole."""
    assert sanitise("<svg><script>alert(1)</script></svg>ok") == "ok"


def test_allowed_attributes_survive_unchanged():
    """Links, images, align, ol start and details open keep the attributes GitHub keeps."""
    markup = (
        '<p><a href="https://rundot.dev" title="Site">docs</a> '
        '<img src="logo.png" alt="logo" title="Logo" width="20" height="10" /></p>'
        '<div align="center"><p align="right">x</p></div><ol start="3"><li>c</li></ol>'
        "<details open><summary>s</summary></details>"
        '<pre><code class="language-json">{}\n</code></pre>'
    )

    assert sanitise(markup) == markup


def test_attribute_values_are_re_escaped():
    """A value can't break out of its quotes, however it was written."""
    assert sanitise('<a title=\'a"><script>x\' href="&lt;&amp;">l</a>') == (
        '<a title="a&quot;&gt;&lt;script&gt;x" href="&lt;&amp;">l</a>'
    )


@pytest.mark.parametrize(
    "markup",
    [
        '<p onclick="alert(1)">x</p>',
        '<p ONMOUSEOVER="alert(1)">x</p>',
        '<p style="background:url(javascript:alert(1))">x</p>',
        '<p id="x" class="rd-block" data-kind="run" name="y" align>x</p>',
    ],
)
def test_other_attributes_are_dropped(markup):
    """on* handlers, style, and anything not on the allowlist for that tag go."""
    assert sanitise(markup) == "<p>x</p>"


def test_img_src_x_onerror_produces_no_handler():
    """The classic <img src=x onerror=…> keeps its harmless src and loses its handler."""
    assert sanitise("<img src=x onerror=alert(1)>") == '<img src="x" />'


def test_a_repeated_attribute_keeps_the_first_as_browsers_do():
    """A second href can't replace a checked first one."""
    assert sanitise('<a href="/safe" href="https://x">l</a>') == '<a href="/safe">l</a>'


def test_code_keeps_only_a_language_class():
    """markdown-it's language-* class stays, so a mermaid fence can be drawn; other classes go."""
    assert sanitise('<pre><code class="language-mermaid">graph TD\n</code></pre>') == (
        '<pre><code class="language-mermaid">graph TD\n</code></pre>'
    )
    assert sanitise('<code class="rd-block">x</code><code class="language-a b">y</code>') == (
        "<code>x</code><code>y</code>"
    )


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_table_alignment_becomes_align(align):
    """markdown-it aligns table cells with style; that becomes align, as GitHub writes it."""
    assert sanitise(f'<th style="text-align:{align}">a</th><td style="text-align:{align}">1</td>') == (
        f'<th align="{align}">a</th><td align="{align}">1</td>'
    )


def test_other_table_styles_are_dropped():
    """Only markdown-it's exact alignment styles are turned into align."""
    assert sanitise('<td style="text-align:right; color:red">1</td><td align="left">2</td>') == (
        '<td>1</td><td align="left">2</td>'
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://rundot.dev/x?y=1#z",
        "http://example.com",
        "mailto:paul@example.com",
        "HTTPS://rundot.dev",
        "docs/setup.md",
        "./logo.png",
        "../up.png",
        "/abs/path",
        "#section",
        "?q=1",
        "dir/a:b.png",
        "",
    ],
)
def test_safe_urls_are_kept(url):
    """http, https, mailto and relative URLs stay."""
    assert sanitise(f'<a href="{url}">l</a><img src="{url}" />') == f'<a href="{url}">l</a><img src="{url}" />'


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " javascript:alert(1)",
        "\x01javascript:alert(1)",
        "java\tscript:alert(1)",
        "java&#x09;script:alert(1)",
        "java&Tab;script:alert(1)",
        "&#106;&#97;&#118;&#97;&#115;&#99;&#114;&#105;&#112;&#116;:alert(1)",
        "&#x6A;avascript:alert(1)",
        "javascript&colon;alert(1)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "DATA:image/svg+xml,<svg onload=alert(1)>",
        "vbscript:msgbox(1)",
        "VbScRiPt:msgbox(1)",
        "file:///etc/passwd",
        "ftp://example.com",
    ],
)
def test_other_url_schemes_are_dropped(url):
    """Any scheme but http, https and mailto goes, however it's cased, spaced or entity-encoded."""
    assert sanitise(f'<a href="{url}">l</a><img src="{url}" alt="a" />') == '<a>l</a><img alt="a" />'


def test_rd_block_hooks_survive_unchanged():
    """render_html's block hooks keep their class and data attributes, for the page to fill in."""
    markup = (
        '<h2 data-step="1">Step</h2>\n'
        '<div class="rd-block" data-kind="run" data-line="7" data-lang="sh"><pre><code>echo hi\n</code></pre></div>\n'
        '<div class="rd-block" data-kind="confirm" data-line="11"><pre><code>Done?\n</code></pre></div>\n'
    )

    assert sanitise(markup) == markup


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<div class="rd-block x" data-kind="run">a</div>', "<div>a</div>"),
        ('<div class="other" data-kind="run" data-line="1">a</div>', "<div>a</div>"),
        (
            '<div class="rd-block" data-other="1" data-kind="run">a</div>',
            '<div class="rd-block" data-kind="run">a</div>',
        ),
        ('<p class="rd-block" data-kind="run">a</p>', "<p>a</p>"),
        ('<h3 data-step="1">a</h3><p data-step="1">b</p>', "<h3>a</h3><p>b</p>"),
    ],
)
def test_hook_attributes_are_kept_only_on_hooks(markup, expected):
    """class and data-* are only for div.rd-block and step headings; elsewhere they go."""
    assert sanitise(markup) == expected


@pytest.mark.parametrize("path", RENDERABLE, ids=lambda path: str(path.relative_to(RUNBOOKS)))
def test_every_fixture_keeps_its_hooks_and_step_numbers(path):
    """Sanitising render_html's output leaves every rd-block hook and data-step as it was."""
    rendered = runbook.render_html(path.read_text(encoding="utf-8"))

    sanitised = sanitise(rendered)

    assert HOOK.findall(sanitised) == HOOK.findall(rendered)
    assert STEP.findall(sanitised) == STEP.findall(rendered)
    assert sanitise(sanitised) == sanitised


def test_the_fixtures_have_hooks_to_check():
    """The fixture test means something: render_html finds hooks and steps in all_features.md."""
    rendered = runbook.render_html((RUNBOOKS / "all_features.md").read_text(encoding="utf-8"))

    assert HOOK.search(rendered) and STEP.search(rendered)


def test_comments_doctypes_and_processing_instructions_are_dropped():
    """None of them is shown, and a conditional comment can't smuggle markup in."""
    markup = (
        "<!DOCTYPE html><?php echo 1 ?><!-- hidden --><!--[if IE]><script>alert(1)</script><![endif]-->a<![CDATA[b]]>"
    )

    assert sanitise(markup) == "a"


VECTORS = [
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//x.example/x.js></SCRIPT>",
    "<ScRiPt>alert(1)</sCrIpT>",
    "<scr<script>ipt>alert(1)</script>",
    "<<script>alert(1)//<</script>",
    "<svg><script>alert(1)</script></svg>",
    "<svg onload=alert(1)>",
    "<svg><a xlink:href=javascript:alert(1)><text>x</text></a></svg>",
    "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
    "<img src=x onerror=alert(1)>",
    "<img src=x onerror=alert(1)//",
    '<img src="x" onerror="alert(1)"',
    "<img src=javascript:alert(1)>",
    "<IMG SRC=JaVaScRiPt:alert(1)>",
    "<img/src=x/onerror=alert(1)>",
    '<img """><script>alert(1)</script>">',
    "<body onload=alert(1)>",
    "<a href=javascript:alert(1)>x</a>",
    '<a href="jav&#x09;ascript:alert(1)">x</a>',
    '<a href="&#0000106&#0000097&#0000118&#0000097&#0000115&#0000099&#0000114'
    '&#0000105&#0000112&#0000116&#0000058alert(1)">x</a>',
    '<a href="  &#14;  javascript:alert(1)">x</a>',
    '<a href="data:text/html,<script>alert(1)</script>">x</a>',
    "<iframe src=javascript:alert(1)></iframe>",
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    "<object data=javascript:alert(1)></object>",
    "<embed src=javascript:alert(1)>",
    "<form action=javascript:alert(1)><button>x</button></form>",
    "<input onfocus=alert(1) autofocus>",
    "<details open ontoggle=alert(1)>",
    "<div style=\"background:url('javascript:alert(1)')\">x</div>",
    "<style>@import 'javascript:alert(1)';</style>",
    "<link rel=stylesheet href=javascript:alert(1)>",
    "<meta http-equiv=refresh content='0;url=javascript:alert(1)'>",
    "<base href=javascript:alert(1)//>",
    "<p>unclosed <a href='x' onclick='alert(1)",
    "<p title='</p><script>alert(1)</script>'>x</p>",
    "<!--><script>alert(1)</script>-->",
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>',
    "<textarea></textarea><script>alert(1)</script>",
    "<title><script>alert(1)</script></title>",
    "<template><script>alert(1)</script></template>",
]


# Every tag the sanitiser writes: a lowercase name, then attributes with quoted, escaped values.
WRITTEN_TAG = re.compile(r'</?([a-z0-9]+)(?: [a-z-]+(?:="[^"<>]*")?)*(?: /)?>')
SCRIPTABLE = {"script", "svg", "math", "iframe", "object", "embed", "form", "style", "link", "meta", "base", "input"}


@pytest.mark.parametrize("markup", VECTORS)
def test_xss_vectors_cant_produce_a_script(markup):
    """Every < left is a tag the sanitiser wrote, none can run script, and no attribute is a handler or javascript:."""
    sanitised = sanitise(markup)

    assert "<" not in WRITTEN_TAG.sub("", sanitised), sanitised
    assert not SCRIPTABLE & set(WRITTEN_TAG.findall(sanitised)), sanitised
    assert not re.search(r"\son\w+=|style=|javascript:|data:", sanitised, re.IGNORECASE), sanitised
