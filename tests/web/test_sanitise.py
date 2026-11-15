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
