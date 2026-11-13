"""A page that renders to nothing is not caught by nesting or emptiness.

`gate_html` checks that tags nest correctly, and `gate_syntax`'s emptiness
check only asks for *some* non-whitespace text. An empty document nests
perfectly, and a newline inside a tag pair is technically non-empty, so both
pass a document that renders blank in a browser.

Measured live: a generated survey page's entire content was
`<pre><code>\\n</code></pre>\\n` -- two tags, correctly nested, containing
nothing -- and it cleared every existing gate. `main.py` faithfully
base64-encoded and pushed it; there was simply nothing in it to push. This is
the same class of bug as a generated `reverse()` that returns its input
unchanged: a detector that only sees shape cannot see that the shape is
empty.

Validated by sabotage: disabling the check reproduces exactly this failure
passing clean.
"""
import codegen

# The exact bytes recovered from the live failure.
LIVE_BLANK_PAGE = "<pre><code>\n</code></pre>\n"


def test_catches_the_live_failure() -> None:
    assert codegen.gate_html_placeholder(LIVE_BLANK_PAGE) is not None
    assert codegen.gate_syntax(LIVE_BLANK_PAGE, "survey.html") is not None
    print("catches the exact blank page recovered from the live failure")


def test_other_empty_shapes() -> None:
    for blank in ("", "\n", "   ", "<div></div>", "<br><hr>",
                 "<html><head></head><body></body></html>"):
        assert codegen.gate_html_placeholder(blank) is not None, blank
    print("catches other empty shapes: bare whitespace, empty containers, "
         "an empty document skeleton")


def test_real_pages_pass() -> None:
    cases = [
        # Real visible text.
        "<html><body><h1>Survey</h1><p>Answer the following.</p></body></html>",
        # An interactive form with no other text.
        "<form><input name=\"age\"><button>Submit</button></form>",
        # A JS-rendered app whose only static content is a mount point.
        ("<html><body><div id=\"app\"></div>"
         "<script>document.getElementById('app').textContent='hi'</script>"
         "</body></html>"),
        # A single image.
        "<img src=\"chart.png\" alt=\"chart\">",
        # A table with no other prose.
        "<table><tr><td>1</td></tr></table>",
    ]
    for page in cases:
        assert codegen.gate_html_placeholder(page) is None, page
        assert codegen.gate_syntax(page, "page.html") is None, page
    print("does not fire on real pages: prose, a bare form, a JS mount "
         "point, an image, a table")


def test_templates_untouched() -> None:
    # `{{ content }}` alone has no static text and no substance tags, but a
    # templating engine will fill it -- the check cannot see that, so it is
    # skipped for anything carrying template syntax, same as the inline
    # script check right below it.
    assert codegen.gate_syntax("{{ content }}", "page.html") is None
    assert codegen.gate_syntax("{% for x in y %}{% endfor %}",
                               "page.html") is None
    print("skipped for templated content, which the check cannot evaluate")


def test_sabotage_without_the_gate() -> None:
    # Prove the check is actually load-bearing: without it, the exact live
    # failure passes `gate_syntax` clean.
    real = codegen.gate_html_placeholder
    codegen.gate_html_placeholder = lambda code: None
    try:
        assert codegen.gate_syntax(LIVE_BLANK_PAGE, "survey.html") is None, (
            "sabotage should have reproduced the blank page passing clean")
    finally:
        codegen.gate_html_placeholder = real
    print("sabotage confirms: without this gate, the live failure ships")


if __name__ == "__main__":
    test_catches_the_live_failure()
    test_other_empty_shapes()
    test_real_pages_pass()
    test_templates_untouched()
    test_sabotage_without_the_gate()
    print("OK")
