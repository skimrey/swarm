"""codegen.gate_mislabeled_base64_data_uri.

Measured live: a component pushed real HTML content into a data URI
through `requests.utils.quote(...)` while the surrounding f-string
literal claimed `;base64,`. Percent-encoding is not base64 -- `%` is not
in the base64 alphabet -- so `base64.b64decode`/a browser's `atob()` fails
on it immediately, and the applet the project was pointed at afterward
showed "Loading applet..." forever, even though the real survey.html on
disk was correct the entire time. Nothing else in this pipeline decodes a
data URI's payload, so nothing else would ever have caught this.

Run directly: `python3 scratch_mislabeled_base64.py`.
"""
import codegen


LIVE_BUG = '''
import requests

def build(survey_html_content):
    version_string = "1.0.1"
    payload = {
        "version": version_string,
        "artifact_url": f"data:text/html;base64,{requests.utils.quote(survey_html_content, safe='')}",
    }
    return payload
'''

CORRECT_INLINE = '''
import base64

def build(html_content):
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("utf-8")
    return f"data:text/html;base64,{encoded}"
'''

CORRECT_DIRECT_CALL_INLINE = '''
import base64

def build(html_content):
    return f"data:text/html;base64,{base64.b64encode(html_content.encode('utf-8')).decode('utf-8')}"
'''

UNRELATED_BASE64_MENTION = '''
import base64

def note(x):
    # This function has nothing to do with data URIs at all.
    return base64.b64encode(x)
'''

UNKNOWN_SHAPE_NOT_FLAGGED = '''
def build(already_encoded):
    # `already_encoded` might be correctly built by a helper this scan
    # cannot see into -- must not be flagged on a guess.
    return f"data:text/html;base64,{already_encoded}"
'''

QUOTE_PLUS_VARIANT = '''
from urllib.parse import quote_plus

def build(html_content):
    return f"data:text/html;base64,{quote_plus(html_content)}"
'''


def test_live_bug_is_caught():
    msg = codegen.gate_mislabeled_base64_data_uri(LIVE_BUG)
    assert msg is not None, "the live percent-encoding-labeled-as-base64 bug was not caught"
    assert "requests.utils.quote" in msg, msg
    assert "base64.b64encode" in msg, msg
    print("  the live requests.utils.quote-mislabeled-as-base64 bug is caught")


def test_correct_base64_via_intermediate_variable_passes():
    assert codegen.gate_mislabeled_base64_data_uri(CORRECT_INLINE) is None
    print("  a real base64.b64encode(...).decode(...) via a variable passes clean")


def test_correct_base64_inline_call_passes():
    assert codegen.gate_mislabeled_base64_data_uri(CORRECT_DIRECT_CALL_INLINE) is None
    print("  a real base64.b64encode(...).decode(...) called inline passes clean")


def test_an_unrelated_base64_call_elsewhere_does_not_confuse_it():
    assert codegen.gate_mislabeled_base64_data_uri(UNRELATED_BASE64_MENTION) is None
    print("  a base64 call with no data URI anywhere near it is left alone")


def test_an_unresolvable_shape_is_not_flagged_on_a_guess():
    assert codegen.gate_mislabeled_base64_data_uri(UNKNOWN_SHAPE_NOT_FLAGGED) is None, (
        "a shape this scan genuinely cannot classify must not be flagged -- "
        "that would be exactly the guessing this project has refused to do "
        "everywhere else")
    print("  a shape this scan cannot resolve at all is left alone, not "
         "flagged on a guess")


def test_quote_plus_variant_is_also_caught():
    msg = codegen.gate_mislabeled_base64_data_uri(QUOTE_PLUS_VARIANT)
    assert msg is not None, "quote_plus is the same mistake as quote and must be caught too"
    assert "quote_plus" in msg, msg
    print("  the quote_plus variant of the same mistake is also caught")


def test_sabotage_no_existing_gate_would_have_caught_this():
    # Confirms this is genuinely new coverage: none of the existing
    # placeholder/host/output-json gates look at a data URI's payload at
    # all, so the live file passed every one of them before this gate
    # existed.
    assert codegen.gate_placeholder_id_literal(LIVE_BUG) is None
    assert codegen.gate_placeholder_host_literal(LIVE_BUG) is None
    print("  sabotage: confirmed no existing gate objects to this shape -- "
         "the live file passed all of them, which is exactly how it shipped")


def main():
    test_live_bug_is_caught()
    test_correct_base64_via_intermediate_variable_passes()
    test_correct_base64_inline_call_passes()
    test_an_unrelated_base64_call_elsewhere_does_not_confuse_it()
    test_an_unresolvable_shape_is_not_flagged_on_a_guess()
    test_quote_plus_variant_is_also_caught()
    test_sabotage_no_existing_gate_would_have_caught_this()
    print("OK")


if __name__ == "__main__":
    main()
