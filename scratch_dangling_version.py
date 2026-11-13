"""Validates `intent.dangling_version_reference` and
`intent.chain_dangling_version_problems`, added for the live failure where
`07_update_project`'s request read "...update the project with the new
version" -- properly wired to two *other* sibling outputs, so
`reads_sibling_output` said nothing was wrong -- while never reading the
version id `06_update_applet` had already created. The writer, with no way
to see that id, called the version-creating endpoint a second time with
placeholder content and pointed the project at the throwaway duplicate.
"""
import sys

import intent


LIVE_07_REQUEST = (
    "read project_id from ../01_create_project/output.json; "
    "read applet_id from ../05_create_applet/output.json; "
    "update the project with the new version")

LIVE_06_REQUEST = (
    "read applet_id from ../05_create_applet/output.json; "
    "read survey.html from ../04_create_survey/survey.html; "
    "update the applet with the html")

FIXED_07_REQUEST = (
    "read project_id from ../01_create_project/output.json; "
    "read applet_version_id from ../06_update_applet/output.json; "
    "update the project with that applet_version_id")


def test_flags_the_exact_live_request():
    assert intent.dangling_version_reference(LIVE_07_REQUEST) is True


def test_reads_sibling_output_alone_would_have_missed_it():
    # Sanity check that this really is the gap: the request DOES reference
    # other siblings, so the generic "any ../dir/output.json at all" check
    # says everything is fine.
    assert intent.reads_sibling_output(LIVE_07_REQUEST) is True


def test_not_flagged_once_the_request_names_the_real_key_and_path():
    assert intent.dangling_version_reference(FIXED_07_REQUEST) is False


def test_producing_step_is_not_flagged_for_creating_its_own_version():
    # "create a new version" -- this step IS the producer, not a consumer
    # assuming one already exists. Must not be told to add a read for
    # something it is itself about to create.
    text = "read applet_id from ../05_create_applet/output.json; create a new version of the applet with survey.html"
    assert intent.dangling_version_reference(text) is False


def test_unrelated_use_of_version_is_not_flagged():
    text = "create the applet's first version using survey.html"
    assert intent.dangling_version_reference(text) is False


def test_other_consuming_prepositions_are_caught_too():
    for phrasing in [
        "point the deployment to the latest version",
        "update the config using the current version",
        "publish the release via this version",
    ]:
        assert intent.dangling_version_reference(phrasing) is True, phrasing


def test_chain_problems_names_the_likely_producer_when_one_mentions_version():
    # The real live `06_update_applet` never says the word "version" at
    # all -- see the separate no-producer-guess test below for that exact
    # shape. This exercises the naming logic itself, on a request that
    # does mention it, the way an operator who reads this warning and
    # rewrites the producer's request to say "save the new version's id"
    # would produce.
    steps = [
        ("01_create_project", "create a project; save its id as project_id in a new output.json in the same directory as the script"),
        ("05_create_applet", "read project_id from ../01_create_project/output.json; create an applet; save its id as applet_id in a new output.json in the same directory as the script"),
        ("06_update_applet", LIVE_06_REQUEST + "; create a new version"),
        ("07_update_project", LIVE_07_REQUEST),
    ]
    problems = intent.chain_dangling_version_problems(steps)
    assert len(problems) == 1, problems
    assert "07_update_project" in problems[0]
    assert "06_update_applet" in problems[0]


def test_chain_problems_names_no_producer_on_the_exact_live_shape():
    # The real live shape: 06_update_applet's own request never says the
    # word "version" anywhere, even though its generated code correctly
    # created one (per the always-on output.json policy). Nothing here
    # can invent that fact from text that was never written, so this
    # must fall back to the no-producer-guess message rather than a
    # wrong or fabricated one.
    steps = [
        ("01_create_project", "create a project; save its id as project_id in a new output.json in the same directory as the script"),
        ("05_create_applet", "read project_id from ../01_create_project/output.json; create an applet; save its id as applet_id in a new output.json in the same directory as the script"),
        ("06_update_applet", LIVE_06_REQUEST),
        ("07_update_project", LIVE_07_REQUEST),
    ]
    problems = intent.chain_dangling_version_problems(steps)
    assert len(problems) == 1, problems
    assert "07_update_project" in problems[0]
    assert "no earlier step" in problems[0]


