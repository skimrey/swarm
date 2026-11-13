"""Validates `intent.unqualified_sibling_asset_refs`, the check that
catches a request naming a produced-artifact filename (`survey.html`)
with no path at all -- the shape that let a generated `main.py` resolve
it against its own directory forever, correctly by its own logic and
wrong every single time, because nothing in the request said the file
came from a different step.

This is deliberately not the "scan sibling directories for the file"
heuristic that was proposed and rejected for this exact bug: that
heuristic answers "has the sibling step run yet", which is the wrong
question here -- the generated code was never going to find the real
file regardless of timing, because it never had a real path to look at.
The right check is entirely on the request side: is there a bare asset
name with no `../` in front of it, that also is not sitting in this
project's own directory right now.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intent


def test_bare_asset_not_in_root_is_flagged():
    with tempfile.TemporaryDirectory() as root:
        request = ("push survey.html as a new applet version, then point "
                    "the project at it")
        found = intent.unqualified_sibling_asset_refs(request, root)
        assert found == ["survey.html"], found


def test_already_qualified_path_is_not_flagged():
    with tempfile.TemporaryDirectory() as root:
        request = ("read the html from ../04_create_survey/survey.html "
                    "and push it as a new applet version")
        found = intent.unqualified_sibling_asset_refs(request, root)
        assert found == [], (
            "a path already carrying its own ../step qualifier is exactly "
            "the shape output.json references already get right -- "
            "flagging it too would be a warning that fires on correct "
            "requests, got %r" % (found,))


def test_file_present_in_root_is_not_flagged():
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "survey.html"), "w") as fh:
            fh.write("<html></html>")
        request = "push survey.html as a new applet version"
        found = intent.unqualified_sibling_asset_refs(request, root)
        assert found == [], (
            "the file genuinely exists right here -- nothing to warn "
            "about, got %r" % (found,))


def test_py_files_are_never_flagged():
    """A request naming `client.py` is naming a component of *this*
    plan (see `wants_multi_file`), not an artifact some earlier,
    unrelated step produced -- `.py` is deliberately excluded from the
    extension list."""
    with tempfile.TemporaryDirectory() as root:
        request = "write client.py and main.py that call the API"
        found = intent.unqualified_sibling_asset_refs(request, root)
        assert found == [], found


def test_output_json_reference_is_not_flagged():
    """The exact shape this project's existing sibling-chain machinery
    already handles correctly -- a `.json` reference with its own
    `../step/` qualifier -- must not also trip this newer, narrower
    check, or every ordinary chained request would get a spurious
    warning."""
    with tempfile.TemporaryDirectory() as root:
        request = ("save the applet id to ../01_create_project/"
                    "output.json under the same key")
        found = intent.unqualified_sibling_asset_refs(request, root)
        assert found == [], found


def test_bare_own_output_json_is_not_flagged():
    """Live false positive: a step's own "save its id to output.json"
    promise -- the exact recommended phrasing this project's own docs
    tell every producing step to use -- is a bare `output.json` with no
    `../` qualifier and (correctly) does not exist in this directory
    yet, since this step is the one about to create it. That is not
    the bug this check exists for (a produced-artifact reference with
    nowhere to resolve against); it is this step's own file, exactly
    the convention `_OUTPUT_JSON_WRITE_RE` already encodes elsewhere.
    Before the fix, this fired on nearly every step of every real
    chain -- constant noise, not an occasional warning."""
    with tempfile.TemporaryDirectory() as root:
        request = ("create a project; save its id as project_id in a "
                   "new output.json in the same directory as the script")
        found = intent.unqualified_sibling_asset_refs(request, root)
        assert found == [], found


def test_bare_own_output_json_alongside_a_real_bare_asset():
    """The exclusion must be narrow: a genuinely bare, unrelated asset
    reference sitting in the same request as the own-output.json
    promise must still be caught."""
    with tempfile.TemporaryDirectory() as root:
        request = ("push survey.html as a new version; save its id as "
                   "applet_version_id in a new output.json in the same "
                   "directory as the script")
        found = intent.unqualified_sibling_asset_refs(request, root)
        assert found == ["survey.html"], found


def test_sabotage_generic_json_extension_alone_reproduces_the_false_positive():
    """Confirms the fix is load-bearing: without the output.json
    exclusion, the plain extension-list check (`_ASSET_EXT_RE` already
    includes `json`) flags this step's own promise."""
    request = ("create a project; save its id as project_id in a new "
               "output.json in the same directory as the script")
    found = []
    for match in intent._ASSET_EXT_RE.finditer(request):
        name = match.group(1)
        if name not in found:
            found.append(name)
    assert found == ["output.json"], (
        "sabotage fixture should reproduce the false positive, got %r"
        % (found,))


def test_multiple_bare_assets_all_reported_once():
    with tempfile.TemporaryDirectory() as root:
        request = ("combine survey.html and survey.html and styles.css "
                    "into one page")
        found = intent.unqualified_sibling_asset_refs(request, root)
        assert found == ["survey.html", "styles.css"], found


def test_no_root_reports_every_bare_asset():
    """`root=None` -- no project directory to check against yet -- must
    not silently suppress every finding; the safe direction for a
    warning that costs nothing if wrong is to report, not to stay quiet."""
    request = "push survey.html as a new applet version"
    found = intent.unqualified_sibling_asset_refs(request, None)
    assert found == ["survey.html"], found


def test_sabotage_dropping_the_qualifier_check_flags_correct_requests():
    """Proves the qualifier lookbehind is load-bearing: without it, a
    request that already does the right thing (`../step/file.ext`) would
    be flagged anyway, which is exactly the "detector fires on correct
    input" failure this project has decided, repeatedly, costs more than
    the detector saves."""
    import re
    with tempfile.TemporaryDirectory() as root:
        request = ("read the html from ../04_create_survey/survey.html "
                    "and push it as a new applet version")

        def sabotaged(req, root_):
            found = []
            for match in intent._ASSET_EXT_RE.finditer(req or ""):
                name = match.group(1)
                if root_ is not None and os.path.isfile(
                        os.path.join(root_, name)):
                    continue
                if name not in found:
                    found.append(name)
            return found

        sabotaged_result = sabotaged(request, root)
        assert sabotaged_result == ["survey.html"], (
            "sabotage fixture should reproduce the false positive, got %r"
            % (sabotaged_result,))
        real_result = intent.unqualified_sibling_asset_refs(request, root)
        assert real_result == [], (
            "the real function must not flag an already-qualified "
            "reference -- got %r" % (real_result,))


def run():
    test_bare_asset_not_in_root_is_flagged()
    test_already_qualified_path_is_not_flagged()
    test_file_present_in_root_is_not_flagged()
    test_py_files_are_never_flagged()
    test_output_json_reference_is_not_flagged()
    test_bare_own_output_json_is_not_flagged()
    test_bare_own_output_json_alongside_a_real_bare_asset()
    test_sabotage_generic_json_extension_alone_reproduces_the_false_positive()
    test_multiple_bare_assets_all_reported_once()
    test_no_root_reports_every_bare_asset()
    test_sabotage_dropping_the_qualifier_check_flags_correct_requests()
    print("scratch_unqualified_asset: all checks passed")


if __name__ == "__main__":
    run()
