#!/usr/bin/env python3
"""Validates `intent.chain_dangling_directory_reference_problems` against
the exact live failure that motivated it: a chain step naming a sibling
step's directory literally ("update the applet created in
05_create_applet") with no ../05_create_applet/... reference anywhere in
the same request. Neither `chain_output_json_problems` (needs the literal
../dir/output.json substring) nor `chain_dangling_version_problems`
(keyed to the word "version") sees this shape at all, and the real chain
generated cleanly on both sides before failing at runtime three rungs
into a repair ladder, when a writer facing a real, unwired dependency
invented a `project_id` read from a bare local output.json that could
never exist during generation.
"""

import intent

LIVE_CHAIN = [
    ("01_create_project",
     "create a project; save its id as project_id in a new output.json "
     "in the same directory as the script"),
    ("04_create_survey",
     "create a survey.html that shows 5 5-point rating axes"),
    ("05_create_applet",
     "create an applet; read the html for the applet's content from "
     "../04_create_survey/survey.html"),
    ("06_update_applet",
     "read survey.html from ../04_create_survey/survey.html; update the "
     "applet created in 05_create_applet with this html; save the new "
     "applet version id as applet_version_id in a new output.json in "
     "the same directory as the script"),
]

# The fixed version of the same chain: 06 now names the real reference
# explicitly instead of the bare directory mention.
FIXED_CHAIN = [
    ("01_create_project",
     "create a project; save its id as project_id in a new output.json "
     "in the same directory as the script"),
    ("04_create_survey",
     "create a survey.html that shows 5 5-point rating axes"),
    ("05_create_applet",
     "create an applet; save its id as applet_id in a new output.json "
     "in the same directory as the script; read the html for the "
     "applet's content from ../04_create_survey/survey.html"),
    ("06_update_applet",
     "read applet_id from ../05_create_applet/output.json; read "
     "survey.html from ../04_create_survey/survey.html; update that "
     "applet with this html; save the new applet version id as "
     "applet_version_id in a new output.json in the same directory as "
     "the script"),
]

# A directory mentioned in prose, but the same request also genuinely
# wires the dependency elsewhere (e.g. to the produced asset, not
# output.json) -- must NOT be flagged, since the dependency is real.
ALREADY_WIRED_VIA_ASSET = [
    ("04_create_survey",
     "create a survey.html that shows 5 5-point rating axes"),
    ("05_create_applet",
     "create an applet; read the html file produced by 04_create_survey "
     "from ../04_create_survey/survey.html"),
]

# A directory mentioned in prose, with a real ../dir/output.json read
# elsewhere in the same request -- must NOT be flagged.
ALREADY_WIRED_VIA_OUTPUT_JSON = [
    ("01_create_project",
     "create a project; save its id as project_id in a new output.json "
     "in the same directory as the script"),
    ("02_create_task",
     "read project_id from ../01_create_project/output.json; the "
     "project made in 01_create_project needs a task now; create one"),
]

NO_CROSS_REFERENCES_AT_ALL = [
    ("01_create_project", "create a project"),
    ("02_do_something_else", "do something unrelated"),
]


def test_live_bug_is_caught():
    problems = intent.chain_dangling_directory_reference_problems(
        LIVE_CHAIN)
    assert len(problems) == 1, problems
    assert "06_update_applet" in problems[0]
    assert "05_create_applet" in problems[0]


def test_fixed_chain_is_clean():
    assert intent.chain_dangling_directory_reference_problems(
        FIXED_CHAIN) == []


def test_bare_mention_alongside_a_real_asset_reference_is_not_flagged():
    assert intent.chain_dangling_directory_reference_problems(
        ALREADY_WIRED_VIA_ASSET) == []


def test_bare_mention_alongside_a_real_output_json_reference_is_not_flagged():
    assert intent.chain_dangling_directory_reference_problems(
        ALREADY_WIRED_VIA_OUTPUT_JSON) == []


def test_a_chain_with_no_cross_references_at_all_is_clean():
    assert intent.chain_dangling_directory_reference_problems(
        NO_CROSS_REFERENCES_AT_ALL) == []


def test_neither_existing_check_sees_this_shape():
    # Confirms this is a genuinely new failure mode, not a duplicate of
    # an existing gate -- chain_output_json_problems needs the literal
    # ../dir/output.json substring, and chain_dangling_version_problems
    # is keyed to the word "version", neither of which the live chain's
    # 06_update_applet request contains for this dependency.
    assert intent.chain_output_json_problems(LIVE_CHAIN) == []
    assert intent.chain_dangling_version_problems(LIVE_CHAIN) == []


def test_sabotage_without_the_check_the_live_chain_looks_consistent():
    # No call to chain_dangling_directory_reference_problems at all --
    # exactly what team.cmd_lint_chain did before this check existed:
    # every other check passes, so the chain reports clean and the real
    # failure only surfaces at runtime, several rungs into a repair
    # ladder, as an invented project_id read from a bare output.json.
    all_clean = (not intent.chain_output_json_problems(LIVE_CHAIN)
                and not intent.chain_dangling_version_problems(LIVE_CHAIN)
                and not intent.chain_missing_content_source_problems(
                    LIVE_CHAIN))
    assert all_clean, (
        "fixture must reproduce the live shape: every existing check "
        "stays silent, which is exactly why the new check is needed")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok:", t.__name__)
    print("%d/%d passed" % (len(tests), len(tests)))
