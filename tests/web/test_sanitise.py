"""Tests for the HTML sanitiser that rundot web runs over rendered runbooks."""

from rundot.web.sanitise import sanitise


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