def test_chain_problems_silent_once_fixed():
    steps = [
        ("01_create_project", "create a project; save its id as project_id in a new output.json in the same directory as the script"),
        ("05_create_applet", "read project_id from ../01_create_project/output.json; create an applet; save its id as applet_id in a new output.json in the same directory as the script"),
        ("06_update_applet", LIVE_06_REQUEST),
        ("07_update_project", FIXED_07_REQUEST),
    ]
    assert intent.chain_dangling_version_problems(steps) == []


def test_chain_problems_reports_with_no_producer_guess_when_none_exists():
    steps = [
        ("only_step", LIVE_07_REQUEST),
    ]
    problems = intent.chain_dangling_version_problems(steps)
    assert len(problems) == 1, problems
    assert "no earlier step" in problems[0]


LIVE_CREATE_AND_POINT_REQUEST = (
    "push survey.html (at ../04_create_survey/survey.html) as a new "
    "version of the applet whose applet_id is read from "
    "../05_create_applet/output.json, with a version string guaranteed "
    "to be different from any that already exist (a uuid, not a fixed "
    "literal like '1.0.0'), and point the project whose project_id is "
    "read from ../01_create_project/output.json at that new version; "
    "save the new version's id as applet_version_id in a new "
    "output.json in the same directory as the script")


def test_version_string_descriptor_is_not_a_dangling_reference():
    # Live failure: this step creates AND points at the version in one
    # request -- exactly the merged shape LOCAL.md recommends -- but
    # "with a version string guaranteed to be different ..." matched
    # `_DANGLING_VERSION_RE`'s "with a [...] version" on the word
    # "version" inside the compound noun "version string" (an attribute
    # of the version this step is about to create), not a reference to
    # one made elsewhere. No earlier step even mentions "version", so
    # this had no producer to name and escalated straight to a real
    # network repair call for a problem that did not exist, hanging the
    # whole chain on step one before anything else ran.
    assert intent.dangling_version_reference(
        LIVE_CREATE_AND_POINT_REQUEST) is False


def test_version_string_sabotage_reproduces_the_live_hang_trigger():
    # Confirms the fix is load-bearing: without the trailing negative
    # lookahead, the old regex matches "with a version" from the
    # "version string" clause and this request reads as dangling.
    import re
    old_re = re.compile(
        r'\b(?:with|using|via|to)\s+(?:the|a|this|that|its)\s+'
        r'(?:(?:new|newest|latest|updated|current)\s+)?version\b',
        re.IGNORECASE)
    assert old_re.search(LIVE_CREATE_AND_POINT_REQUEST) is not None


def test_other_version_descriptor_nouns_are_also_excluded():
    for word in ("number", "identifier", "label", "name", "tag", "id"):
        text = "tag the release with a version %s for later lookup" % word
        assert intent.dangling_version_reference(text) is False, word


def test_genuinely_dangling_phrase_with_indefinite_article_still_caught():
    # The lookahead must only exclude "version <descriptor-noun>", not
    # weaken the check generally -- "with a new version" with nothing
    # else following is still exactly the dangling shape.
    text = "update the deployment with a new version"
    assert intent.dangling_version_reference(text) is True


def test_sabotage_generic_sibling_check_alone_reports_clean():
    # Reproduces the exact live blind spot: chain_output_json_problems
    # (the existing, unrelated check) finds nothing wrong with this chain,
    # proving the dangling-version gap was real and not already covered.
    steps = [
        ("01_create_project", "create a project; save its id as project_id in a new output.json in the same directory as the script"),
        ("05_create_applet", "read project_id from ../01_create_project/output.json; create an applet; save its id as applet_id in a new output.json in the same directory as the script"),
        ("06_update_applet", LIVE_06_REQUEST),
        ("07_update_project", LIVE_07_REQUEST),
    ]
    assert intent.chain_output_json_problems(steps) == []


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("ok  ", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "--", e)
    if failed:
        print("%d/%d failed" % (failed, len(tests)))
        sys.exit(1)
    print("%d/%d passed" % (len(tests), len(tests)))


if __name__ == "__main__":
    main()
